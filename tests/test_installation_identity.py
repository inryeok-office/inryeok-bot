from sqlalchemy import select

from app.jobs.models import GitHubInstallation, RepositorySettings
from tests.conftest import signed


async def test_webhook_persists_installation_and_numeric_repository_identity(
    app_client, pr_payload
):
    client, factory = app_client
    pr_payload["repository"]["id"] = 987654321  # type: ignore[index]
    body, headers = signed(pr_payload, delivery="identity-1")

    response = await client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 200
    async with factory() as session:
        installation = await session.scalar(
            select(GitHubInstallation).where(GitHubInstallation.github_installation_id == 1)
        )
        repository = await session.scalar(
            select(RepositorySettings).where(RepositorySettings.github_repository_id == 987654321)
        )
        assert installation is not None
        assert installation.account_login == "acme"
        assert repository is not None
        assert repository.installation_fk_id == installation.id


async def test_repository_identity_is_scoped_to_installation(app_client, pr_payload):
    client, factory = app_client
    pr_payload["repository"]["id"] = 123  # type: ignore[index]
    body, headers = signed(pr_payload, delivery="identity-2")
    assert (await client.post("/webhooks/github", content=body, headers=headers)).status_code == 200

    second = dict(pr_payload)
    second["installation"] = {"id": 2, "account": {"login": "other"}}
    second["pull_request"] = {
        **second["pull_request"],
        "head": {"sha": "c" * 40},
    }
    body, headers = signed(second, delivery="identity-3")
    assert (await client.post("/webhooks/github", content=body, headers=headers)).status_code == 200

    async with factory() as session:
        rows = (
            await session.scalars(
                select(RepositorySettings).where(RepositorySettings.github_repository_id == 123)
            )
        ).all()
        assert {row.installation_id for row in rows} == {1, 2}
