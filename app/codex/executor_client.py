"""Client for the isolated Codex executor service."""

import base64
import io
import tarfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from app.codex.runner import CodexError, ReviewRunner
from app.codex.schemas import ReviewOutput

MAX_ARCHIVE_BYTES = 25_000_000
MAX_ARCHIVE_FILES = 20_000


def _archive_workspace(checkout: Path) -> bytes:
    """Create a safe, source-only archive without .git or symlinks."""
    root = checkout.resolve()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        count = 0
        for candidate in root.rglob("*"):
            relative = candidate.relative_to(root)
            if ".git" in relative.parts:
                continue
            if candidate.is_symlink():
                raise CodexError("EXECUTOR_UNSAFE_WORKSPACE", "workspace contains a symlink")
            if not candidate.is_file():
                continue
            count += 1
            if count > MAX_ARCHIVE_FILES:
                raise CodexError("EXECUTOR_INPUT_LIMIT", "workspace file limit exceeded")
            archive.add(candidate, arcname=relative.as_posix(), recursive=False)
    payload = output.getvalue()
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise CodexError("EXECUTOR_INPUT_LIMIT", "workspace archive is too large")
    return payload


class ExecutorRunner(ReviewRunner):
    def __init__(self, url: str, timeout: float = 960.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    async def run(
        self, checkout: Path, prompt: str, model: str | None = None, timeout: int | None = None
    ) -> ReviewOutput:
        archive = _archive_workspace(checkout)
        payload = {
            "archive": base64.b64encode(archive).decode("ascii"),
            "prompt": prompt,
            "model": model,
            "timeout": timeout,
        }
        request_timeout = max(self.timeout, float(timeout or 0) + 30.0)
        try:
            if self.url.startswith("unix://"):
                socket_path = unquote(urlparse(self.url).path)
                if not socket_path.startswith("/"):
                    raise CodexError(
                        "EXECUTOR_UNAVAILABLE", "executor socket path is invalid", True
                    )
                transport = httpx.AsyncHTTPTransport(uds=socket_path)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://executor", timeout=request_timeout
                ) as client:
                    response = await client.post("/review", json=payload)
            else:
                async with httpx.AsyncClient(timeout=request_timeout) as client:
                    response = await client.post(f"{self.url}/review", json=payload)
        except httpx.TimeoutException as exc:
            raise CodexError("CODEX_TIMEOUT", "Codex executor timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise CodexError("EXECUTOR_UNAVAILABLE", "Codex executor is unavailable", True) from exc
        if response.status_code >= 400:
            try:
                error = response.json().get("error_code", "EXECUTOR_FAILED")
            except ValueError:
                error = "EXECUTOR_FAILED"
            raise CodexError(str(error), "Codex executor rejected the review", retryable=True)
        try:
            return ReviewOutput.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise CodexError(
                "CODEX_OUTPUT_SCHEMA", "Codex executor returned invalid output"
            ) from exc
