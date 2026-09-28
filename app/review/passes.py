"""Selective orchestration with durable reservations and fail-closed outcomes."""

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.codex.runner import CodexError, ReviewRunner
from app.codex.schemas import Finding
from app.config import Settings
from app.jobs.models import GlobalReviewSettings, ReviewJob, ReviewPass
from app.review.domains import PROMPT_VERSION
from app.review.settings import EffectiveReviewSettings

GENERAL = "GENERAL"

PASS_SIGNALS: dict[str, dict[str, int]] = {
    "SECURITY_PRIVACY": {"SECURITY": 4, "AUTH": 4, "PRIVACY": 4, "LOGGING": 3, "WEBHOOK": 2},
    "EXTERNAL_CONTRACT": {
        "AWS": 3,
        "CLOUDWATCH": 4,
        "WEBHOOK": 2,
        "HTTP_CLIENT": 2,
        "BATCHING": 2,
        "PAGINATION": 2,
        "ENCODING": 2,
    },
    "FAILURE_RELIABILITY": {"EXCEPTION_HANDLING": 4, "RETRY_TIMEOUT": 4, "FILE_IO": 2},
    "DATA_CONCURRENCY": {
        "DATABASE": 3,
        "SQL_JDBC": 3,
        "REDIS": 4,
        "TRANSACTION": 4,
        "CONCURRENCY": 3,
        "CACHE": 2,
    },
    "PERFORMANCE_RESOURCE": {
        "FILE_IO": 3,
        "CACHE": 2,
        "SERIALIZATION": 2,
        "LOGGING": 1,
        "BATCHING": 1,
        "SCHEDULER": 2,
    },
}
PASS_FOCUS = {
    "GENERAL": "correctness, state transitions, changed contracts and concrete test gaps",
    "SECURITY_PRIVACY": "authorization, credentials, personal data, logging and masking",
    "EXTERNAL_CONTRACT": "external API contracts, bytes, batching and pagination",
    "FAILURE_RELIABILITY": "exception chains, retries, timeout outcomes, partial failure and loss",
    "DATA_CONCURRENCY": "database, transactions, cache, locking, ordering and idempotency",
    "PERFORMANCE_RESOURCE": "concrete CPU, regex, memory, I/O and unbounded resource costs",
}
PARTIAL_SAFE_FAILURES = {
    "CODEX_OUTPUT_SCHEMA_MISMATCH",
    "SCHEMA_ERROR",
    "OUTPUT_MODEL_VALIDATION_FAILED",
    "CODEX_OUTPUT_INVALID_JSON",
    "OUTPUT_JSON_INVALID",
    "CODEX_OUTPUT_MISSING",
    "CODEX_QUOTA",
    "CODEX_RATE_LIMIT",
}
UNKNOWN_FAILURES = {
    "EXECUTOR_UNKNOWN_OUTCOME",
    "EXECUTION_IN_PROGRESS",
    "EXECUTION_RESULT_UNAVAILABLE",
    "EXECUTOR_RESULT_NOT_DURABLE",
    "CODEX_TIMEOUT",
    "EXECUTOR_INTERNAL",
}
KNOWN_HARD_FAILURES = {
    "CODEX_AUTH",
    "FILE_PERMISSION_ERROR",
    "CODEX_NOT_FOUND",
    "CODEX_MODEL_NOT_ALLOWED",
    "SCHEMA_DEFINITION_ERROR",
    "EXECUTION_ID_CONFLICT",
    "CODEX_OUTPUT_LIMIT",
}


def plan_passes(signals: tuple[str, ...], enabled: bool, maximum: int) -> tuple[str, ...]:
    if not 1 <= maximum <= 3:
        raise ValueError("invalid pass limit")
    if not enabled or maximum == 1:
        return ("GENERAL",)
    scores = {
        name: sum(weights.get(signal, 0) for signal in set(signals))
        for name, weights in PASS_SIGNALS.items()
    }
    ordered = sorted(scores, key=lambda name: (-scores[name], list(PASS_SIGNALS).index(name)))
    extras = [name for name in ordered if scores[name] >= 2]
    # A third pass is reserved for independently evidenced complex risks.
    count = 2 if len(signals) >= 6 and len(extras) > 1 and scores[extras[1]] >= 4 else 1
    return ("GENERAL", *extras[: min(maximum - 1, count)])


@dataclass(frozen=True)
class MergedReview:
    summary: str
    findings: list[Finding]
    origins: list[str]
    partial: bool


async def execute_passes(
    session: AsyncSession,
    job: ReviewJob,
    settings: Settings,
    effective: EffectiveReviewSettings,
    runner: ReviewRunner,
    checkout: Path,
    prompt: str,
    signals: tuple[str, ...],
    execution_id: str,
) -> MergedReview:
    candidates: list[Finding] = []
    origins: list[str] = []
    summary = ""
    partial = False
    elapsed = 0.0
    if job.started_at is not None:
        start = (
            job.started_at.replace(tzinfo=UTC) if job.started_at.tzinfo is None else job.started_at
        )
        elapsed = max(0.0, (datetime.now(UTC) - start).total_seconds())
    deadline = time.monotonic() + max(0.0, effective.codex_timeout_seconds - elapsed)
    for pass_type in plan_passes(
        signals, settings.selective_multi_pass, settings.review_max_passes
    ):
        pass_prompt = (
            prompt + f"\nReview pass: {pass_type}. Focus: {PASS_FOCUS[pass_type]}. "
            "Keep the same grounding and output contract; zero findings is valid."
        )
        identity = (
            execution_id
            if pass_type == GENERAL
            else hashlib.sha256(f"{execution_id}:{pass_type}".encode()).hexdigest()
        )
        context_hash = hashlib.sha256(pass_prompt.encode()).hexdigest()
        digest = hashlib.sha256(
            "\0".join(
                (
                    pass_type,
                    effective.model or "CLI_DEFAULT",
                    effective.reasoning_effort,
                    PROMPT_VERSION,
                    job.schema_hash or "UNKNOWN",
                    context_hash,
                )
            ).encode()
        ).hexdigest()
        prior = await session.scalar(
            select(ReviewPass).where(ReviewPass.job_id == job.id, ReviewPass.pass_type == pass_type)
        )
        if prior is not None:
            if prior.fingerprint != digest:
                raise CodexError("EXECUTION_ID_CONFLICT", "pass inputs changed")
            if prior.state != "SUCCEEDED":
                raise CodexError(
                    "EXECUTOR_UNKNOWN_OUTCOME", "pass cannot be automatically repeated"
                )
            record = prior
        else:
            # The global singleton serializes admission across worker processes.
            await session.scalar(
                select(GlobalReviewSettings).where(GlobalReviewSettings.id == 1).with_for_update()
            )
            now = datetime.now(UTC)
            day_count = await session.scalar(
                select(func.count())
                .select_from(ReviewPass)
                .where(
                    ReviewPass.started_at >= now - timedelta(days=1), ReviewPass.state != "SKIPPED"
                )
            )
            hour_count = await session.scalar(
                select(func.count())
                .select_from(ReviewPass)
                .where(
                    ReviewPass.started_at >= now - timedelta(hours=1), ReviewPass.state != "SKIPPED"
                )
            )
            remaining = int(deadline - time.monotonic())
            reason = None
            if (
                int(day_count or 0) >= settings.review_processes_per_day
                or int(hour_count or 0) >= settings.review_processes_per_hour
            ):
                reason = "PROCESS_BUDGET"
            elif remaining < 30:
                reason = "JOB_TIMEOUT_BUDGET"
            record = ReviewPass(
                job_id=job.id,
                pass_type=pass_type,
                execution_id=identity,
                fingerprint=digest,
                model=effective.model,
                effort=effective.reasoning_effort,
                state="SKIPPED" if reason else "RESERVED",
                reason=reason,
                started_at=now,
                timeout_seconds=min(
                    effective.codex_timeout_seconds,
                    settings.review_pass_timeout_seconds,
                    max(30, remaining),
                ),
            )
            session.add(record)
            await session.commit()
            if reason:
                if pass_type == GENERAL:
                    raise CodexError("PROCESS_BUDGET_EXCEEDED", "general review budget unavailable")
                partial = True
                continue
        timeout = record.timeout_seconds
        started = time.monotonic()
        try:
            result = await runner.run(
                checkout,
                pass_prompt,
                effective.model,
                timeout,
                identity,
                reasoning_effort=effective.reasoning_effort,
                model_catalog_version=job.model_catalog_version,
                model_verification_id=job.model_verification_id,
            )
        except CodexError as error:
            record.process_count = error.process_count
            record.duration_ms = int((time.monotonic() - started) * 1000)
            safe_partial = error.code in PARTIAL_SAFE_FAILURES
            record.state = (
                "FAILED" if safe_partial or error.code in KNOWN_HARD_FAILURES else "UNKNOWN"
            )
            record.reason = (
                error.code
                if safe_partial or error.code in KNOWN_HARD_FAILURES | UNKNOWN_FAILURES
                else "OPERATOR_REVIEW_REQUIRED"
            )
            await session.commit()
            if pass_type == GENERAL or not safe_partial:
                error.retryable = False
                raise
            partial = True
            continue
        record.state = "SUCCEEDED"
        record.process_count = getattr(runner, "last_process_count", None)
        record.duration_ms = int((time.monotonic() - started) * 1000)
        record.raw_count = len(result.findings)
        await session.commit()
        if pass_type == GENERAL:
            summary = result.summary
        candidates.extend(result.findings)
        origins.extend([pass_type] * len(result.findings))
    return MergedReview(summary, candidates, origins, partial)
