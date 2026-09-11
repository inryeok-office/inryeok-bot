"""Manage the PostgreSQL-backed Codex model catalog.

The command is intentionally conservative: reads are the default, writes need
``--apply --actor --reason`` and every mutation uses the catalog command
service.  No model discovery or private API is performed here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import get_settings
from app.db.session import get_session_factory
from app.jobs.models import AdminAuditLog, CodexModelCatalog
from app.review.model_catalog import db_catalog_version, load_db_catalog
from app.review.model_catalog_store import add_candidate, finish_verification

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _safe(row: CodexModelCatalog) -> dict[str, object]:
    return {
        "model_id": row.model_id,
        "display_name": row.display_name,
        "purpose_ko": row.purpose_ko,
        "description_ko": row.description_ko,
        "enabled": row.enabled,
        "recommended": row.recommended,
        "availability_status": row.availability_status,
        "source": row.source,
        "supported_efforts": list(row.supported_efforts or []),
        "default_effort": row.default_effort,
        "verified_cli_version": row.verified_cli_version,
        "schema_hash": row.schema_hash,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "verified_by": row.verified_by,
        "failure_code": row.failure_code,
        "failure_message": row.failure_message,
        "verification_id": row.verification_id,
        "version": row.version,
    }


async def _read() -> None:
    async with get_session_factory()() as session:
        rows = (
            await session.scalars(select(CodexModelCatalog).order_by(CodexModelCatalog.model_id))
        ).all()
        print(
            json.dumps(
                {
                    "catalog_version": await db_catalog_version(session),
                    "models": [_safe(row) for row in rows],
                },
                ensure_ascii=False,
            )
        )


async def _add(args: argparse.Namespace) -> None:
    if not args.apply or not args.actor or not args.reason:
        raise SystemExit("add-candidate requires --apply, --actor and --reason")
    async with get_session_factory()() as session:
        row = await add_candidate(
            session,
            model_id=args.model,
            display_name=args.display_name or args.model,
            purpose_ko=args.purpose or "",
            description_ko=args.description or "",
            recommended=args.recommended,
            actor=args.actor,
            reason=args.reason,
            expected_catalog_version=args.expected_catalog_version,
        )
        print(json.dumps({"event": "candidate", "model": _safe(row)}, ensure_ascii=False))


async def _record(args: argparse.Namespace) -> None:
    if not args.apply or not args.actor or not args.reason:
        raise SystemExit("record-verification requires --apply, --actor and --reason")
    if not args.verification_id or not SAFE_ID_RE.fullmatch(args.verification_id):
        raise SystemExit("verification id must be filename-safe ASCII")
    if args.status not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
        raise SystemExit("status must be SUCCEEDED, FAILED, or UNKNOWN")
    async with get_session_factory()() as session:
        row = await finish_verification(
            session,
            verification_id=args.verification_id,
            status=args.status,
            elapsed_seconds=args.elapsed_seconds,
            process_exit_code=args.exit_code,
            safe_error_code=args.error_code,
            safe_error_category=args.error_category,
            diagnostic_extraction_failed=args.diagnostic_extraction_failed,
            actor=args.actor,
            reason=args.reason,
        )
        print(
            json.dumps(
                {
                    "event": "verification_recorded",
                    "verification_id": row.verification_id,
                    "model": row.model_id,
                    "effort": row.reasoning_effort,
                    "status": row.status,
                },
                ensure_ascii=False,
            )
        )


async def _set_default(args: argparse.Namespace) -> None:
    if not args.apply or not args.actor or not args.reason:
        raise SystemExit("set-default requires --apply, --actor and --reason")
    from app.admin.control_plane import update_global_policy
    from app.jobs.models import GlobalReviewSettings

    async with get_session_factory()() as session:
        value = await session.get(GlobalReviewSettings, 1)
        if value is None:
            value = GlobalReviewSettings(id=1, version=1)
            session.add(value)
            await session.flush()
        catalog = await load_db_catalog(session)
        await update_global_policy(
            session,
            patch={
                "model": None if args.model == "CLI_DEFAULT" else args.model,
                "reasoning_effort": args.effort,
            },
            settings=get_settings(),
            actor_login=args.actor,
            reason=args.reason,
            expected_version=value.version,
            catalog=catalog,
        )
        print(json.dumps({"event": "default_updated", "model": args.model, "effort": args.effort}))


async def _disable(args: argparse.Namespace) -> None:
    if not args.apply or not args.actor or not args.reason:
        raise SystemExit("disable/retire requires --apply, --actor and --reason")
    async with get_session_factory()() as session:
        row = await session.scalar(
            select(CodexModelCatalog)
            .where(CodexModelCatalog.model_id == args.model)
            .with_for_update()
        )
        if row is None:
            raise SystemExit("model does not exist")
        old = row.availability_status
        row.enabled = False
        row.availability_status = "RETIRED" if args.command == "retire" else "DISABLED"
        row.failure_code = "OPERATOR_DISABLED"
        row.failure_message = " ".join(args.reason.split())[:300]
        row.version += 1
        session.add(
            AdminAuditLog(
                actor_login=args.actor[:255],
                action="MODEL_RETIRED" if args.command == "retire" else "MODEL_DISABLED",
                target_type="model_catalog",
                target_id=row.model_id,
                summary=f"reason={args.reason[:300]}; old={old}; new={row.availability_status}",
            )
        )
        await session.commit()
        print(json.dumps({"event": args.command, "model": row.model_id}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "list",
            "inspect",
            "validate",
            "add-candidate",
            "record-verification",
            "set-default",
            "disable",
            "retire",
        ),
    )
    parser.add_argument("--model")
    parser.add_argument("--display-name")
    parser.add_argument("--purpose")
    parser.add_argument("--description")
    parser.add_argument("--recommended", action="store_true")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--status", default="SUCCEEDED")
    parser.add_argument("--verification-id")
    parser.add_argument("--elapsed-seconds", type=float)
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--error-code")
    parser.add_argument("--error-category")
    parser.add_argument("--diagnostic-extraction-failed", action="store_true")
    parser.add_argument("--expected-catalog-version")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command in {"add-candidate", "set-default", "disable", "retire"} and args.model:
        if args.model != "CLI_DEFAULT" and not MODEL_ID_RE.fullmatch(args.model):
            raise SystemExit("model must be a safe, non-empty model ID")
    if args.command in {"list", "inspect", "validate"}:
        asyncio.run(_read())
    elif args.command == "add-candidate":
        asyncio.run(_add(args))
    elif args.command == "record-verification":
        asyncio.run(_record(args))
    elif args.command == "set-default":
        asyncio.run(_set_default(args))
    else:
        asyncio.run(_disable(args))


if __name__ == "__main__":
    main()
