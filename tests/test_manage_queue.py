from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.jobs.models import AdminAuditLog, GlobalReviewSettings, RepositorySettings
from scripts import manage_queue


@pytest.mark.asyncio
async def test_pause_test_does_not_rewrite_repository_policy(
    app_client, monkeypatch, capsys
) -> None:
    """The legacy test pause command must not reset production repository rows."""
    _, factory = app_client
    async with factory() as session:
        session.add(GlobalReviewSettings(id=1, processing_paused=False))
        session.add(
            RepositorySettings(
                installation_id=1,
                repository_owner="example",
                repository_name="inherited",
                enabled=False,
                auto_review=False,
                override_enabled=None,
                override_auto_review_enabled=None,
            )
        )
        await session.commit()

    monkeypatch.setattr(manage_queue, "get_session_factory", lambda: factory)
    await manage_queue.set_policy("example", "test")
    output = json.loads(capsys.readouterr().out)
    assert output["repository_policy_changed"] is False

    async with factory() as session:
        repository = await session.scalar(
            select(RepositorySettings).where(RepositorySettings.repository_name == "inherited")
        )
        assert repository is not None
        assert repository.enabled is False
        assert repository.auto_review is False
        global_settings = await session.get(GlobalReviewSettings, 1)
        assert global_settings is not None and global_settings.processing_paused is True
        audit = await session.scalar(
            select(AdminAuditLog).where(AdminAuditLog.action == "pause_processing")
        )
        assert audit is not None
            assert "informational" in audit.summary
