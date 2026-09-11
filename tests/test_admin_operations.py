import pytest
from sqlalchemy import select

from app.admin.auth import csrf_token
from app.jobs.models import AdminAuditLog, GlobalReviewSettings, RepositorySettings
from tests.test_admin import authenticated_repository


@pytest.mark.asyncio
async def test_operations_page_has_pause_resume_control_and_dashboard_link(app_client) -> None:
    client, factory, settings, principal, _ = await authenticated_repository(app_client)
    async with factory() as session:
        session.add(GlobalReviewSettings(id=1, processing_paused=True, version=4))
        await session.commit()
    response = await client.get("/admin/operations")
    assert response.status_code == 200
    assert "/admin/operations/resume" in response.text
    assert "처리 재개" in response.text
    dashboard = await client.get("/admin")
    assert 'href="/admin/operations"' in dashboard.text


@pytest.mark.asyncio
async def test_resume_pause_are_audited_and_do_not_change_repository_policy(app_client) -> None:
    client, factory, settings, principal, repository_id = await authenticated_repository(app_client)
    async with factory() as session:
        session.add(GlobalReviewSettings(id=1, processing_paused=True, version=2))
        repo = await session.get(RepositorySettings, repository_id)
        assert repo
        repo.enabled = True
        repo.auto_review = True
        repo.override_enabled = False
        await session.commit()

    response = await client.post(
        "/admin/operations/resume",
        data={
            "_csrf": csrf_token(principal, settings),
            "expected_version": "2",
            "reason": "test resume",
        },
    )
    assert response.status_code == 303
    async with factory() as session:
        value = await session.get(GlobalReviewSettings, 1)
        repo = await session.get(RepositorySettings, repository_id)
        audit = await session.scalar(
            select(AdminAuditLog)
            .where(AdminAuditLog.target_type == "global_processing")
            .order_by(AdminAuditLog.id.desc())
        )
        assert value and value.processing_paused is False and value.version == 3
        assert repo and repo.enabled and repo.auto_review and repo.override_enabled is False
        assert audit and audit.action == "resume_processing"

    response = await client.post(
        "/admin/operations/pause",
        data={
            "_csrf": csrf_token(principal, settings),
            "expected_version": "3",
            "reason": "test pause",
        },
    )
    assert response.status_code == 303


@pytest.mark.asyncio
async def test_operations_reject_csrf_and_stale_version(app_client) -> None:
    client, _, settings, principal, _ = await authenticated_repository(app_client)
    response = await client.post(
        "/admin/operations/resume",
        data={"_csrf": "bad", "expected_version": "1"},
    )
    assert response.status_code == 403
    response = await client.post(
        "/admin/operations/resume",
        data={"_csrf": csrf_token(principal, settings), "expected_version": "999"},
    )
    assert response.status_code == 409
