"""Safely inspect and update the operator-managed model catalog.

Writes require ``--apply``, an actor and a reason, and only touch the
explicit catalog file.  Verification evidence is recorded only through
``record-verification`` after the separate executor harness succeeds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import get_session_factory
from app.jobs.models import AdminAuditLog
from app.review.model_catalog import VALID_EFFORTS, load_catalog

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _catalog_path(argument: str | None) -> Path:
    path = argument or get_settings().codex_model_catalog_file
    if path is None:
        raise ValueError("catalog file is not configured; pass --catalog-file")
    return path


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("catalog file must contain a JSON array of objects")
    return [dict(item) for item in value]


def _load_specs(path_argument: str | None):
    settings = get_settings()
    if path_argument:
        settings.codex_model_catalog_json = Path(path_argument).read_text(encoding="utf-8").strip()
    return settings, load_catalog(settings)


def _write(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(values, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def _record_audit(
    *, action: str, model_id: str, actor: str, reason: str, verification_id: str | None = None
) -> None:
    summary = f"model={model_id}; reason={' '.join(reason.split())[:240]}"
    if verification_id:
        summary += f"; verification_id={verification_id}"
    async with get_session_factory()() as session:
        session.add(
            AdminAuditLog(
                actor_login=actor[:255],
                action=action[:100],
                target_type="model_catalog",
                target_id=model_id[:128],
                summary=summary,
            )
        )
        await session.commit()


def _audit(
    *, action: str, model_id: str, actor: str, reason: str, verification_id: str | None = None
) -> None:
    try:
        asyncio.run(
            _record_audit(
                action=action,
                model_id=model_id,
                actor=actor,
                reason=reason,
                verification_id=verification_id,
            )
        )
    except Exception as exc:
        raise SystemExit("catalog audit could not be recorded") from exc


def _safe_spec(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_id": item.get("model_id"),
        "display_name": item.get("display_name"),
        "supported_efforts": item.get("supported_efforts"),
        "default_effort": item.get("default_effort"),
        "enabled": item.get("enabled"),
        "availability_status": item.get("availability_status"),
        "verified_cli_version": item.get("verified_cli_version"),
        "version": item.get("version"),
        "source": item.get("source"),
        "schema_hash": item.get("schema_hash"),
        "verified_at": item.get("verified_at"),
        "verified_by": item.get("verified_by"),
        "failure_code": item.get("failure_code"),
        "verification_id": item.get("verification_id"),
    }


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
            "disable",
            "retire",
        ),
    )
    parser.add_argument("--catalog-file")
    parser.add_argument("--model")
    parser.add_argument("--efforts", default="medium")
    parser.add_argument("--default-effort", default="medium")
    parser.add_argument("--cli-version")
    parser.add_argument("--schema-hash")
    parser.add_argument("--verification-id")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command in {"list", "inspect", "validate"}:
        settings, specs = _load_specs(args.catalog_file)
        print(
            json.dumps(
                {
                    "valid": True,
                    "catalog_file_configured": (
                        settings.codex_model_catalog_file is not None
                        or args.catalog_file is not None
                    ),
                    "count": len(specs),
                    "models": [_safe_spec(item) for item in specs],
                },
                ensure_ascii=False,
            )
        )
        return

    path = _catalog_path(args.catalog_file)
    values = _read(path)
    if not args.apply:
        raise SystemExit("write commands require --apply")
    if not args.actor or not args.reason:
        raise SystemExit("write commands require --actor and --reason")
    if not args.model or not MODEL_ID_RE.fullmatch(args.model):
        raise SystemExit("model must be a safe, non-empty model ID")
    if args.model == "CLI_DEFAULT":
        raise SystemExit("CLI_DEFAULT is represented by a null setting, not a catalog row")
    existing = next((item for item in values if item.get("model_id") == args.model), None)

    if args.command == "add-candidate":
        efforts = tuple(dict.fromkeys(item.strip().lower() for item in args.efforts.split(",")))
        if not efforts or not set(efforts) <= VALID_EFFORTS:
            raise SystemExit("efforts must be default, low, medium, or high")
        if existing is None:
            values.append(
                {
                    "model_id": args.model,
                    "display_name": args.model,
                    "description_ko": "운영자 검증 대기 모델",
                    "supported_efforts": list(efforts),
                    "default_effort": args.default_effort,
                    "enabled": False,
                    "recommended": False,
                    "availability_status": "UNVERIFIED",
                    "source": "OPERATOR",
                    "version": "1",
                }
            )
        _write(path, values)
        _audit(
            action="MODEL_CANDIDATE_ADDED",
            model_id=args.model,
            actor=args.actor,
            reason=args.reason,
        )
        print(json.dumps({"event": "candidate_added", "model_id": args.model, "actor": args.actor}))
        return

    if existing is None:
        raise SystemExit("model candidate does not exist")
    if args.command == "record-verification":
        if (
            not args.cli_version
            or not args.schema_hash
            or not args.verification_id
            or not SAFE_ID_RE.fullmatch(args.verification_id)
        ):
            raise SystemExit(
                "record-verification requires cli version, schema hash, and verification id"
            )
        existing.update(
            {
                "enabled": True,
                "availability_status": "VERIFIED",
                "verified_cli_version": args.cli_version,
                "schema_hash": args.schema_hash,
                "verified_at": datetime.now(UTC).isoformat(),
                "verified_by": args.actor,
                "verification_id": args.verification_id,
                "failure_code": None,
                "failure_message": None,
            }
        )
        _write(path, values)
        _audit(
            action="MODEL_VERIFICATION_RECORDED",
            model_id=args.model,
            actor=args.actor,
            reason=args.reason,
            verification_id=args.verification_id,
        )
        print(
            json.dumps(
                {"event": "verification_recorded", "model_id": args.model, "actor": args.actor}
            )
        )
        return

    existing["enabled"] = False
    existing["availability_status"] = "RETIRED" if args.command == "retire" else "UNAVAILABLE"
    existing["failure_code"] = "OPERATOR_DISABLED"
    existing["failure_message"] = args.reason[:300]
    _write(path, values)
    _audit(
        action="MODEL_RETIRED" if args.command == "retire" else "MODEL_DISABLED",
        model_id=args.model,
        actor=args.actor,
        reason=args.reason,
    )
    print(json.dumps({"event": args.command, "model_id": args.model, "actor": args.actor}))


if __name__ == "__main__":
    main()
