from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from pydantic import ValidationError
from sqlalchemy import select

from app.admin.auth import (
    AdminPrincipal,
    csrf_token,
    encrypt_token,
    sign_session_id,
)
from app.admin.oauth import safe_admin_redirect
from app.config import Settings, get_settings
from app.jobs.models import (
    AdminAuditLog,
    AdminSession,
    GlobalReviewSettings,
    JobStatus,
    RepositorySettings,
    ReviewJob,
    ReviewRun,
    TriggerType,
)
from app.main import app


def oauth_settings() -> Settings:
    return Settings(
        environment="test",
        public_base_url="http://test",
        admin_github_client_id="client-id",
        admin_github_client_secret="client-secret",
        admin_session_secret="s" * 32,
        superadmin_github_logins="admin-user",
        allowed_github_accounts="acme",
    )


async def authenticated_repository(app_client):
    client, factory = app_client
    settings = oauth_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    principal = AdminPrincipal("session-id", 42, "admin-user", "user-token")
    async with factory() as session:
        record = AdminSession(
            id=principal.session_id,
            github_user_id=42,
            github_login="admin-user",
            encrypted_access_token=encrypt_token("user-token", settings),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        repository = RepositorySettings(
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
        )
        session.add_all([record, repository])
        await session.commit()
        repository_id = repository.id
    client.cookies.set("admin_session", sign_session_id(principal.session_id, settings))
    return client, factory, settings, principal, repository_id


@pytest.mark.asyncio
async def test_production_admin_bypass_is_blocked(app_client) -> None:
    client, _ = app_client
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="production",
        public_base_url="https://review.example.test",
        admin_session_secret="x" * 32,
        github_bot_login="test-bot[bot]",
        allowed_github_accounts="inryeok-office",
        admin_local_bypass=True,
        admin_github_client_id="",
        admin_github_client_secret="",
    )
    assert (await client.get("/admin")).status_code == 503


def test_production_requires_https_public_base_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            public_base_url="http://review.example.test",
            admin_session_secret="x" * 32,
            github_bot_login="test-bot[bot]",
            allowed_github_accounts="inryeok-office",
        )


def test_production_does_not_require_organization_allowlist() -> None:
    settings = Settings(
        environment="production",
        public_base_url="https://review.example.test",
        admin_session_secret="x" * 32,
        github_bot_login="test-bot[bot]",
        allowed_github_accounts="",
    )
    assert settings.github_account_allowed("any-installation-account")


@pytest.mark.asyncio
async def test_admin_static_design_system_is_local(app_client) -> None:
    client, _ = app_client
    response = await client.get("/admin/static/admin.css")
    assert response.status_code == 200
    assert "--primary:" in response.text
    assert "unpkg" not in response.text
    assert ".preset-grid" in response.text
    assert "prefers-reduced-motion" in response.text


@pytest.mark.asyncio
async def test_admin_console_uses_korean_operational_labels(app_client) -> None:
    client, _, _, _, _ = await authenticated_repository(app_client)
    response = await client.get("/admin")
    assert response.status_code == 200
    assert "개요" in response.text
    assert "전체 운영 상태" in response.text
    assert "리뷰 작업" in response.text
    assert "Review jobs" not in response.text


@pytest.mark.asyncio
async def test_admin_detail_presets_are_explained_and_accessible(app_client) -> None:
    client, _, _, _, _ = await authenticated_repository(app_client)
    response = await client.get("/admin/settings")
    assert response.status_code == 200
    assert "핵심 검토" in response.text
    assert "균형 검토" in response.text
    assert "상세 검토" in response.text
    assert 'name="review_profile"' in response.text
    assert "리뷰 품질" in response.text


@pytest.mark.asyncio
async def test_admin_console_information_architecture_has_five_sections(app_client) -> None:
    client, _, _, _, repository_id = await authenticated_repository(app_client)
    paths = (
        "/admin",
        "/admin/repositories",
        f"/admin/repositories/{repository_id}",
        "/admin/jobs",
        "/admin/usage",
        "/admin/settings",
        "/admin/audit",
        "/admin/operations",
    )
    for path in paths:
        response = await client.get(path)
        assert response.status_code == 200, path
        assert "�" not in response.text, path
    dashboard = (await client.get("/admin")).text
    assert dashboard.count('class="nav"') == 1
    assert all(label in dashboard for label in ("개요", "저장소", "리뷰", "운영", "설정"))


@pytest.mark.asyncio
async def test_admin_empty_model_catalog_explains_cli_default(app_client) -> None:
    client, *_ = await authenticated_repository(app_client)
    response = await client.get("/admin/settings")
    assert response.status_code == 200
    assert "현재 CLI 기본 모델을 사용 중입니다" in response.text
    assert "검증된 선택 모델이 없습니다" in response.text
    assert 'name="model"' not in response.text


@pytest.mark.asyncio
async def test_admin_job_detail_explains_failure_and_finding_pipeline(app_client) -> None:
    client, factory, *_ = await authenticated_repository(app_client)
    async with factory() as session:
        job = ReviewJob(
            delivery_id="admin-detail-job",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.COMMAND,
            status=JobStatus.FAILED,
            attempts=1,
            error_code="OUTPUT_SCHEMA_MISMATCH",
            error_category="OUTPUT",
            error_stage="validation",
            user_action_required=True,
        )
        session.add(job)
        await session.flush()
        session.add(
            ReviewRun(
                job_id=job.id,
                base_sha="a" * 40,
                head_sha="b" * 40,
                summary="safe summary",
                reviewed_file_count=1,
                finding_count=1,
                raw_findings_count=2,
                schema_valid_findings_count=1,
                scope_valid_findings_count=1,
                evidence_findings_count=1,
                deduplicated_findings_count=1,
                rejected_findings_count=0,
                published_findings_count=1,
                comparison_new_count=1,
            )
        )
        await session.commit()
        job_id = job.id
    response = await client.get(f"/admin/jobs/{job_id}")
    assert response.status_code == 200
    assert "조치 필요" in response.text
    assert "Finding 처리" in response.text
    assert "모델 출력" in response.text
    assert "�" not in response.text


def test_installation_trust_does_not_depend_on_account_name() -> None:
    assert Settings(environment="development").github_account_allowed("any-account")
    assert Settings(environment="test").github_account_allowed("any-account")


def test_admin_redirect_stays_local() -> None:
    assert safe_admin_redirect("/admin/jobs/1") == "/admin/jobs/1"
    assert safe_admin_redirect("https://evil.invalid/") == "/admin"
    assert safe_admin_redirect("//evil.invalid/admin") == "/admin"


@respx.mock
@pytest.mark.asyncio
async def test_repository_admin_can_change_settings(app_client) -> None:
    client, factory, settings, principal, repository_id = await authenticated_repository(app_client)
    respx.get("https://api.github.com/repos/acme/repo").mock(
        return_value=httpx.Response(200, json={"permissions": {"admin": True}})
    )
    response = await client.post(
        f"/admin/repositories/{repository_id}/settings",
        data={
            "_csrf": csrf_token(principal, settings),
            "expected_version": "1",
            "enabled": "true",
            "auto_review": "true",
            "ignore_draft": "true",
            "min_confidence": "0.95",
            "max_findings": "7",
            "ignore_patterns": "dist/**",
        },
    )
    assert response.status_code == 303
    async with factory() as session:
        repository = await session.get(RepositorySettings, repository_id)
        assert repository and repository.min_confidence == 0.95
        assert repository.max_findings == 7 and repository.ignore_draft


@respx.mock
@pytest.mark.asyncio
async def test_repository_settings_partial_form_preserves_unsubmitted_values(app_client) -> None:
    client, factory, settings, principal, repository_id = await authenticated_repository(app_client)
    async with factory() as session:
        repository = await session.get(RepositorySettings, repository_id)
        assert repository
        repository.enabled = True
        repository.auto_review = True
        repository.ignore_draft = False
        repository.override_auto_review_enabled = True
        repository.override_review_profile = "THOROUGH"
        await session.commit()

    respx.get("https://api.github.com/repos/acme/repo").mock(
        return_value=httpx.Response(200, json={"permissions": {"admin": True}})
    )
    response = await client.post(
        f"/admin/repositories/{repository_id}/settings",
        data={
            "_csrf": csrf_token(principal, settings),
            "expected_version": "1",
            # Deliberately submit only one legacy field.  Missing checkbox and
            # override fields must not be interpreted as false/inherit.
            "ignore_patterns": "generated/**",
        },
    )
    assert response.status_code == 303
    async with factory() as session:
        repository = await session.get(RepositorySettings, repository_id)
        assert repository
        assert repository.enabled and repository.auto_review
        assert repository.ignore_draft is False
        assert repository.override_auto_review_enabled is True
        assert repository.override_review_profile == "THOROUGH"
        assert repository.ignore_patterns == "generated/**"


@respx.mock
@pytest.mark.asyncio
async def test_non_admin_cannot_change_settings(app_client) -> None:
    client, _, settings, principal, repository_id = await authenticated_repository(app_client)
    respx.get("https://api.github.com/repos/acme/repo").mock(
        return_value=httpx.Response(200, json={"permissions": {"admin": False}})
    )
    response = await client.post(
        f"/admin/repositories/{repository_id}/settings",
        data={
            "_csrf": csrf_token(principal, settings),
            "min_confidence": "0.9",
            "max_findings": "10",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_settings_change_requires_csrf(app_client) -> None:
    client, _, _, _, repository_id = await authenticated_repository(app_client)
    response = await client.post(
        f"/admin/repositories/{repository_id}/settings",
        data={"_csrf": "invalid", "min_confidence": "0.9", "max_findings": "10"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_global_settings_are_saved_with_audit_log(app_client) -> None:
    client, factory, settings, principal, _ = await authenticated_repository(app_client)
    response = await client.post(
        "/admin/settings",
        data={
            "_csrf": csrf_token(principal, settings),
            "expected_version": "1",
            "enabled": "true",
            "auto_review_enabled": "true",
            "command_review_enabled": "true",
            "language": "en",
            "review_profile": "THOROUGH",
            "max_findings": "8",
            "minimum_confidence": "0.9",
            "minimum_severity": "MEDIUM",
            "codex_timeout_seconds": "600",
            "review_on_opened": "true",
            "review_on_reopened": "true",
            "review_on_ready_for_review": "true",
            "review_on_synchronize": "true",
        },
    )
    assert response.status_code == 303
    async with factory() as session:
        value = await session.get(GlobalReviewSettings, 1)
        audit = await session.scalar(select(AdminAuditLog).limit(1))
        assert value and value.language == "en" and value.review_profile == "THOROUGH"
        assert audit and audit.target_type == "global_settings"


@pytest.mark.asyncio
async def test_audit_log_page_is_available_to_authenticated_admin(app_client) -> None:
    client, _, _, _, _ = await authenticated_repository(app_client)
    response = await client.get("/admin/audit")
    assert response.status_code == 200
    assert "감사 기록" in response.text


@pytest.mark.asyncio
async def test_oauth_state_mismatch_is_rejected(app_client) -> None:
    client, _ = app_client
    settings = oauth_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    login = await client.get("/auth/github/login")
    assert login.status_code == 302
    callback = await client.get("/auth/github/callback?code=code&state=wrong")
    assert callback.status_code == 400


@respx.mock
@pytest.mark.asyncio
async def test_oauth_callback_creates_server_side_session(app_client) -> None:
    client, factory = app_client
    settings = oauth_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    login = await client.get("/auth/github/login?redirect_to=/admin/repositories")
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "user-secret-token", "expires_in": 3600}
        )
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, json={"id": 42, "login": "admin-user"})
    )
    callback = await client.get(f"/auth/github/callback?code=code&state={state}")
    assert callback.status_code == 303
    assert callback.headers["location"] == "/admin/repositories"
    cookie = callback.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "user-secret-token" not in cookie
    async with factory() as session:
        records = list((await session.execute(select(AdminSession))).scalars())
        assert len(records) == 1
        assert "user-secret-token" not in records[0].encrypted_access_token


@respx.mock
@pytest.mark.asyncio
async def test_authenticated_non_superadmin_cannot_enter_global_console(app_client) -> None:
    client, factory = app_client
    settings = oauth_settings()
    settings.superadmin_github_logins = "exijn"
    app.dependency_overrides[get_settings] = lambda: settings
    principal = AdminPrincipal("non-admin-session", 43, "external-user", "user-token")
    async with factory() as session:
        session.add(
            AdminSession(
                id=principal.session_id,
                github_user_id=43,
                github_login=principal.github_login,
                encrypted_access_token=encrypt_token("user-token", settings),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()
    client.cookies.set("admin_session", sign_session_id(principal.session_id, settings))
    response = await client.get("/admin")
    assert response.status_code == 403


def test_superadmin_login_matching_is_case_insensitive_and_fail_closed() -> None:
    settings = Settings(environment="test", superadmin_github_logins=" ExIJn, second ")
    assert settings.is_superadmin_login("exijn")
    assert settings.is_superadmin_login("SECOND")
    assert not Settings(environment="test").is_superadmin_login("exijn")


@pytest.mark.asyncio
async def test_admin_lists_all_installations_not_organization_allowlist(app_client) -> None:
    client, factory = app_client
    settings = Settings(
        environment="development",
        admin_local_bypass=True,
        allowed_github_accounts="Acme",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    async with factory() as session:
        session.add_all(
            [
                RepositorySettings(
                    installation_id=20, repository_owner="acme", repository_name="visible"
                ),
                RepositorySettings(
                    installation_id=21,
                    repository_owner="outside-org",
                    repository_name="hidden",
                ),
            ]
        )
        await session.commit()
    response = await client.get("/admin/repositories")
    assert response.status_code == 200
    assert "acme/visible" in response.text
    assert "outside-org/hidden" in response.text
