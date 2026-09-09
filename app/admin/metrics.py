"""Read-only usage metrics for the administrator console.

The metrics deliberately use persisted Job and ReviewRun fields only.  No
token or model-cost estimates are inferred when the underlying data is not
available.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import JobStatus, ReviewJob, ReviewRun


@dataclass(frozen=True)
class MetricRow:
    label: str
    value: int


@dataclass(frozen=True)
class UsageMetrics:
    period_days: int
    since: datetime
    total_jobs: int
    succeeded: int
    failed: int
    skipped: int
    pending: int
    running: int
    success_rate: float | None
    average_duration_seconds: float | None
    p50_duration_seconds: float | None
    p95_duration_seconds: float | None
    by_repository: tuple[MetricRow, ...]
    by_trigger: tuple[MetricRow, ...]
    by_error: tuple[MetricRow, ...]
    raw_findings: int
    schema_valid_findings: int
    evidence_findings: int
    published_findings: int
    rejected_findings: int
    summary_fallbacks: int


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


async def usage_metrics(session: AsyncSession, period_days: int = 1) -> UsageMetrics:
    """Build bounded, read-only metrics for the selected period.

    ``period_days`` is intentionally constrained at the route boundary and
    again here so callers cannot accidentally request an unbounded report.
    """
    days = period_days if period_days in {1, 7, 30} else 1
    since = datetime.now(UTC) - timedelta(days=days)
    jobs = list(
        (
            await session.scalars(
                select(ReviewJob)
                .where(ReviewJob.created_at >= since)
                .order_by(ReviewJob.created_at.desc())
            )
        ).all()
    )

    counts = {status: sum(job.status == status for job in jobs) for status in JobStatus}
    terminal = counts[JobStatus.SUCCEEDED] + counts[JobStatus.FAILED] + counts[JobStatus.SKIPPED]
    success_rate = (counts[JobStatus.SUCCEEDED] / terminal * 100) if terminal else None
    durations = [
        (job.finished_at - job.started_at).total_seconds()
        for job in jobs
        if job.started_at is not None
        and job.finished_at is not None
        and job.finished_at >= job.started_at
    ]

    repository_counts: dict[str, int] = {}
    trigger_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    for job in jobs:
        repository = f"{job.repository_owner}/{job.repository_name}"
        repository_counts[repository] = repository_counts.get(repository, 0) + 1
        trigger = job.trigger_type.value
        trigger_counts[trigger] = trigger_counts.get(trigger, 0) + 1
        if job.error_code:
            error_counts[job.error_code] = error_counts.get(job.error_code, 0) + 1

    runs = list(
        (
            await session.scalars(
                select(ReviewRun).join(ReviewJob).where(ReviewJob.created_at >= since)
            )
        ).all()
    )
    raw = sum(run.raw_findings_count or 0 for run in runs)
    schema_valid = sum(run.schema_valid_findings_count or 0 for run in runs)
    evidence = sum(run.evidence_findings_count or 0 for run in runs)
    published = sum(run.published_findings_count or 0 for run in runs)
    rejected = max(0, raw - published)
    # The current schema stores summary fallback as a rejection reason.  Keep
    # this conservative: unknown/missing data is not counted as a fallback.
    fallback = sum(
        1 for run in runs if run.rejection_counts and "SUMMARY_FALLBACK" in run.rejection_counts
    )

    def row_sort(item: tuple[str, int]) -> tuple[int, str]:
        return (-item[1], item[0])

    return UsageMetrics(
        period_days=days,
        since=since,
        total_jobs=len(jobs),
        succeeded=counts[JobStatus.SUCCEEDED],
        failed=counts[JobStatus.FAILED],
        skipped=counts[JobStatus.SKIPPED],
        pending=counts[JobStatus.PENDING],
        running=counts[JobStatus.RUNNING],
        success_rate=success_rate,
        average_duration_seconds=(sum(durations) / len(durations)) if durations else None,
        p50_duration_seconds=_percentile(durations, 0.50),
        p95_duration_seconds=_percentile(durations, 0.95),
        by_repository=tuple(
            MetricRow(*item) for item in sorted(repository_counts.items(), key=row_sort)[:10]
        ),
        by_trigger=tuple(MetricRow(*item) for item in sorted(trigger_counts.items(), key=row_sort)),
        by_error=tuple(
            MetricRow(*item) for item in sorted(error_counts.items(), key=row_sort)[:10]
        ),
        raw_findings=raw,
        schema_valid_findings=schema_valid,
        evidence_findings=evidence,
        published_findings=published,
        rejected_findings=rejected,
        summary_fallbacks=fallback,
    )
