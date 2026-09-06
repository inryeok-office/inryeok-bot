"""Minimal internal service that runs Codex without application credentials."""

import asyncio
import base64
import binascii
import io
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.codex.runner import CodexError, CodexRunner
from app.config import Settings

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
    command = os.environ.get("CODEX_COMMAND", "codex")
    home = Path(os.environ.get("CODEX_HOME", "/var/lib/codex"))
    settings = Settings(
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
            return JSONResponse(
                status_code=502,
                content={"error_code": exc.code, "error": "codex execution failed"},
            )
        return output.model_dump(mode="json")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
