"""Regression coverage for installation policy persistence and queue parity.

These tests model the failure mode where an installation refresh treats an
existing repository as a new row, and the related failure mode where the
worker's claim predicate disagrees with the effective policy resolver.
"""

from conftest import signed
from sqlalchemy import select

from app.config import Settings
from app.jobs.models import (
    GlobalReviewSettings,
    RepositorySettings,
    ReviewJob,
    TriggerType,
)
from app.jobs.repository import JobRepository
from app.review.settings import resolve


async def _sync_added(client, *, delivery: str, installation_id: int, full_name: str) -> None:
    payload = {
        "action": "added",
        "installation": {"id": installation_id, "account": {"login": "acme"}},
        "repositories_added": [{"full_name": full_name}],
        "repositories_removed": [],
    }
    body, headers = signed(payload, "installation_repositories", delivery)
    response = await client.post("/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["synced"] is True


async def _sync_installation_created(client, *, delivery: str, installation_id: int) -> None:
    payload = {
        "action": "created",
        "installation": {"id": installation_id, "account": {"login": "acme"}},
    }
    body, headers = signed(payload, "installation", delivery)
    response = await client.post("/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["synced"] is True


async def test_installation_refresh_recovers_legacy_inherited_disabled_row(app_client) -> None:
    """A NULL override means inheritance, even if a legacy materialized flag is false."""

    client, factory = app_client
    async with factory() as session:
        session.add(
            RepositorySettings(
                installation_id=41,
                repository_owner="acme",
                repository_name="legacy-disabled",
                installed=True,
                enabled=False,
                auto_review=False,
                override_enabled=None,
                override_auto_review_enabled=None,
            )
        )
        await session.commit()

    await _sync_added(
        client,
        delivery="policy-repair-41-a",
        installation_id=41,
        full_name="acme/legacy-disabled",
    )
    async with factory() as session:
        repository = await session.scalar(
            select(RepositorySettings).where(RepositorySettings.installation_id == 41)
        )
        assert repository is not None
        assert repository.installed is True
        assert repository.enabled is True
        assert repository.auto_review is True

    # A second delivery (as happens after a restart or redelivery) must be
    # idempotent and must not reset the inherited policy again.
    await _sync_added(
        client,
        delivery="policy-repair-41-b",
        installation_id=41,
        full_name="acme/legacy-disabled",
    )
    async with factory() as session:
        repository = await session.scalar(
            select(RepositorySettings).where(RepositorySettings.installation_id == 41)
        )
        assert repository is not None and repository.enabled and repository.auto_review


async def test_reinstall_restores_inherited_policy_but_preserves_explicit_off(app_client) -> None:
    client, factory = app_client
    async with factory() as session:
        session.add_all(
            [
                RepositorySettings(
                    installation_id=42,
                    repository_owner="acme",
                    repository_name="repo",
                    enabled=True,
                    auto_review=True,
                    override_enabled=None,
                    override_auto_review_enabled=None,
                ),
                RepositorySettings(
                    installation_id=43,
                    repository_owner="acme",
                    repository_name="repo",
                    enabled=False,
                    auto_review=False,
                    override_enabled=False,
                    override_auto_review_enabled=False,
                ),
            ]
        )
        await session.commit()

    # The fixture GitHub client returns acme/repo, so use the added event for
    # the inherited row and verify a removed/re-added row through that path.
    await _sync_added(
        client,
        delivery="policy-reinstall-42-a",
        installation_id=42,
        full_name="acme/repo",
    )
    removed = {
        "action": "removed",
        "installation": {"id": 42, "account": {"login": "acme"}},
        "repositories_added": [],
        "repositories_removed": [{"full_name": "acme/repo"}],
    }
    body, headers = signed(removed, "installation_repositories", "policy-reinstall-42-b")
    response = await client.post("/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["synced"] is True
    await _sync_added(
        client,
        delivery="policy-reinstall-42-c",
        installation_id=42,
        full_name="acme/repo",
    )

    async with factory() as session:
        restored = await session.scalar(
            select(RepositorySettings).where(
                RepositorySettings.installation_id == 42,
                RepositorySettings.repository_name == "repo",
            )
        )
        assert restored is not None and restored.installed and restored.enabled

    await _sync_added(
        client,
        delivery="policy-reinstall-43-a",
        installation_id=43,
        full_name="acme/repo",
    )
    async with factory() as session:
        explicit_off = await session.scalar(
            select(RepositorySettings).where(
                RepositorySettings.installation_id == 43,
                RepositorySettings.repository_name == "repo",
            )
        )
        assert explicit_off is not None
        assert explicit_off.override_enabled is False
        assert explicit_off.override_auto_review_enabled is False
        assert explicit_off.enabled is False
        assert explicit_off.auto_review is False


async def _add_pending_job(session, *, delivery: str, trigger: TriggerType) -> None:
    session.add(
        ReviewJob(
            delivery_id=delivery,
            installation_id=51,
            repository_owner="acme",
            repository_name="policy-repo",
            pull_request_number=1,
            base_sha="a" * 40,
            head_sha=delivery.ljust(40, "0")[:40],
            trigger_type=trigger,
        )
    )
    await session.commit()


async def test_claim_matches_explicit_repository_enabled_override(app_client) -> None:
    """The worker must not claim a job the resolver marks disabled."""

    _, factory = app_client
    async with factory() as session:
        global_settings = GlobalReviewSettings(id=1, enabled=True, auto_review_enabled=True)
        repository = RepositorySettings(
            installation_id=51,
            repository_owner="acme",
            repository_name="policy-repo",
            installed=True,
            enabled=True,
            auto_review=True,
            override_enabled=False,
        )
        session.add_all([global_settings, repository])
        await session.flush()
        effective = resolve(global_settings, repository, Settings(environment="test"))
        assert effective.enabled is False
        await _add_pending_job(session, delivery="claim-override-off", trigger=TriggerType.COMMAND)
        assert await JobRepository(session).claim_next() is None


async def test_claim_matches_global_enabled_default(app_client) -> None:
    """A globally disabled policy must block claims even with a true row flag."""

    _, factory = app_client
    async with factory() as session:
        global_settings = GlobalReviewSettings(id=1, enabled=False, auto_review_enabled=True)
        repository = RepositorySettings(
            installation_id=51,
            repository_owner="acme",
            repository_name="policy-repo",
            installed=True,
            enabled=True,
            auto_review=True,
        )
        session.add_all([global_settings, repository])
        await session.flush()
        effective = resolve(global_settings, repository, Settings(environment="test"))
        assert effective.enabled is False
        await _add_pending_job(session, delivery="claim-global-off", trigger=TriggerType.AUTO)
        assert await JobRepository(session).claim_next() is None


async def test_claim_respects_command_review_override(app_client) -> None:
    """Manual jobs must use manual-review policy, independently of auto review."""

    _, factory = app_client
    async with factory() as session:
        global_settings = GlobalReviewSettings(id=1, enabled=True, command_review_enabled=True)
        repository = RepositorySettings(
            installation_id=51,
            repository_owner="acme",
            repository_name="policy-repo",
            installed=True,
            enabled=True,
            auto_review=True,
            override_command_review_enabled=False,
        )
        session.add_all([global_settings, repository])
        await session.flush()
        effective = resolve(global_settings, repository, Settings(environment="test"))
        assert effective.command_review_enabled is False
        await _add_pending_job(session, delivery="claim-command-off", trigger=TriggerType.COMMAND)
        assert await JobRepository(session).claim_next() is None
