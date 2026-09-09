import asyncio
import logging
import signal
from datetime import UTC, datetime
from uuid import uuid4
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
from app.logging import configure_logging
from app.review.diff import DiffError
from app.review.failures import ReviewFailure, failure_from_exception
from app.review.service import ReviewService, ReviewSkipped

logger = logging.getLogger(__name__)

FAILURE_MESSAGES = {
    "QUOTA": "## ⚠️ 리뷰를 완료하지 못했습니다\n\nCodex 사용량 한도에 도달했습니다.",
    "RATE_LIMIT": "## ⏳ 리뷰 요청이 제한되었습니다\n\nCodex 요청 한도에 도달했습니다.",
    "AUTH": "## 🔐 리뷰를 완료하지 못했습니다\n\n리뷰 엔진 인증을 사용할 수 없습니다.",
    "SERVICE": "## 🌐 리뷰 엔진에 일시적인 문제가 있습니다\n\n잠시 후 다시 시도해 주세요.",
    "INTERNAL": "## ⚠️ 리뷰를 완료하지 못했습니다\n\n내부 오류가 발생했습니다.",
    "SCHEMA": (
        "## 리뷰를 완료하지 못했습니다.\n\n구조화된 리뷰 결과가 출력 계약을 충족하지 않았습니다."
    ),
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
            "CODEX_OUTPUT_SCHEMA_MISMATCH": "SCHEMA",
            "CODEX_OUTPUT_INVALID_JSON": "SCHEMA",
            "CODEX_OUTPUT_MISSING": "SCHEMA",
            "SCHEMA_ERROR": "SCHEMA",
        }.get(error.code, "INTERNAL")
    if isinstance(error, GitHubAPIError):
        if error.category == "GITHUB_RATE_LIMIT" or error.status_code == 429:
            return "RATE_LIMIT"
        if error.status_code in {401, 403}:
            return "AUTH"
        if error.status_code >= 500:
            return "SERVICE"
        return "INTERNAL"
    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
        return "SERVICE"
    return "INTERNAL"


def failure_code(error: Exception) -> str:
    """Return a bounded, actionable code for terminal external failures.

    The previous worker path collapsed every GitHub/transport failure into
    ``EXTERNAL_FAILURE``.  That made an authentication or rate-limit outage
    indistinguishable from a malformed request in the admin view and in the
    user notice.  Only stable categories/statuses are retained; exception
    messages are still redacted at the call site.
    """
    if isinstance(error, GitHubAPIError):
        return error.category[:100]
    if isinstance(error, httpx.TimeoutException):
        return "HTTP_TIMEOUT"
    if isinstance(error, httpx.NetworkError):
        return "HTTP_NETWORK_ERROR"
    if isinstance(error, DiffError):
        return "DIFF_ERROR"
    return "EXTERNAL_FAILURE"


async def publish_failure_notice(
    repository: JobRepository,
    github: GitHubClient,
    job: ReviewJob,
    category: str | ReviewFailure,
    retry_at: datetime | None = None,
) -> None:
    """Publish one safe, user-facing failure notice for a PR head and category."""
    notice_category = category.category if isinstance(category, ReviewFailure) else category
    notice = await repository.get_or_create_failure_notice(job, notice_category)
    if notice.github_comment_id is not None:
        return
    try:
        posted = await github.create_issue_comment(
            job.installation_id,
            job.repository_owner,
            job.repository_name,
            job.pull_request_number,
            (
                category.user_message_ko
                if isinstance(category, ReviewFailure)
                else failure_message(category, retry_at)
            )
            + f"\n\n<!-- inryeok-review-failure:{notice_category.casefold()} -->",
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


def _apply_failure(job: ReviewJob, failure: ReviewFailure) -> None:
    """Copy only the bounded, structured failure contract onto a Job."""
    job.error_code = failure.error_code
    job.error_category = failure.category
    job.error_stage = failure.stage
    job.error_signature = failure.safe_signature
    job.retry_policy = failure.retry_policy
    job.user_action_required = failure.user_action_required
    job.user_error_message = failure.user_message_ko
    job.operator_error_message = failure.operator_message_ko
    job.output_field = failure.output_field
    job.validation_type = failure.validation_type
    job.diagnostic_extraction_failed = failure.diagnostic_extraction_failed
    job.http_status = failure.http_status
    job.codex_exit_code = failure.process_exit_code
    if failure.correlation_id:
        job.correlation_id = failure.correlation_id


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
                # Persist the execution identity before any executor request.
                # A transport failure can leave the executor with a durable
                # result; retaining this id lets the next safe lookup reuse it
                # instead of starting an untraceable duplicate process.
                execution_id = job.execution_id or uuid4().hex
                if job.execution_id is None:
                    job.execution_id = execution_id
                    await session.commit()
                    await session.refresh(job)
                await ReviewService(session, github, runner).execute(job, execution_id)
                await repository.finish(job, JobStatus.SUCCEEDED)
            except ReviewSkipped as exc:
                await finish_after_error(
                    session, repository, job, JobStatus.SKIPPED, "SKIPPED", str(exc)
                )
            except CodexError as exc:
                assert github is not None
                attempts = job.attempts
                await session.rollback()
                failure = failure_from_exception(exc, job=job)
                _apply_failure(job, failure)
                job.codex_exit_code = exc.exit_code
                job.error_signature = exc.signature
                job.correlation_id = exc.correlation_id
                job.stderr_byte_length = exc.stderr_byte_length
                job.redacted_diagnostic = "\n".join(exc.safe_diagnostic)[:2048] or None
                if exc.retryable and attempts < settings.worker_max_attempts:
                    job.status = JobStatus.PENDING
                    job.started_at = None
                    job.error_message = failure.operator_message_ko
                    await session.commit()
                else:
                    await repository.finish(
                        job, JobStatus.FAILED, failure.error_code, failure.operator_message_ko
                    )
                    await publish_failure_notice(repository, github, job, failure, exc.retry_at)
            except (httpx.TimeoutException, httpx.NetworkError, DiffError, GitHubAPIError) as exc:
                assert github is not None
                attempts = job.attempts
                await session.rollback()
                failure = failure_from_exception(exc, job=job)
                _apply_failure(job, failure)
                retryable = failure.retryable
                if retryable and attempts < settings.worker_max_attempts:
                    job.status = JobStatus.PENDING
                    job.started_at = None
                    job.error_message = failure.operator_message_ko
                    await session.commit()
                else:
                    await repository.finish(
                        job, JobStatus.FAILED, failure.error_code, failure.operator_message_ko
                    )
                    await publish_failure_notice(repository, github, job, failure)
            except asyncio.CancelledError:
                await session.rollback()
                job.status = JobStatus.PENDING
                job.started_at = None
                await session.commit()
                raise
            except Exception as exc:
                assert github is not None
                logger.warning("Review job %s failed with an unexpected error", job.id)
                failure = failure_from_exception(exc, job=job)
                _apply_failure(job, failure)
                await finish_after_error(
                    session,
                    repository,
                    job,
                    JobStatus.FAILED,
                    failure.error_code,
                    failure.operator_message_ko,
                )
                await publish_failure_notice(repository, github, job, failure)
            finally:
                if github is not None:
                    await github.http.aclose()


def main() -> None:
    configure_logging()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
