import pytest
from conftest import FakeGitHub, signed
from sqlalchemy import func, select

from app.github.webhook import get_github
from app.jobs.models import RepositorySettings, ReviewJob
from app.main import app


@pytest.mark.asyncio
async def test_valid_signature_enqueues_and_duplicate_is_idempotent(app_client, pr_payload):
    client, factory = app_client
    body, headers = signed(pr_payload)
    first = await client.post("/webhooks/github", content=body, headers=headers)
    second = await client.post("/webhooks/github", content=body, headers=headers)
    assert first.status_code == 200 and first.json()["created"] is True
    assert second.status_code == 200 and second.json()["ignored"] == "duplicate_delivery"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ReviewJob)) == 1


@pytest.mark.asyncio
async def test_invalid_signature_rejected(app_client, pr_payload):
    client, _ = app_client
    body, headers = signed(pr_payload)
    headers["X-Hub-Signature-256"] = "sha256=bad"
    assert (await client.post("/webhooks/github", content=body, headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_unsupported_event_ignored(app_client):
    client, _ = app_client
    body, headers = signed({}, "push")
    response = await client.post("/webhooks/github", content=body, headers=headers)
    assert response.json()["ignored"] == "unsupported_event"


@pytest.mark.asyncio
async def test_draft_skipped(app_client, pr_payload):
    client, _ = app_client
    pr_payload["pull_request"]["draft"] = True
    body, headers = signed(pr_payload)
    assert (await client.post("/webhooks/github", content=body, headers=headers)).json()[
        "ignored"
    ] == "draft"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["opened", "reopened", "ready_for_review", "synchronize"])
async def test_pull_request_actions(app_client, pr_payload, action):
    client, _ = app_client
    pr_payload["action"] = action
    body, headers = signed(pr_payload, delivery=f"d-{action}")
    assert (await client.post("/webhooks/github", content=body, headers=headers)).json()[
        "created"
    ] is True


@pytest.mark.asyncio
async def test_pr_comment_command(app_client):
    client, _ = app_client
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "repository": {"name": "repo", "owner": {"login": "acme"}},
        "sender": {"login": "alice"},
        "issue": {"number": 7, "pull_request": {"url": "x"}},
        "comment": {"id": 101, "body": "  /review  ", "user": {"login": "alice"}},
    }
    body, headers = signed(payload, "issue_comment", "comment-1")
    assert (await client.post("/webhooks/github", content=body, headers=headers)).json()[
        "created"
    ] is True


@pytest.mark.asyncio
async def test_installation_repository_sync(app_client):
    client, _ = app_client
    payload = {
        "action": "added",
        "installation": {"id": 3},
        "repositories_added": [{"full_name": "acme/new-repo"}],
        "repositories_removed": [],
    }
    body, headers = signed(payload, "installation_repositories", "install-1")
    assert (await client.post("/webhooks/github", content=body, headers=headers)).json()[
        "synced"
    ] is True


@pytest.mark.asyncio
async def test_installation_created_syncs_default_settings(app_client):
    client, factory = app_client
    body, headers = signed(
        {"action": "created", "installation": {"id": 8}},
        "installation",
        "install-created",
    )
    assert (await client.post("/webhooks/github", content=body, headers=headers)).json()[
        "synced"
    ] is True
    async with factory() as session:
        repository = await session.scalar(
            select(RepositorySettings).where(RepositorySettings.installation_id == 8)
        )
        assert repository
        assert repository.enabled and repository.installed and repository.auto_review
        assert repository.min_confidence == 0.9 and repository.max_findings == 10
        assert repository.ignore_draft and not repository.include_low_severity
        assert "dist/**" in repository.ignore_patterns and "*.lock" in repository.ignore_patterns


@pytest.mark.asyncio
async def test_installation_deleted_disables_without_deleting(app_client):
    client, factory = app_client
    body, headers = signed(
        {"action": "created", "installation": {"id": 9}},
        "installation",
        "install-created-9",
    )
    await client.post("/webhooks/github", content=body, headers=headers)
    body, headers = signed(
        {"action": "deleted", "installation": {"id": 9}},
        "installation",
        "install-deleted-9",
    )
    await client.post("/webhooks/github", content=body, headers=headers)
    async with factory() as session:
        repository = await session.scalar(
            select(RepositorySettings).where(RepositorySettings.installation_id == 9)
        )
        assert repository and not repository.enabled and not repository.installed


@pytest.mark.asyncio
async def test_installation_suspend_and_unsuspend(app_client):
    client, factory = app_client
    for action, delivery in (
        ("created", "lifecycle-created"),
        ("suspend", "lifecycle-suspend"),
        ("unsuspend", "lifecycle-unsuspend"),
    ):
        body, headers = signed(
            {"action": action, "installation": {"id": 10}}, "installation", delivery
        )
        await client.post("/webhooks/github", content=body, headers=headers)
    async with factory() as session:
        repository = await session.scalar(
            select(RepositorySettings).where(RepositorySettings.installation_id == 10)
        )
        assert repository and repository.enabled and repository.installed


@pytest.mark.asyncio
async def test_installation_repository_removed_is_disabled(app_client):
    client, factory = app_client
    added = {
        "action": "added",
        "installation": {"id": 11},
        "repositories_added": [{"full_name": "acme/removed"}],
        "repositories_removed": [],
    }
    body, headers = signed(added, "installation_repositories", "repo-added")
    await client.post("/webhooks/github", content=body, headers=headers)
    removed = {
        "action": "removed",
        "installation": {"id": 11},
        "repositories_added": [],
        "repositories_removed": [{"full_name": "acme/removed"}],
    }
    body, headers = signed(removed, "installation_repositories", "repo-removed")
    await client.post("/webhooks/github", content=body, headers=headers)
    async with factory() as session:
        repository = await session.scalar(
            select(RepositorySettings).where(RepositorySettings.installation_id == 11)
        )
        assert repository and not repository.enabled and not repository.installed


@pytest.mark.asyncio
async def test_same_comment_id_with_new_delivery_is_not_reprocessed(app_client):
    client, _ = app_client
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "repository": {"name": "repo", "owner": {"login": "acme"}},
        "sender": {"login": "alice"},
        "issue": {"number": 8, "pull_request": {"url": "x"}},
        "comment": {"id": 777, "body": "/review", "user": {"login": "alice"}},
    }
    body, headers = signed(payload, "issue_comment", "comment-delivery-1")
    assert (await client.post("/webhooks/github", content=body, headers=headers)).json()[
        "created"
    ] is True
    body, headers = signed(payload, "issue_comment", "comment-delivery-2")
    assert (await client.post("/webhooks/github", content=body, headers=headers)).json()[
        "created"
    ] is False


@pytest.mark.asyncio
async def test_read_permission_command_is_ignored(app_client):
    client, _ = app_client

    class ReadOnlyGitHub(FakeGitHub):
        permission = "read"

    app.dependency_overrides[get_github] = lambda: ReadOnlyGitHub()
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "repository": {"name": "repo", "owner": {"login": "acme"}},
        "sender": {"login": "reader"},
        "issue": {"number": 9, "pull_request": {"url": "x"}},
        "comment": {"id": 909, "body": "/review", "user": {"login": "reader"}},
    }
    body, headers = signed(payload, "issue_comment", "read-user")
    response = await client.post("/webhooks/github", content=body, headers=headers)
    assert response.json()["ignored"] == "insufficient_permission"
