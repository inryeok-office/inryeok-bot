"""Minimal internal service that runs Codex without application credentials."""

import asyncio
import base64
import binascii
import io
import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.codex.runner import CodexError, CodexRunner
from app.config import Settings

logger = logging.getLogger(__name__)

MAX_ARCHIVE_BYTES = 25_000_000
MAX_ARCHIVE_FILES = 20_000
MAX_PROMPT_BYTES = 6_000_000


class ReviewRequest(BaseModel):
    archive: str = Field(min_length=1, max_length=35_000_000)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_BYTES)
    model: str | None = Field(default=None, max_length=200)
    timeout: int | None = Field(default=None, ge=30, le=3600)


def _extract_archive(encoded: str, destination: Path) -> None:
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid archive encoding") from exc
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive is too large")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_FILES:
            raise ValueError("archive file limit exceeded")
        root = destination.resolve()
        for member in members:
            name = Path(member.name)
            target = (root / name).resolve()
            if name.is_absolute() or ".." in name.parts or not target.is_relative_to(root):
                raise ValueError("unsafe archive path")
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError("unsupported archive entry")
        archive.extractall(root, filter="data")


app = FastAPI(title="Codex executor")
_execution_lock = asyncio.Lock()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/review", response_model=None)
async def review(request: ReviewRequest) -> dict[str, object] | JSONResponse:
    if _execution_lock.locked():
        raise HTTPException(status_code=429, detail="executor busy")
    async with _execution_lock:
        return await _run_review(request)


async def _run_review(request: ReviewRequest) -> dict[str, object] | JSONResponse:
    correlation_id = uuid.uuid4().hex
    command = os.environ.get("CODEX_COMMAND", "codex")
    home = Path(os.environ.get("CODEX_HOME", "/var/lib/codex"))
    settings = Settings(
        _env_file=None,
        environment="test",
        public_base_url="",
        codex_command=command,
        codex_home=home,
        codex_model_allowlist=os.environ.get("CODEX_MODEL_ALLOWLIST", ""),
        allowed_github_accounts="",
    )
    workspace = Path(tempfile.mkdtemp(prefix="codex-job-", dir="/tmp"))
    try:
        try:
            _extract_archive(request.archive, workspace)
        except ValueError as exc:
            raise HTTPException(status_code=413, detail="unsafe executor input") from exc
        try:
            output = await CodexRunner(settings).run(
                workspace, request.prompt, request.model, request.timeout
            )
        except CodexError as exc:
            status_code = {
                "CODEX_AUTH": 401,
                "CODEX_QUOTA": 429,
                "CODEX_RATE_LIMIT": 429,
                "CODEX_TIMEOUT": 504,
                "CODEX_SERVICE_UNAVAILABLE": 503,
            }.get(exc.code, 500)
            logger.warning(
                "executor review failed correlation=%s category=%s retryable=%s stage=codex_exec",
                correlation_id,
                exc.code,
                exc.retryable,
            )
            return JSONResponse(
                status_code=status_code,
                content={
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                    "correlation_id": correlation_id,
                    "stage": "codex_exec",
                    "error": "codex execution failed",
                },
            )
        except Exception:
            logger.warning(
                "executor review failed correlation=%s "
                "category=EXECUTOR_INTERNAL retryable=false stage=internal",
                correlation_id,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error_code": "EXECUTOR_INTERNAL",
                    "retryable": False,
                    "correlation_id": correlation_id,
                    "stage": "internal",
                    "error": "executor internal error",
                },
            )
        return output.model_dump(mode="json")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
