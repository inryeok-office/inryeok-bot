from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import JobStatus, ReviewFailureNotice, ReviewJob, TriggerType


def claim_statement() -> Select[tuple[ReviewJob]]:
    return (
        select(ReviewJob)
        .where(ReviewJob.status == JobStatus.PENDING)
        .order_by(ReviewJob.created_at, ReviewJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(self, **values: object) -> tuple[ReviewJob | None, bool]:
        job = ReviewJob(**values)
        self.session.add(job)
        try:
            await self.session.commit()
            await self.session.refresh(job)
            return job, True
        except IntegrityError:
            await self.session.rollback()
            conditions = [ReviewJob.delivery_id == values["delivery_id"]]
            if values.get("source_comment_id") is not None:
                conditions.append(ReviewJob.source_comment_id == values["source_comment_id"])
            conditions.append(
                (ReviewJob.repository_owner == values["repository_owner"])
                & (ReviewJob.repository_name == values["repository_name"])
                & (ReviewJob.pull_request_number == values["pull_request_number"])
                & (ReviewJob.head_sha == values["head_sha"])
                & (ReviewJob.trigger_type == values["trigger_type"])
            )
            existing = await self.session.scalar(select(ReviewJob).where(or_(*conditions)))
            return existing, False

    async def claim_next(self) -> ReviewJob | None:
        job = await self.session.scalar(claim_statement())
        if job is None:
            return None
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.started_at = datetime.now(UTC)
        job.finished_at = None
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def recover_stale(self, seconds: int, max_attempts: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=seconds)
        result = await self.session.execute(
            update(ReviewJob)
            .where(
                ReviewJob.status == JobStatus.RUNNING,
                ReviewJob.started_at < cutoff,
                ReviewJob.attempts < max_attempts,
            )
            .values(
                status=JobStatus.PENDING,
                started_at=None,
                error_code="STALE_RECOVERED",
                error_message="Recovered after worker interruption",
            )
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(
            update(ReviewJob)
            .where(
                ReviewJob.status == JobStatus.RUNNING,
                ReviewJob.started_at < cutoff,
                ReviewJob.attempts >= max_attempts,
            )
            .values(
                status=JobStatus.FAILED,
                finished_at=datetime.now(UTC),
                error_code="MAX_ATTEMPTS",
                error_message="Stale job exceeded maximum attempts",
            )
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    async def finish(
        self,
        job: ReviewJob,
        status: JobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        job.status = status
        job.error_code = error_code
        job.error_message = error_message[:4000] if error_message else None
        job.finished_at = datetime.now(UTC)
        await self.session.commit()

    async def retry(self, job_id: int) -> bool:
        job = await self.session.get(ReviewJob, job_id)
        if not job or job.status not in {JobStatus.FAILED, JobStatus.SKIPPED}:
            return False
        job.status = JobStatus.PENDING
        job.trigger_type = TriggerType.RETRY
        job.finished_at = None
        job.error_code = None
        job.error_message = None
        await self.session.commit()
        return True

    async def get_or_create_failure_notice(
        self, job: ReviewJob, error_category: str
    ) -> ReviewFailureNotice:
        notice = await self.session.scalar(
            select(ReviewFailureNotice).where(
                ReviewFailureNotice.repository_owner == job.repository_owner,
                ReviewFailureNotice.repository_name == job.repository_name,
                ReviewFailureNotice.pull_request_number == job.pull_request_number,
                ReviewFailureNotice.head_sha == job.head_sha,
                ReviewFailureNotice.error_category == error_category,
            )
        )
        if notice is not None:
            return notice
        notice = ReviewFailureNotice(
            repository_owner=job.repository_owner,
            repository_name=job.repository_name,
            pull_request_number=job.pull_request_number,
            head_sha=job.head_sha,
            error_category=error_category,
        )
        self.session.add(notice)
        try:
            await self.session.commit()
            await self.session.refresh(notice)
            return notice
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(ReviewFailureNotice).where(
                    ReviewFailureNotice.repository_owner == job.repository_owner,
                    ReviewFailureNotice.repository_name == job.repository_name,
                    ReviewFailureNotice.pull_request_number == job.pull_request_number,
                    ReviewFailureNotice.head_sha == job.head_sha,
                    ReviewFailureNotice.error_category == error_category,
                )
            )
            assert existing is not None
            return existing
