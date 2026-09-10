"""Safe operational incident recording and optional notification."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.jobs.models import OpsIncident

logger = logging.getLogger(__name__)


def _safe_context(values: Mapping[str, object]) -> str:
    allowed = {
        key: str(value)[:200]
        for key, value in values.items()
        if key in {"job_id", "repository", "pr", "execution_id", "correlation_id", "error_code"}
    }
    return json.dumps(allowed, ensure_ascii=False, sort_keys=True)


async def record_incident(
    session: AsyncSession,
    settings: Settings,
    *,
    incident_key: str,
    severity: str,
    summary: str,
    context: Mapping[str, object] | None = None,
) -> OpsIncident:
    """Upsert an incident and notify at most once per cooldown.

    A missing or broken notifier is deliberately non-fatal to the caller.  No
    secret, prompt, source, stdout, or stderr is ever persisted.
    """
    now = datetime.now(UTC)
    incident = await session.scalar(
        select(OpsIncident).where(OpsIncident.incident_key == incident_key).with_for_update()
    )
    if incident is None:
        incident = OpsIncident(
            incident_key=incident_key[:180],
            severity=severity[:16].upper(),
            safe_summary=summary[:500],
            safe_context=_safe_context(context or {}),
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(incident)
        await session.flush()
    else:
        incident.last_seen_at = now
        incident.occurrence_count += 1
        incident.status = "OPEN"
        incident.resolved_at = None
    last_notified = incident.last_notified_at
    if last_notified is not None and last_notified.tzinfo is None:
        last_notified = last_notified.replace(tzinfo=UTC)
    should_notify = last_notified is None or now - last_notified >= timedelta(
        seconds=settings.ops_alert_cooldown_seconds
    )
    if should_notify:
        incident.last_notified_at = now
        webhook = settings.ops_alert_webhook_url.get_secret_value()
        if webhook:
            payload = {
                "content": f"[{severity.upper()}] {summary[:500]}",
                "allowed_mentions": {"parse": []},
            }
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(webhook, json=payload)
                    response.raise_for_status()
            except Exception:
                # Incident state remains durable; notifier failure is itself
                # observable through the next watchdog/admin inspection.
                logger.warning("operational notifier failed for incident %s", incident_key)
    await session.commit()
    return incident


async def resolve_incident(session: AsyncSession, incident_key: str) -> None:
    incident = await session.scalar(
        select(OpsIncident).where(OpsIncident.incident_key == incident_key).with_for_update()
    )
    if incident is None:
        return
    incident.status = "RESOLVED"
    incident.resolved_at = datetime.now(UTC)
    await session.commit()
