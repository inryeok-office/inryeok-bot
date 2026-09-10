from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import Settings
from app.jobs.models import JobStatus, OpsIncident, ReviewJob, TriggerType
from app.jobs.repository import JobRepository
from app.ops.alerts import record_incident
from app.review.model_catalog import load_catalog, validate_model_effort


def test_verified_model_catalog_is_explicit_and_effort_is_model_scoped() -> None:
    settings = Settings(
        environment="test",
        codex_model_catalog_json=(
            '[{"model_id":"verified-a","display_name":"A",'
            '"supported_efforts":["low","high"],"default_effort":"low",'
            '"enabled":true,"availability_status":"VERIFIED","version":"2"}]'
        ),
    )
    assert [item.model_id for item in load_catalog(settings)] == ["verified-a"]
    validate_model_effort(settings, "verified-a", "high")
    with pytest.raises(ValueError, match="not supported"):
        validate_model_effort(settings, "verified-a", "medium")
    with pytest.raises(ValueError, match="not verified"):
        validate_model_effort(settings, "unknown", "low")


@pytest.mark.asyncio
async def test_alerts_are_deduplicated_without_persisting_sensitive_context(app_client) -> None:
    _, factory = app_client
    settings = Settings(environment="test", ops_alert_cooldown_seconds=900)
    async with factory() as session:
        await record_incident(
            session,
            settings,
            incident_key="test:worker-down",
            severity="high",
            summary="worker unhealthy",
            context={"job_id": 7, "prompt": "must not persist", "error_code": "WORKER_DOWN"},
        )
        await record_incident(
            session,
            settings,
            incident_key="test:worker-down",
            severity="high",
            summary="worker unhealthy",
            context={"job_id": 7, "stderr": "must not persist"},
        )
        incident = await session.scalar(
            select(OpsIncident).where(OpsIncident.incident_key == "test:worker-down")
        )
        assert incident is not None
        assert incident.occurrence_count == 2
        assert incident.last_notified_at == incident.first_seen_at
        assert "prompt" not in (incident.safe_context or "")
        assert "stderr" not in (incident.safe_context or "")


@pytest.mark.asyncio
async def test_stale_execution_identity_is_never_retried(app_client) -> None:
    _, factory = app_client
    async with factory() as session:
        job = ReviewJob(
            delivery_id="unknown-outcome",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=1,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.COMMAND,
            status=JobStatus.RUNNING,
            attempts=1,
            execution_id="already-started-execution",
            started_at=datetime.now(UTC) - timedelta(hours=2),
        )
        session.add(job)
        await session.commit()
        assert await JobRepository(session).recover_stale(60, 3) == 0
        await session.refresh(job)
        assert job.status == JobStatus.FAILED
        assert job.error_code == "UNKNOWN_OUTCOME"
        assert job.retry_policy == "NEVER"


@pytest.mark.asyncio
async def test_stale_job_without_execution_identity_can_be_requeued(app_client) -> None:
    _, factory = app_client
    async with factory() as session:
        job = ReviewJob(
            delivery_id="safe-recover",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=2,
            base_sha="a" * 40,
            head_sha="c" * 40,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.RUNNING,
            attempts=1,
            started_at=datetime.now(UTC) - timedelta(hours=2),
        )
        session.add(job)
        await session.commit()
        assert await JobRepository(session).recover_stale(60, 3) == 1
        await session.refresh(job)
        assert job.status == JobStatus.PENDING
