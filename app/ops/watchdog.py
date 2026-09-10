"""Deterministic stale-state inspection and conservative recovery."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.github.webhook import recover_stale_deliveries
from app.jobs.models import JobStatus, ReviewJob, WebhookDelivery
from app.jobs.repository import JobRepository
from app.ops.alerts import record_incident


async def inspect_and_recover(
    session: AsyncSession, settings: Settings, *, apply: bool
) -> dict[str, int | bool]:
    now = datetime.now(UTC)
    webhook_cutoff = now - timedelta(seconds=settings.watchdog_webhook_seconds)
    pending_cutoff = now - timedelta(seconds=settings.watchdog_pending_seconds)
    stale_webhooks = int(
        await session.scalar(
            select(func.count(WebhookDelivery.id)).where(
                WebhookDelivery.status == "PROCESSING",
                WebhookDelivery.processing_started_at < webhook_cutoff,
            )
        )
        or 0
    )
    stale_pending = int(
        await session.scalar(
            select(func.count(ReviewJob.id)).where(
                ReviewJob.status == JobStatus.PENDING,
                ReviewJob.created_at < pending_cutoff,
            )
        )
        or 0
    )
    stale_running = int(
        await session.scalar(
            select(func.count(ReviewJob.id)).where(
                ReviewJob.status == JobStatus.RUNNING,
                ReviewJob.started_at < now - timedelta(seconds=settings.stale_running_seconds),
            )
        )
        or 0
    )
    recovered_deliveries = 0
    recovered_jobs = 0
    if apply:
        reports = await recover_stale_deliveries(
            session, threshold_seconds=settings.watchdog_webhook_seconds, apply=True
        )
        recovered_deliveries = len(reports)
        recovered_jobs = await JobRepository(session).recover_stale(
            settings.stale_running_seconds, settings.worker_max_attempts
        )
        # Do not silently leave a non-paused queue with an ancient pending
        # record.  This is a terminal, auditable failure—not an auto-retry.
        await session.execute(
            update(ReviewJob)
            .where(
                ReviewJob.status == JobStatus.PENDING,
                ReviewJob.created_at < pending_cutoff,
            )
            .values(
                status=JobStatus.FAILED,
                error_code="STALE_PENDING",
                error_message="Pending beyond watchdog threshold",
                retry_policy="NEVER",
                terminal_outcome="FAILED_FINAL",
                finished_at=now,
            )
        )
        await session.commit()
    if stale_webhooks or stale_pending or stale_running:
        await record_incident(
            session,
            settings,
            incident_key="watchdog:stale-queue",
            severity="HIGH",
            summary="stale webhook or review job detected",
            context={"error_code": "STALE_QUEUE"},
        )
    return {
        "apply": apply,
        "stale_webhooks": stale_webhooks,
        "stale_pending": stale_pending,
        "stale_running": stale_running,
        "recovered_deliveries": recovered_deliveries,
        "recovered_jobs": recovered_jobs,
    }
