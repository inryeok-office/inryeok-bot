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
        self,
        checkout: Path,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        execution_id: str | None = None,
        reasoning_effort: str | None = None,
    ) -> ReviewOutput:
        archive = _archive_workspace(checkout)
        payload = {
            "archive": base64.b64encode(archive).decode("ascii"),
            "prompt": prompt,
            "model": model,
            "timeout": timeout,
            "execution_id": execution_id,
            "reasoning_effort": reasoning_effort,
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
            raise CodexError("CODEX_TIMEOUT", "Codex executor timed out") from exc
        except httpx.HTTPError as exc:
            raise CodexError(
                "EXECUTOR_UNKNOWN_OUTCOME", "Codex executor result is unknown"
            ) from exc
        if response.status_code >= 400:
            body: dict[str, object] = {}
            try:
                body = response.json()
                error = str(body.get("error_code", "EXECUTOR_INTERNAL"))
                retryable = bool(body.get("retryable", False))
            except ValueError:
                error = "EXECUTOR_FAILED"
                retryable = False
            codex_error = CodexError(
                error,
                "Codex executor rejected the review",
                retryable=retryable,
                signature=str(body.get("matched_safe_signature", error)),
            )
            raw_exit_code = body.get("exit_code")
            if isinstance(raw_exit_code, int):
                codex_error.exit_code = raw_exit_code
            raw_stderr_length = body.get("stderr_byte_length")
            if isinstance(raw_stderr_length, int):
                codex_error.stderr_byte_length = raw_stderr_length
            raw_correlation = body.get("correlation_id")
            if isinstance(raw_correlation, str):
                codex_error.correlation_id = raw_correlation
            raw_stage = body.get("stage")
            if isinstance(raw_stage, str):
                codex_error.stage = raw_stage
            raw_diagnostic = body.get("safe_diagnostic")
            if isinstance(raw_diagnostic, list):
                codex_error.safe_diagnostic = tuple(
                    item for item in raw_diagnostic if isinstance(item, str)
                )
            raise codex_error
        try:
            return ReviewOutput.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise CodexError(
                "CODEX_OUTPUT_SCHEMA", "Codex executor returned invalid output"
            ) from exc
