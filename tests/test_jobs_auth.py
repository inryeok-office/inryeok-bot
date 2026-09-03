from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.dialects import postgresql

from app.config import Settings
from app.github.auth import InstallationTokenProvider
from app.jobs.models import JobStatus, ReviewJob, TriggerType
from app.jobs.repository import JobRepository, claim_statement


def test_claim_uses_postgres_skip_locked():
    sql = str(claim_statement().compile(dialect=postgresql.dialect()))
    assert "SKIP LOCKED" in sql
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_claim_order_and_stale_recovery(app_client):
    _, factory = app_client
    async with factory() as session:
        session.add_all(
            [
                ReviewJob(
                    delivery_id="one",
                    installation_id=1,
                    repository_owner="o",
                    repository_name="r",
                    pull_request_number=1,
                    base_sha="a" * 40,
                    head_sha="b" * 40,
                    trigger_type=TriggerType.AUTO,
                ),
                ReviewJob(
                    delivery_id="two",
                    installation_id=1,
                    repository_owner="o",
                    repository_name="r",
                    pull_request_number=2,
                    base_sha="a" * 40,
                    head_sha="c" * 40,
                    trigger_type=TriggerType.AUTO,
                ),
            ]
        )
        await session.commit()
        first = await JobRepository(session).claim_next()
        second = await JobRepository(session).claim_next()
        assert first and second and first.delivery_id == "one" and second.delivery_id == "two"
        first.status = JobStatus.RUNNING
        first.started_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()
        assert await JobRepository(session).recover_stale(60, 3) == 1
        await session.refresh(first)
        assert first.status == JobStatus.PENDING


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_is_cached_and_not_in_error():
    settings = Settings(
        environment="test", github_api_url="https://api.github.test", github_app_id="1"
    )
    client = httpx.AsyncClient()
    provider = InstallationTokenProvider(settings, client)
    provider._tokens[3] = type("T", (), {"value": "secret-token", "expires_at": 99999999999})()
    assert await provider.get(3) == "secret-token"
    await client.aclose()
