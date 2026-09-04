import asyncio
import json
import os
import re
import signal
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from app.codex.schemas import ReviewOutput
from app.config import Settings

MAX_PROCESS_OUTPUT = 2_000_000


def _process_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        process.terminate()
    else:
        killpg = getattr(os, "killpg", None)
        try:
            if callable(killpg):
                killpg(process.pid, getattr(signal, "SIGTERM", 15))
            else:
                process.terminate()
        except (ProcessLookupError, PermissionError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        if os.name != "nt":
            try:
                killpg = getattr(os, "killpg", None)
                if callable(killpg):
                    killpg(process.pid, getattr(signal, "SIGKILL", 9))
                else:
                    process.kill()
            except (ProcessLookupError, PermissionError):
                process.kill()
        else:
            process.kill()
        await process.wait()


class CodexError(RuntimeError):
    def __init__(
        self, code: str, message: str, retryable: bool = False, retry_at: datetime | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_at = retry_at


def _error_text(stdout: bytes, stderr: bytes) -> str:
    """Extract only classifier input; never persist or expose process output."""
    parts = [stdout.decode(errors="replace"), stderr.decode(errors="replace")]
    for payload in list(parts):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            parts.extend(str(value) for value in decoded.values() if isinstance(value, str))
    return "\n".join(parts).casefold()


def _retry_at(text: str) -> datetime | None:
    """Extract a bounded retry time without retaining the diagnostic itself."""
    timestamp = re.search(r"\b(20\d{2}-\d{2}-\d{2}t\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?z)\b", text)
    if timestamp:
        try:
            return datetime.fromisoformat(timestamp.group(1).replace("z", "+00:00")).astimezone(UTC)
        except ValueError:
            pass
    delay = re.search(
        r"(?:retry(?:\s+after)?|reset(?:s)?(?:\s+in)?)\D{0,20}(\d{1,5})\s*(second|minute|hour)",
        text,
    )
    if not delay:
        return None
    amount = int(delay.group(1))
    if amount > 7 * 24 * 60 * 60:
        return None
    seconds = amount * {"second": 1, "minute": 60, "hour": 3600}[delay.group(2)]
    return datetime.now(UTC) + timedelta(seconds=seconds)


def classify_codex_failure(returncode: int, stdout: bytes, stderr: bytes) -> CodexError:
    """Classify known Codex CLI failures without retaining untrusted diagnostics."""
    text = _error_text(stdout, stderr)
    if any(
        value in text for value in ("rate limit", "too many requests", "http 429", "status 429")
    ):
        retry_at = _retry_at(text)
        suffix = f"; retry at {retry_at.isoformat()}" if retry_at else ""
        return CodexError(
            "CODEX_RATE_LIMIT", "Codex request was rate limited" + suffix, retry_at=retry_at
        )
    if any(
        value in text
        for value in (
            "usage limit",
            "usage quota",
            "quota exceeded",
            "plan limit",
            "monthly limit",
            "credit balance",
        )
    ):
        retry_at = _retry_at(text)
        suffix = f"; retry at {retry_at.isoformat()}" if retry_at else ""
        return CodexError(
            "CODEX_QUOTA", "Codex usage limit was reached" + suffix, retry_at=retry_at
        )
    if any(
        value in text
        for value in (
            "not logged in",
            "login required",
            "authentication",
            "unauthorized",
            "session expired",
            "invalid api key",
        )
    ):
        return CodexError("CODEX_AUTH", "Codex CLI is not authenticated")
    if any(
        value in text
        for value in (
            "service unavailable",
            "temporarily unavailable",
            "internal server error",
            "http 502",
            "http 503",
            "http 504",
            "connection reset",
        )
    ):
        return CodexError(
            "CODEX_SERVICE_UNAVAILABLE", "Codex service is temporarily unavailable", True
        )
    return CodexError("CODEX_FAILED", f"Codex CLI exited unsuccessfully ({returncode})", True)


class ReviewRunner(Protocol):
    async def run(
        self, checkout: Path, prompt: str, model: str | None = None, timeout: int | None = None
    ) -> ReviewOutput: ...


class FakeRunner:
    def __init__(self, output: ReviewOutput) -> None:
        self.output = output

    async def run(
        self, checkout: Path, prompt: str, model: str | None = None, timeout: int | None = None
    ) -> ReviewOutput:
        return self.output


class CodexRunner:
    _lock = asyncio.Lock()

    def __init__(self, settings: Settings, schema_path: Path = Path("review-schema.json")) -> None:
        self.settings = settings
        self.schema_path = schema_path.resolve()

    async def run(
        self, checkout: Path, prompt: str, model: str | None = None, timeout: int | None = None
    ) -> ReviewOutput:
        command = [
            self.settings.codex_command,
            "exec",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--color",
            "never",
            "--output-schema",
            str(self.schema_path),
            "-",
        ]
        if model:
            if model not in self.settings.allowed_codex_models:
                raise CodexError("CODEX_MODEL_NOT_ALLOWED", "Codex model is not allowed")
            command[2:2] = ["--model", model]
        safe_environment = {
            "PATH",
            "HOME",
            "USERPROFILE",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        }
        env = {key: value for key, value in os.environ.items() if key.upper() in safe_environment}
        if self.settings.codex_home:
            env["CODEX_HOME"] = str(self.settings.codex_home)
        async with self._lock:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=checkout,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **_process_group_options(),
                )
            except FileNotFoundError as exc:
                raise CodexError("CODEX_NOT_FOUND", "Codex CLI is not installed") from exc
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode()),
                    timeout=timeout or self.settings.review_timeout_seconds,
                )
            except TimeoutError as exc:
                await _stop_process_group(process)
                raise CodexError("CODEX_TIMEOUT", "Codex review timed out", retryable=True) from exc
            except asyncio.CancelledError:
                await _stop_process_group(process)
                raise
        if len(stdout) > MAX_PROCESS_OUTPUT or len(stderr) > MAX_PROCESS_OUTPUT:
            raise CodexError("CODEX_OUTPUT_LIMIT", "Codex output exceeded the safe limit")
        if process.returncode != 0:
            raise classify_codex_failure(process.returncode or 1, stdout, stderr)
        try:
            return ReviewOutput.model_validate(json.loads(stdout))
        except (json.JSONDecodeError, ValueError) as exc:
            raise CodexError(
                "CODEX_INVALID_OUTPUT", "Codex returned invalid structured output"
            ) from exc
