"""Auditable queue inspection and policy controls; never edits job rows directly."""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.db.session import get_session_factory
from app.jobs.models import (
    AdminAuditLog,
    GlobalReviewSettings,
    JobStatus,
    RepositorySettings,
    ReviewJob,
)


async def audit() -> None:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ReviewJob)
            .where(ReviewJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]))
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
    async with get_session_factory()() as session:
        global_settings = await session.get(GlobalReviewSettings, 1)
        if global_settings is None:
            global_settings = GlobalReviewSettings(id=1)
            session.add(global_settings)
        global_settings.processing_paused = True
        repos = list(await session.scalars(select(RepositorySettings)))
        for repository in repos:
            allowed = (
                repository.repository_owner.casefold() == test_owner.casefold()
                and repository.repository_name.casefold() == test_name.casefold()
            )
            repository.enabled = allowed
            repository.auto_review = False
        session.add(
            AdminAuditLog(
                actor_login="operations",
                action="QUEUE_POLICY",
                target_type="global_settings",
                target_id="1",
                summary=(
                    f"processing_paused=true; only {test_owner}/{test_name} enabled; "
                    "automatic review disabled"
                ),
            )
        )
        await session.commit()
        print(
            json.dumps(
                {
                    "processing_paused": True,
                    "test_repository": f"{test_owner}/{test_name}",
                    "automatic_review": False,
                }
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    policy = sub.add_parser("pause-test")
    policy.add_argument("owner")
    policy.add_argument("name")
    args = parser.parse_args()
    if args.command == "audit":
        asyncio.run(audit())
    else:
        asyncio.run(set_policy(args.owner, args.name))


if __name__ == "__main__":
    main()
