import asyncio
import json
import os
from pathlib import Path
from typing import Protocol

from app.codex.schemas import ReviewOutput
from app.config import Settings

MAX_PROCESS_OUTPUT = 2_000_000


class CodexError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ReviewRunner(Protocol):
    async def run(self, checkout: Path, prompt: str) -> ReviewOutput: ...


class FakeRunner:
    def __init__(self, output: ReviewOutput) -> None:
        self.output = output

    async def run(self, checkout: Path, prompt: str) -> ReviewOutput:
        return self.output


class CodexRunner:
    _lock = asyncio.Lock()

    def __init__(self, settings: Settings, schema_path: Path = Path("review-schema.json")) -> None:
        self.settings = settings
        self.schema_path = schema_path.resolve()

    async def run(self, checkout: Path, prompt: str) -> ReviewOutput:
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
                )
            except FileNotFoundError as exc:
                raise CodexError("CODEX_NOT_FOUND", "Codex CLI is not installed") from exc
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode()),
                    timeout=self.settings.review_timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise CodexError("CODEX_TIMEOUT", "Codex review timed out", retryable=True) from exc
            except asyncio.CancelledError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                raise
        if len(stdout) > MAX_PROCESS_OUTPUT or len(stderr) > MAX_PROCESS_OUTPUT:
            raise CodexError("CODEX_OUTPUT_LIMIT", "Codex output exceeded the safe limit")
        if process.returncode != 0:
            message = stderr.decode(errors="replace")[-1000:]
            lowered = message.lower()
            if "login" in lowered or "authentication" in lowered:
                raise CodexError("CODEX_AUTH", "Codex CLI is not authenticated")
            raise CodexError("CODEX_FAILED", "Codex CLI exited unsuccessfully", retryable=True)
        try:
            return ReviewOutput.model_validate(json.loads(stdout))
        except (json.JSONDecodeError, ValueError) as exc:
            raise CodexError(
                "CODEX_INVALID_OUTPUT", "Codex returned invalid structured output"
            ) from exc
