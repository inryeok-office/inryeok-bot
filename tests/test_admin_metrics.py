from datetime import UTC, datetime, timedelta

import pytest

from app.admin.metrics import usage_metrics
from app.jobs.models import JobStatus, ReviewJob, ReviewRun, TriggerType
from tests.test_admin import authenticated_repository


@pytest.mark.asyncio
async def test_usage_page_is_available_for_admin_and_has_empty_state(app_client) -> None:
    client, *_ = await authenticated_repository(app_client)
    response = await client.get("/admin/usage?period=7")
    assert response.status_code == 200
    assert "사용량 및 성공률" in response.text
    assert "선택한 기간에 작업이 없습니다." in response.text


@pytest.mark.asyncio
async def test_usage_metrics_aggregate_jobs_and_review_findings(app_client) -> None:
    client, factory, *_ = await authenticated_repository(app_client)
    now = datetime.now(UTC)
    async with factory() as session:
        succeeded = ReviewJob(
            delivery_id="metrics-success",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=1,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.COMMAND,
            status=JobStatus.SUCCEEDED,
            attempts=1,
            created_at=now - timedelta(hours=2),
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=59),
        )
        failed = ReviewJob(
            delivery_id="metrics-failed",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=2,
            base_sha="c" * 40,
            head_sha="d" * 40,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.FAILED,
            attempts=1,
            error_code="SCHEMA",
            created_at=now - timedelta(hours=1),
        )
        session.add_all([succeeded, failed])
        await session.flush()
        session.add(
            ReviewRun(
                job_id=succeeded.id,
                base_sha="a" * 40,
                head_sha="b" * 40,
                summary="safe summary",
                reviewed_file_count=1,
                finding_count=1,
                raw_findings_count=2,
                schema_valid_findings_count=2,
                evidence_findings_count=1,
                published_findings_count=1,
            )
        )
        await session.commit()

    async with factory() as session:
        metrics = await usage_metrics(session, 7)
    assert metrics.total_jobs == 2
    assert metrics.succeeded == 1
    assert metrics.failed == 1
    assert metrics.success_rate == 50.0
    assert metrics.raw_findings == 2
    assert metrics.published_findings == 1
    assert metrics.rejected_findings == 1
    assert {row.label for row in metrics.by_trigger} == {"AUTO", "COMMAND"}
    response = await client.get("/admin/usage?period=7")
    assert response.status_code == 200
    assert "SCHEMA" in response.text
