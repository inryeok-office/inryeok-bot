"""Auditable queue inspection and policy controls; never edits job rows directly."""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.admin.control_plane import set_processing_state
from app.db.session import get_session_factory
from app.jobs.models import (
    AdminAuditLog,
    GlobalReviewSettings,
    JobStatus,
    ReviewJob,
)
from app.jobs.repository import JobRepository


async def audit() -> None:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ReviewJob)
            .where(ReviewJob.status != JobStatus.SUCCEEDED)
            .order_by(ReviewJob.created_at, ReviewJob.id)
        )
        jobs = []
        for job in rows:
            jobs.append(
                {
                    "id": job.id,
                    "repository": f"{job.repository_owner}/{job.repository_name}",
                    "pr": job.pull_request_number,
                    "trigger": job.trigger_type.value,
                    "status": job.status.value,
                    "attempts": job.attempts,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "not_before": job.not_before.isoformat() if job.not_before else None,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "head_sha": job.head_sha[:8],
                    "error_code": job.error_code,
                    "codex_exit_code": job.codex_exit_code,
                    "execution_id": None,
                }
            )
        counts = {status.value: 0 for status in JobStatus}
        all_rows = await session.scalars(select(ReviewJob))
        for job in all_rows:
            counts[job.status.value] += 1
        settings = await session.get(GlobalReviewSettings, 1)
        print(
            json.dumps(
                {
                    "processing_paused": bool(settings and settings.processing_paused),
                    "counts": counts,
                    "claim_order": jobs,
                },
                ensure_ascii=False,
            )
        )


async def set_policy(test_owner: str, test_name: str) -> None:
    """Pause processing without mutating repository policy.

    The old implementation used this maintenance command as a test allowlist
    and rewrote every repository's materialized ``enabled``/``auto_review``
    flags.  That silently erased the effective policy for repositories that
    inherited the global default and was the source of recurring resets after
    maintenance windows.  Queue pause and repository policy are independent;
    test isolation must be enforced by an explicit, temporary deployment
    environment rather than changing production rows.

    Keep the positional arguments for CLI compatibility, but record that they
    are informational only.  No repository rows are loaded or changed.
    """
    async with get_session_factory()() as session:
        global_settings = await session.get(GlobalReviewSettings, 1)
        version = int(global_settings.version if global_settings else 1)
        await set_processing_state(
            session,
            paused=True,
            actor_login="operations",
            reason=f"legacy test target {test_owner}/{test_name} is informational",
            expected_version=version,
        )
        print(
            json.dumps(
                {
                    "processing_paused": True,
                    "test_repository": f"{test_owner}/{test_name}",
                    "repository_policy_changed": False,
                }
            )
        )


async def resume() -> None:
    """Resume claiming after an explicitly audited pause window."""
    async with get_session_factory()() as session:
        global_settings = await session.get(GlobalReviewSettings, 1)
        version = int(global_settings.version if global_settings else 1)
        await set_processing_state(
            session,
            paused=False,
            actor_login="operations",
            reason="manual resume",
            expected_version=version,
        )
        print(json.dumps({"processing_paused": False}))


async def pause() -> None:
    """Pause claiming after an explicitly audited maintenance pause window."""
    async with get_session_factory()() as session:
        global_settings = await session.get(GlobalReviewSettings, 1)
        version = int(global_settings.version if global_settings else 1)
        await set_processing_state(
            session,
            paused=True,
            actor_login="operations",
            reason="maintenance pause",
            expected_version=version,
        )
        print(json.dumps({"processing_paused": True}))


async def skip_job(job_id: int, reason: str) -> None:
    async with get_session_factory()() as session:
        job = await JobRepository(session).skip_pending(job_id, reason)
        if job is None:
            raise ValueError("job is missing or not pending")
        session.add(
            AdminAuditLog(
                actor_login="operations",
                action="SKIP_JOB",
                target_type="review_job",
                target_id=str(job_id),
                summary=f"audited skip: {reason[:80]}",
            )
        )
        await session.commit()
        print(json.dumps({"job_id": job_id, "status": job.status.value, "reason": reason}))


async def activate_repositories(names: list[str]) -> None:
    """Retained only as a compatibility guard; never mutates policy rows."""
    raise ValueError(
        "activate-repositories is retired; use the audited admin policy "
        "command with an explicit override"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    sub.add_parser("resume")
    sub.add_parser("pause")
    policy = sub.add_parser("pause-test")
    policy.add_argument("owner")
    policy.add_argument("name")
    activate = sub.add_parser("activate-repositories")
    activate.add_argument("repositories", nargs="+", metavar="OWNER/REPOSITORY")
    skip = sub.add_parser("skip-job")
    skip.add_argument("job_id", type=int)
    skip.add_argument(
        "reason",
        choices=[
            "STALE_HEAD",
            "PR_CLOSED",
            "PR_MERGED",
            "PR_DRAFT",
            "INSTALLATION_INACTIVE",
            "REPOSITORY_NOT_ACCESSIBLE",
            "POLICY_DISABLED",
            "DUPLICATE_REVIEW_IDENTITY",
        ],
    )
    args = parser.parse_args()
    if args.command == "audit":
        asyncio.run(audit())
    elif args.command == "pause-test":
        asyncio.run(set_policy(args.owner, args.name))
    elif args.command == "activate-repositories":
        asyncio.run(activate_repositories(args.repositories))
    elif args.command == "pause":
        asyncio.run(pause())
    elif args.command == "skip-job":
        asyncio.run(skip_job(args.job_id, args.reason))
    else:
        asyncio.run(resume())


if __name__ == "__main__":
    main()
