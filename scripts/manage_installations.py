"""Inspect and reconcile GitHub App installations without changing policy."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import get_settings
from app.db.session import get_session_factory
from app.github.client import GitHubClient
from app.jobs.models import AdminAuditLog, GitHubInstallation, RepositorySettings


async def reconcile(apply: bool) -> None:
    settings = get_settings()
    github = GitHubClient(settings)
    async with get_session_factory()() as session:
        installations = list((await session.scalars(select(GitHubInstallation))).all())
        result = {
            "installations": 0,
            "repositories": 0,
            "new_repositories": 0,
            "access_updates": 0,
            "policy_changes": 0,
            "override_changes": 0,
            "deletes": 0,
            "apply": apply,
        }
        for installation in installations:
            result["installations"] += 1
            try:
                remote = await github.list_installation_repositories(
                    installation.github_installation_id
                )
            except Exception as exc:  # safe metadata only
                print(
                    json.dumps(
                        {
                            "installation": installation.github_installation_id,
                            "status": "ERROR",
                            "category": type(exc).__name__,
                        }
                    )
                )
                continue
            installation.status = "ACTIVE"
            installation.last_synced_at = datetime.now(UTC)
            for item in remote:
                full_name = str(item.get("full_name", ""))
                if "/" not in full_name:
                    continue
                owner, name = full_name.split("/", 1)
                repo = await session.scalar(
                    select(RepositorySettings).where(
                        RepositorySettings.installation_id == installation.github_installation_id,
                        RepositorySettings.repository_owner.ilike(owner),
                        RepositorySettings.repository_name.ilike(name),
                    )
                )
                result["repositories"] += 1
                if repo is None:
                    result["new_repositories"] += 1
                    if apply:
                        repo = RepositorySettings(
                            installation_id=installation.github_installation_id,
                            installation_fk_id=installation.id,
                            github_repository_id=item.get("id"),
                            repository_owner=owner.casefold(),
                            repository_name=name.casefold(),
                            installed=True,
                            enabled=True,
                            auto_review=True,
                            min_confidence=settings.default_min_confidence,
                            max_findings=settings.default_max_findings,
                            include_low_severity=settings.default_include_low_severity,
                            ignore_draft=settings.default_ignore_draft,
                            ignore_patterns=settings.default_ignore_patterns,
                        )
                        session.add(repo)
                elif apply:
                    repo.installed = True
                    repo.installation_fk_id = installation.id
                    if item.get("id") is not None:
                        repo.github_repository_id = int(item["id"])
            if apply:
                session.add(
                    AdminAuditLog(
                        actor_login="reconciliation",
                        action="INSTALLATION_RECONCILIATION",
                        target_type="installation",
                        target_id=str(installation.github_installation_id),
                        summary=(
                            "synchronized installation metadata and repository access; "
                            "policy unchanged"
                        ),
                    )
                )
        if apply:
            await session.commit()
        print(json.dumps(result, ensure_ascii=False))


async def inspect(account: str | None) -> None:
    async with get_session_factory()() as session:
        rows = list((await session.scalars(select(GitHubInstallation))).all())
        if account:
            rows = [r for r in rows if (r.account_login or "").casefold() == account.casefold()]
        print(
            json.dumps(
                {
                    "count": len(rows),
                    "installations": [
                        {
                            "id": r.github_installation_id,
                            "account": r.account_login,
                            "status": str(r.status),
                            "version": r.version,
                        }
                        for r in rows
                    ],
                },
                ensure_ascii=False,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("reconcile")
    rec.add_argument("--apply", action="store_true")
    ins = sub.add_parser("inspect")
    ins.add_argument("--account")
    args = parser.parse_args()
    if args.command == "reconcile":
        asyncio.run(reconcile(args.apply))
    else:
        asyncio.run(inspect(args.account))


if __name__ == "__main__":
    main()
