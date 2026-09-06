import asyncio
import logging
import signal
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.codex.executor_client import ExecutorRunner
from app.codex.runner import CodexError
from app.config import get_settings
from app.db.session import get_session_factory
from app.github.client import GitHubAPIError, GitHubClient
from app.jobs.models import JobStatus, ReviewJob
from app.jobs.repository import JobRepository
from app.logging import configure_logging, redact
from app.review.diff import DiffError
from app.review.service import ReviewService, ReviewSkipped

logger = logging.getLogger(__name__)

FAILURE_MESSAGES = {
    "QUOTA": "## ⚠️ 리뷰를 완료하지 못했습니다\n\nCodex 사용량 한도에 도달했습니다.",
    "RATE_LIMIT": "## ⏳ 리뷰 요청이 제한되었습니다\n\nCodex 요청 한도에 도달했습니다.",
    "AUTH": "## 🔐 리뷰를 완료하지 못했습니다\n\n리뷰 엔진 인증을 사용할 수 없습니다.",
    "SERVICE": "## 🌐 리뷰 엔진에 일시적인 문제가 있습니다\n\n잠시 후 다시 시도해 주세요.",
    "INTERNAL": "## ⚠️ 리뷰를 완료하지 못했습니다\n\n내부 오류가 발생했습니다.",
}


def failure_message(category: str, retry_at: datetime | None = None) -> str:
    message = FAILURE_MESSAGES[category]
    if category in {"QUOTA", "RATE_LIMIT"}:
        if retry_at is not None:
            utc_timestamp = retry_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
            kst_timestamp = retry_at.astimezone(ZoneInfo("Asia/Seoul")).strftime(
                "%Y-%m-%d %H:%M KST"
            )
            message += f"\n\n> 다시 시도 가능 시각: **{kst_timestamp}** (`{utc_timestamp}`)"
        else:
            message += "\n\n> 재시작 시각은 현재 Codex 응답에서 제공되지 않았습니다."
    if category == "AUTH":
        return message + "\n\n관리자가 인증을 복구한 뒤 `/review`로 재시도해 주세요."
    if category == "INTERNAL":
        return message + "\n\n관리자 확인 후 `/review`로 재시도해 주세요."
    return message + "\n\n사용 가능해진 뒤 `/review`로 재시도해 주세요."


def failure_category(error: CodexError | Exception) -> str:
    if isinstance(error, CodexError):
        return {
            "CODEX_QUOTA": "QUOTA",
            "CODEX_RATE_LIMIT": "RATE_LIMIT",
            "CODEX_AUTH": "AUTH",
            "CODEX_SERVICE_UNAVAILABLE": "SERVICE",
            "CODEX_TIMEOUT": "SERVICE",
        }.get(error.code, "INTERNAL")
    return "INTERNAL"


async def publish_failure_notice(
    repository: JobRepository,
    github: GitHubClient,
    job: ReviewJob,
    category: str,
    retry_at: datetime | None = None,
) -> None:
    """Publish one safe, user-facing failure notice for a PR head and category."""
    notice = await repository.get_or_create_failure_notice(job, category)
    if notice.github_comment_id is not None:
        return
    try:
        posted = await github.create_issue_comment(
            job.installation_id,
            job.repository_owner,
            job.repository_name,
            job.pull_request_number,
            failure_message(category, retry_at)
            + f"\n\n<!-- inryeok-review-failure:{category.casefold()} -->",
        )
        notice.github_comment_id = int(posted["id"])
        await repository.session.commit()
    except Exception:
        await repository.session.rollback()
        logger.warning("Unable to publish the review failure notice for job %s", job.id)


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
                if not settings.codex_executor_url:
                    raise CodexError("EXECUTOR_NOT_CONFIGURED", "Codex executor is not configured")
                runner = ExecutorRunner(
                    settings.codex_executor_url, settings.review_timeout_seconds + 60
                )
                await ReviewService(session, github, runner).execute(job)
                await repository.finish(job, JobStatus.SUCCEEDED)
            except ReviewSkipped as exc:
                await finish_after_error(
                    session, repository, job, JobStatus.SKIPPED, "SKIPPED", str(exc)
                )
            except CodexError as exc:
                assert github is not None
                await session.rollback()
                if exc.retryable and job.attempts < settings.worker_max_attempts:
                    job.status = JobStatus.PENDING
                    job.started_at = None
                    job.error_code = exc.code
                    job.error_message = str(exc)
                    await session.commit()
                else:
                    await repository.finish(job, JobStatus.FAILED, exc.code, str(exc))
                    await publish_failure_notice(
                        repository, github, job, failure_category(exc), exc.retry_at
                    )
            except (httpx.TimeoutException, httpx.NetworkError, DiffError, GitHubAPIError) as exc:
                assert github is not None
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
                    await publish_failure_notice(repository, github, job, failure_category(exc))
            except asyncio.CancelledError:
                await session.rollback()
                job.status = JobStatus.PENDING
                job.started_at = None
                await session.commit()
                raise
            except Exception as exc:
                assert github is not None
                logger.warning("Review job %s failed with an unexpected error", job.id)
                await finish_after_error(
                    session, repository, job, JobStatus.FAILED, "UNEXPECTED", redact(str(exc))
                )
                await publish_failure_notice(repository, github, job, failure_category(exc))
            finally:
                if github is not None:
                    await github.http.aclose()


def main() -> None:
    configure_logging()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
