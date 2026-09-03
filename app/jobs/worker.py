import asyncio
import logging
import signal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.codex.runner import CodexError, CodexRunner
from app.config import get_settings
from app.db.session import get_session_factory
from app.github.client import GitHubAPIError, GitHubClient
from app.jobs.models import JobStatus, ReviewJob
from app.jobs.repository import JobRepository
from app.logging import configure_logging, redact
from app.review.diff import DiffError
from app.review.service import ReviewService, ReviewSkipped

logger = logging.getLogger(__name__)


async def finish_after_error(
    session: AsyncSession,
    repository: JobRepository,
    job: ReviewJob,
    status: JobStatus,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Clear a failed transaction before recording a terminal job state."""
    await session.rollback()
    await repository.finish(job, status, error_code, error_message)


async def run_worker() -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, stop.set)
        except NotImplementedError:
            pass
    async with get_session_factory()() as session:
        await JobRepository(session).recover_stale(
            settings.stale_running_seconds, settings.worker_max_attempts
        )
    while not stop.is_set():
        async with get_session_factory()() as session:
            repository = JobRepository(session)
            job = await repository.claim_next()
            if not job:
                try:
                    await asyncio.wait_for(stop.wait(), settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
            github: GitHubClient | None = None
            try:
                github = GitHubClient(settings)
                await ReviewService(session, github, CodexRunner(settings)).execute(job)
                await repository.finish(job, JobStatus.SUCCEEDED)
            except ReviewSkipped as exc:
                await finish_after_error(
                    session, repository, job, JobStatus.SKIPPED, "SKIPPED", str(exc)
                )
            except CodexError as exc:
                await session.rollback()
                if exc.retryable and job.attempts < settings.worker_max_attempts:
                    job.status = JobStatus.PENDING
                    job.started_at = None
                    job.error_code = exc.code
                    job.error_message = str(exc)
                    await session.commit()
                else:
                    await repository.finish(job, JobStatus.FAILED, exc.code, str(exc))
            except (httpx.TimeoutException, httpx.NetworkError, DiffError, GitHubAPIError) as exc:
                await session.rollback()
                retryable = not isinstance(exc, GitHubAPIError) or exc.retryable
                if retryable and job.attempts < settings.worker_max_attempts:
                    job.status = JobStatus.PENDING
                    job.started_at = None
                    job.error_code = "TRANSIENT"
                    job.error_message = redact(str(exc))
                    await session.commit()
                else:
                    await repository.finish(
                        job, JobStatus.FAILED, "EXTERNAL_FAILURE", redact(str(exc))
                    )
            except asyncio.CancelledError:
                await session.rollback()
                job.status = JobStatus.PENDING
                job.started_at = None
                await session.commit()
                raise
            except Exception as exc:
                logger.exception("Review job %s failed", job.id)
                await finish_after_error(
                    session, repository, job, JobStatus.FAILED, "UNEXPECTED", redact(str(exc))
                )
            finally:
                if github is not None:
                    await github.http.aclose()


def main() -> None:
    configure_logging()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
