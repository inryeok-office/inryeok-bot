from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.jobs.models import AdminAuditLog, GlobalReviewSettings, JobStatus, ReviewJob, TriggerType
from app.jobs.repository import JobRepository


@pytest.mark.asyncio
async def test_three_pushes_coalesce_with_capacity_one_and_preserve_running(app_client):
    _, factory = app_client
    async with factory() as session:
        session.add(GlobalReviewSettings(id=1))
        await session.commit()
        repository = JobRepository(session)
        jobs = []
        values = dict(
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            trigger_type=TriggerType.AUTO,
            trigger_action="synchronize",
            not_before=datetime.now(UTC) + timedelta(seconds=60),
        )
        for index, head in enumerate("bcd"):
            job, created = await repository.enqueue(
                max_pending_jobs=1,
                max_repository_pending_jobs=1,
                delivery_id=f"push-{index}",
                head_sha=head * 40,
                **values,
            )
            assert created
            jobs.append(job)
        assert [job.status for job in jobs] == [
            JobStatus.SKIPPED,
            JobStatus.SKIPPED,
            JobStatus.PENDING,
        ]
        assert len((await session.scalars(select(AdminAuditLog))).all()) == 2
        existing, created = await repository.enqueue(
            delivery_id="redelivery", head_sha="d" * 40, **values
        )
        assert existing.id == jobs[-1].id and not created
        jobs[-1].status = JobStatus.RUNNING
        await session.commit()
        _, created = await repository.enqueue(delivery_id="push-new", head_sha="e" * 40, **values)
        assert created and jobs[-1].status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_auto_coalescing_does_not_supersede_manual_request(app_client):
    _, factory = app_client
    async with factory() as session:
        manual = ReviewJob(
            delivery_id="manual",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
            trigger_type=TriggerType.COMMAND,
        )
        session.add(manual)
        await session.commit()
        await JobRepository(session).enqueue(
            delivery_id="auto",
            installation_id=1,
            repository_owner="acme",
            repository_name="repo",
            pull_request_number=7,
            base_sha="a" * 40,
            head_sha="c" * 40,
            trigger_type=TriggerType.AUTO,
        )
        assert manual.status == JobStatus.PENDING
