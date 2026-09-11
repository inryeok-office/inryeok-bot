"""Run one bounded model/effort verification through the executor socket.

Only the user-provided model candidate is accepted.  The fixture never calls
GitHub and no model output, prompt, source, or stderr is persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import tarfile
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.codex.schemas import ReviewOutput
from app.config import get_settings
from app.db.session import get_session_factory
from app.review.model_catalog import load_db_catalog
from app.review.model_catalog_store import finish_verification, start_verification

FIXTURE_PROMPT = (
    "Return a valid review result for this verification fixture. "
    "The fixture has no review finding; return findings=[] and a short summary."
)
MODEL_CANDIDATES = frozenset(
    {"gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"}
)


def _archive() -> str:
    with tempfile.TemporaryDirectory(prefix="model-verification-") as name:
        root = Path(name)
        (root / ".git").mkdir()
        (root / "changed.txt").write_text("safe verification fixture\n", encoding="utf-8")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(root.rglob("*")):
                archive.add(path, arcname=path.relative_to(root))
        return base64.b64encode(buffer.getvalue()).decode("ascii")


def _schema_hash() -> str:
    return hashlib.sha256(Path("review-schema.json").read_bytes()).hexdigest()


def _fingerprint(model: str, effort: str, cli_version: str, schema_hash: str) -> str:
    value = "\0".join((model, effort, cli_version, schema_hash, "model-verification-v1"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_error(error_code: str) -> tuple[str, str]:
    mapping = {
        "CODEX_MODEL_NOT_ALLOWED": ("MODEL_NOT_ALLOWED", "model"),
        "CODEX_AUTH": ("AUTHENTICATION_FAILURE", "authentication"),
        "CODEX_QUOTA": ("QUOTA_OR_RATE_LIMIT", "quota"),
        "CODEX_RATE_LIMIT": ("QUOTA_OR_RATE_LIMIT", "rate_limit"),
        "CODEX_TIMEOUT": ("EXECUTOR_TIMEOUT", "timeout"),
        "CODEX_OUTPUT_SCHEMA_MISMATCH": ("OUTPUT_SCHEMA_MISMATCH", "output"),
        "SCHEMA_ERROR": ("OUTPUT_SCHEMA_MISMATCH", "output"),
        "EXECUTOR_RESULT_NOT_DURABLE": ("DURABLE_RESULT_FAILURE", "durable_result"),
        "EXECUTION_RESULT_UNAVAILABLE": ("UNKNOWN_VERIFICATION_OUTCOME", "unknown"),
        "EXECUTOR_UNKNOWN_OUTCOME": ("UNKNOWN_VERIFICATION_OUTCOME", "unknown"),
    }
    return mapping.get(error_code, ("VERIFICATION_ENVIRONMENT_FAILURE", "executor"))


async def _verify(socket: str, model: str, effort: str, execution_id: str, actor: str) -> int:
    settings = get_settings()
    if model not in MODEL_CANDIDATES:
        print(json.dumps({"success": False, "error_code": "MODEL_NOT_ALLOWED"}))
        return 2
    schema_hash = _schema_hash()
    cli_version = settings.codex_cli_version or "unknown"
    fingerprint = _fingerprint(model, effort, cli_version, schema_hash)
    async with get_session_factory()() as session:
        catalog = await load_db_catalog(session)
        if not any(item.model_id == model for item in catalog):
            print(json.dumps({"success": False, "error_code": "MODEL_NOT_ALLOWED", "model": model}))
            return 2
        verification = await start_verification(
            session,
            model_id=model,
            reasoning_effort=effort,
            cli_version=cli_version,
            schema_hash=schema_hash,
            actor=actor,
            reason="operator-approved isolated model verification",
            execution_id=execution_id,
            fingerprint=fingerprint,
        )
        if verification.status == "SUCCEEDED":
            print(json.dumps({"success": True, "reused": True, "model": model, "effort": effort}))
            return 0
        verification_id = verification.verification_id
    payload = {
        "archive": _archive(),
        "prompt": FIXTURE_PROMPT,
        "model": model,
        "reasoning_effort": effort,
        "model_catalog_version": "verification",
        "model_verification_id": verification_id,
        "verification_mode": True,
        "timeout": min(300, settings.review_timeout_seconds),
        "execution_id": execution_id,
    }
    started = time.monotonic()
    error_code: str | None = None
    error_category: str | None = None
    exit_code: int | None = None
    success = False
    diagnostic_failed = False
    try:
        transport = httpx.AsyncHTTPTransport(uds=socket)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://executor", timeout=360
        ) as client:
            response = await client.post("/review", json=payload)
        if response.status_code == 200:
            value = response.json()
            ReviewOutput.model_validate(value)
            success = True
        else:
            body = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            raw_code = body.get("error_code") if isinstance(body, dict) else None
            error_code, error_category = _safe_error(str(raw_code or "EXECUTOR_FAILED"))
            raw_exit = body.get("exit_code") if isinstance(body, dict) else None
            exit_code = raw_exit if isinstance(raw_exit, int) else None
            diagnostic_failed = (
                bool(body.get("diagnostic_extraction_failed")) if isinstance(body, dict) else False
            )
    except (httpx.HTTPError, OSError, TimeoutError, ValidationError, ValueError):
        error_code, error_category = "UNKNOWN_VERIFICATION_OUTCOME", "unknown"
        diagnostic_failed = True
    elapsed = round(time.monotonic() - started, 3)
    async with get_session_factory()() as session:
        await finish_verification(
            session,
            verification_id=verification_id,
            status="SUCCEEDED"
            if success
            else "UNKNOWN"
            if error_code == "UNKNOWN_VERIFICATION_OUTCOME"
            else "FAILED",
            elapsed_seconds=elapsed,
            process_exit_code=exit_code if exit_code is not None else 0 if success else None,
            safe_error_code=error_code,
            safe_error_category=error_category,
            diagnostic_extraction_failed=diagnostic_failed,
            actor=actor,
            reason="operator-approved isolated model verification result",
        )
    print(
        json.dumps(
            {
                "success": success,
                "model": model,
                "effort": effort,
                "execution_id": execution_id,
                "verification_id": verification_id,
                "fingerprint": fingerprint,
                "http_status": 200 if success else None,
                "exit_code": 0 if success else exit_code,
                "elapsed_seconds": elapsed,
                "error_code": error_code,
                "error_category": error_category,
                "diagnostic_extraction_failed": diagnostic_failed,
                "structured_output": success,
                "github_write": 0,
                "fixture_workspace": "temporary_deleted",
            },
            sort_keys=True,
        )
    )
    return 0 if success else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/inryeok-bot/executor.sock")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True, choices=("low", "medium", "high"))
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--actor", default="operator")
    args = parser.parse_args()
    if (
        not args.execution_id.isascii()
        or not args.execution_id.replace("-", "").replace("_", "").isalnum()
    ):
        parser.error("execution id must be filename-safe ASCII")
    raise SystemExit(
        asyncio.run(_verify(args.socket, args.model, args.effort, args.execution_id, args.actor))
    )


if __name__ == "__main__":
    main()
