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
from app.jobs.models import AdminSession, RepositorySettings
from app.main import app


def oauth_settings() -> Settings:
    return Settings(
        environment="test",
        public_base_url="http://test",
        admin_github_client_id="client-id",
        admin_github_client_secret="client-secret",
        admin_session_secret="s" * 32,
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


def test_production_rejects_empty_account_allowlist() -> None:
    with pytest.raises(ValidationError, match="ALLOWED_GITHUB_ACCOUNTS"):
        Settings(
            environment="production",
            public_base_url="https://review.example.test",
            admin_session_secret="x" * 32,
            github_bot_login="test-bot[bot]",
            allowed_github_accounts="",
        )


def test_unlisted_accounts_can_only_be_enabled_explicitly_in_development() -> None:
    development = Settings(
        environment="development",
        allow_unlisted_github_accounts=True,
    )
    assert development.github_account_allowed("any-account")
    assert not Settings(environment="test").github_account_allowed("any-account")


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


@pytest.mark.asyncio
async def test_admin_lists_only_allowed_accounts(app_client) -> None:
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
    assert "outside-org/hidden" not in response.text
