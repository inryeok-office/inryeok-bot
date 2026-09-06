from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import JobStatus, ReviewFailureNotice, ReviewJob, TriggerType


class QueueCapacityError(RuntimeError):
    """The bounded database queue cannot accept another job right now."""


def claim_statement() -> Select[tuple[ReviewJob]]:
    return (
        select(ReviewJob)
        .where(
            ReviewJob.status == JobStatus.PENDING,
            (ReviewJob.not_before.is_(None) | (ReviewJob.not_before <= datetime.now(UTC))),
        )
        .order_by(ReviewJob.created_at, ReviewJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        max_pending_jobs: int | None = None,
        max_repository_pending_jobs: int | None = None,
        **values: object,
    ) -> tuple[ReviewJob | None, bool]:
        if max_pending_jobs is not None:
            pending = await self.session.scalar(
                select(func.count())
                .select_from(ReviewJob)
                .where(ReviewJob.status == JobStatus.PENDING)
            )
            if int(pending or 0) >= max_pending_jobs:
                raise QueueCapacityError("review queue capacity reached")
        if max_repository_pending_jobs is not None:
            repository_pending = await self.session.scalar(
                select(func.count())
                .select_from(ReviewJob)
                .where(
                    ReviewJob.status == JobStatus.PENDING,
                    ReviewJob.repository_owner == values["repository_owner"],
                    ReviewJob.repository_name == values["repository_name"],
                )
            )
            if int(repository_pending or 0) >= max_repository_pending_jobs:
                raise QueueCapacityError("repository review queue capacity reached")
        job = ReviewJob(**values)
        self.session.add(job)
        try:
            if values["trigger_type"] == TriggerType.AUTO:
                await self.session.execute(
                    update(ReviewJob)
                    .where(
                        ReviewJob.status == JobStatus.PENDING,
                        ReviewJob.repository_owner == values["repository_owner"],
                        ReviewJob.repository_name == values["repository_name"],
                        ReviewJob.pull_request_number == values["pull_request_number"],
                        ReviewJob.head_sha != values["head_sha"],
                    )
                    .values(
                        status=JobStatus.SKIPPED,
                        error_code="SUPERSEDED",
                        error_message="Superseded by a newer pull request head",
                        superseded_by_head_sha=values["head_sha"],
                        finished_at=datetime.now(UTC),
                    )
                )
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

    async def retry(self, job_id: int) -> ReviewJob | None:
        job = await self.session.get(ReviewJob, job_id)
        if not job or job.status not in {JobStatus.FAILED, JobStatus.SKIPPED}:
            return None
        already_succeeded = await self.session.scalar(
            select(ReviewJob.id).where(
                ReviewJob.repository_owner == job.repository_owner,
                ReviewJob.repository_name == job.repository_name,
                ReviewJob.pull_request_number == job.pull_request_number,
                ReviewJob.head_sha == job.head_sha,
                ReviewJob.status == JobStatus.SUCCEEDED,
            )
        )
        if already_succeeded is not None:
            return None
        existing_retry = await self.session.scalar(
            select(ReviewJob).where(ReviewJob.retry_of_job_id == job.id).limit(1)
        )
        if existing_retry is not None:
            return existing_retry
        retry = ReviewJob(
            delivery_id=f"admin-retry:{job.id}:{datetime.now(UTC).timestamp():.6f}",
            source_comment_id=None,
            installation_id=job.installation_id,
            repository_owner=job.repository_owner,
            repository_name=job.repository_name,
            pull_request_number=job.pull_request_number,
            base_sha=job.base_sha,
            head_sha=job.head_sha,
            trigger_type=TriggerType.RETRY,
            retry_of_job_id=job.id,
        )
        self.session.add(retry)
        await self.session.commit()
        await self.session.refresh(retry)
        return retry

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
