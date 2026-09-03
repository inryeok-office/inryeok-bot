from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.codex.runner import CodexError
from app.config import Settings
from app.github.auth import InstallationTokenProvider
from app.jobs.models import JobStatus, ReviewFailureNotice, ReviewJob, TriggerType
from app.jobs.repository import JobRepository, claim_statement
from app.jobs.worker import (
    FAILURE_MESSAGES,
    failure_category,
    finish_after_error,
    publish_failure_notice,
)


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


@pytest.mark.asyncio
async def test_error_finish_rolls_back_failed_transaction(app_client):
    _, factory = app_client
    async with factory() as session:
        job = ReviewJob(
            delivery_id="rollback-target",
            installation_id=1,
            repository_owner="o",
            repository_name="r",
            pull_request_number=1,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.AUTO,
        )
        session.add(job)
        await session.commit()
        session.add(
            ReviewJob(
                delivery_id="rollback-target",
                installation_id=1,
                repository_owner="o",
                repository_name="r",
                pull_request_number=2,
                base_sha="a" * 40,
                head_sha="c" * 40,
                trigger_type=TriggerType.AUTO,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await finish_after_error(
            session, JobRepository(session), job, JobStatus.FAILED, "UNEXPECTED", "database error"
        )
        await session.refresh(job)
        assert job.status == JobStatus.FAILED
        assert job.error_code == "UNEXPECTED"


@pytest.mark.asyncio
async def test_failure_notices_are_korean_and_deduplicated(app_client) -> None:
    _, factory = app_client

    class NoticeGitHub:
        def __init__(self) -> None:
            self.bodies: list[str] = []

        async def create_issue_comment(self, *_: object) -> dict[str, int]:
            self.bodies.append(str(_[-1]))
            return {"id": 5_107_673_581 + len(self.bodies)}

    cases = [
        ("CODEX_QUOTA", "QUOTA"),
        ("CODEX_RATE_LIMIT", "RATE_LIMIT"),
        ("CODEX_AUTH", "AUTH"),
        ("CODEX_SERVICE_UNAVAILABLE", "SERVICE"),
        ("CODEX_FAILED", "INTERNAL"),
    ]
    async with factory() as session:
        job = ReviewJob(
            delivery_id="failure-notice",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=1,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.COMMAND,
        )
        session.add(job)
        await session.commit()
        repository = JobRepository(session)
        github = NoticeGitHub()
        for code, category in cases:
            error = CodexError(code, "sensitive stderr must not be exposed")
            assert failure_category(error) == category
            await publish_failure_notice(repository, github, job, category)  # type: ignore[arg-type]
        await publish_failure_notice(repository, github, job, "QUOTA")  # type: ignore[arg-type]
        assert len(github.bodies) == len(cases)
        assert all("sensitive stderr" not in body for body in github.bodies)
        assert all(
            FAILURE_MESSAGES[category] in body
            for body, (_, category) in zip(github.bodies, cases, strict=True)
        )
        assert await session.scalar(select(func.count()).select_from(ReviewFailureNotice)) == len(
            cases
        )


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
