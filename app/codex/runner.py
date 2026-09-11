import asyncio
import json
import os
import re
import signal
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from app.codex.schemas import ReviewOutput
from app.config import Settings
from app.review.model_catalog import validate_model_effort

MAX_PROCESS_OUTPUT = 2_000_000
MAX_CAPTURE_BYTES = 16_000
MAX_SAFE_DIAGNOSTIC_BYTES = 2_048
MAX_SAFE_DIAGNOSTIC_LINES = 10
MAX_SAFE_DIAGNOSTIC_LINE = 300
SCHEMA_DIAGNOSTIC_FALLBACK = "output_field=__root__;type=schema_validation_unknown"


def _validation_diagnostic(error: ValidationError) -> tuple[str, ...]:
    """Return schema failure locations without retaining model output values."""
    diagnostics: list[str] = []
    for item in error.errors(include_url=False, include_context=False):
        location = ".".join(str(part) for part in item.get("loc", ())) or "$"
        error_type = str(item.get("type", "validation_error"))
        diagnostics.append(f"output_field={location};type={error_type}")
        if len(diagnostics) >= MAX_SAFE_DIAGNOSTIC_LINES:
            break
    return tuple(diagnostics)


def normalize_schema_diagnostic(
    diagnostic: tuple[str, ...] | list[str] | None,
) -> tuple[tuple[str, ...], bool]:
    """Keep schema diagnostics typed and guarantee a safe bounded fallback."""
    values = tuple(
        item[:MAX_SAFE_DIAGNOSTIC_LINE]
        for item in (diagnostic or ())
        if isinstance(item, str) and item.startswith("output_field=") and ";type=" in item
    )[:MAX_SAFE_DIAGNOSTIC_LINES]
    if values:
        return values, False
    return (SCHEMA_DIAGNOSTIC_FALLBACK,), True


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
        self,
        code: str,
        message: str,
        retryable: bool = False,
        retry_at: datetime | None = None,
        signature: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_at = retry_at
        self.signature = signature or code
        self.exit_code: int | None = None
        self.safe_diagnostic: tuple[str, ...] = ()
        self.diagnostic_extraction_failed = False
        self.stderr_byte_length = 0
        self.correlation_id: str | None = None
        self.stage = "codex_exec"


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


def _parse_structured_output(stdout: bytes) -> object:
    """Parse JSON while accepting one complete Markdown JSON fence.

    Some CLI/model combinations wrap otherwise valid structured output in a
    single Markdown fence. This narrow normalization does not extract JSON
    from surrounding prose and still leaves contract validation to Pydantic.
    """
    text = stdout.decode("utf-8")
    candidate = text.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        first_line, separator, body = candidate.partition("\n")
        if separator and first_line[3:].strip().casefold() in {"", "json"}:
            candidate = body[:-3].rstrip()
    return json.loads(candidate)


def redact_diagnostic(
    stdout: bytes,
    stderr: bytes,
    sensitive_values: tuple[str, ...] = (),
    sensitive_paths: tuple[Path, ...] = (),
) -> tuple[str, ...]:
    """Return bounded operator diagnostics without credentials or raw payloads."""
    captured = (stdout[:MAX_CAPTURE_BYTES] + b"\n" + stderr[:MAX_CAPTURE_BYTES])[:MAX_CAPTURE_BYTES]
    text = captured.decode(errors="replace")
    text = re.sub(
        r"-----BEGIN[A-Z0-9 _-]*-----.*?-----END[A-Z0-9 _-]*-----",
        "[PEM_REDACTED]",
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"(?i)(authorization)\s*[:=]\s*bearer\s+\S+", r"\1=[REDACTED]", text)
    text = re.sub(
        r"(?i)(authorization|cookie|token|password|secret|api[_-]?key)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(postgres(?:ql)?(?:\+\w+)?://)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)([^\s/@]+):([^\s/@]+)@", r"\1[REDACTED]@", text)
    for value in sensitive_values:
        if value:
            text = text.replace(value[:512], "[REDACTED]")
    for path in sensitive_paths:
        text = text.replace(str(path), "[PATH_REDACTED]")
    text = re.sub(r"(?:[A-Za-z]:\\|/)(?:[^\s'\"`]|\\ )+", "[PATH_REDACTED]", text)
    text = "".join(char if char in "\n\t" or ord(char) >= 32 else " " for char in text)
    lines: list[str] = []
    used = 0
    for line in text.splitlines():
        safe = line[:MAX_SAFE_DIAGNOSTIC_LINE]
        if used + len(safe) > MAX_SAFE_DIAGNOSTIC_BYTES:
            break
        lines.append(safe)
        used += len(safe)
        if len(lines) == MAX_SAFE_DIAGNOSTIC_LINES:
            break
    return tuple(lines)


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
            "CODEX_RATE_LIMIT",
            "Codex request was rate limited" + suffix,
            retryable=True,
            retry_at=retry_at,
            signature="rate_limit",
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
            "CODEX_QUOTA",
            "Codex usage limit was reached" + suffix,
            retry_at=retry_at,
            signature="quota",
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
        return CodexError(
            "CODEX_AUTH", "Codex CLI is not authenticated", signature="authentication"
        )
    if any(value in text for value in ("permission denied", "operation not permitted")):
        return CodexError(
            "FILE_PERMISSION_ERROR",
            "Codex execution permission denied",
            signature="permission_denied",
        )
    if any(
        value in text
        for value in (
            "unsupported option",
            "unknown option",
            "unexpected argument",
            "invalid config",
        )
    ):
        return CodexError(
            "CLI_ARGUMENT_ERROR",
            "Codex CLI configuration is unsupported",
            signature="configuration",
        )
    if any(
        value in text for value in ("not a git repository", "repository check", "fatal: not a git")
    ):
        return CodexError(
            "GIT_REPOSITORY_ERROR",
            "Codex workspace repository check failed",
            signature="repository",
        )
    if any(
        value in text for value in ("schema", "invalid json", "json parse", "structured output")
    ):
        return CodexError(
            "SCHEMA_ERROR", "Codex output schema validation failed", signature="schema"
        )
    if any(
        value in text
        for value in ("bwrap", "bubblewrap", "user namespace", "pivot_root", "sandbox")
    ):
        return CodexError(
            "SANDBOX_START_ERROR", "Codex sandbox could not start", signature="sandbox"
        )
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
            "CODEX_SERVICE_UNAVAILABLE",
            "Codex service is temporarily unavailable",
            True,
            signature="service_unavailable",
        )
    return CodexError(
        "CODEX_EXIT_NONZERO", "Codex CLI exited unsuccessfully", signature="unknown_nonzero"
    )


class ReviewRunner(Protocol):
    async def run(
        self,
        checkout: Path,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        execution_id: str | None = None,
        reasoning_effort: str | None = None,
        model_catalog_version: str | None = None,
        model_verification_id: str | None = None,
    ) -> ReviewOutput: ...


class FakeRunner:
    def __init__(self, output: ReviewOutput) -> None:
        self.output = output

    async def run(
        self,
        checkout: Path,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        execution_id: str | None = None,
        reasoning_effort: str | None = None,
        model_catalog_version: str | None = None,
        model_verification_id: str | None = None,
    ) -> ReviewOutput:
        return self.output


class CodexRunner:
    _lock = asyncio.Lock()

    def __init__(self, settings: Settings, schema_path: Path | None = None) -> None:
        self.settings = settings
        configured_schema = schema_path or Path(
            os.environ.get("CODEX_SCHEMA_PATH", "review-schema.json")
        )
        self.schema_path = configured_schema.resolve()

    def _validate_schema_definition(self) -> None:
        """Validate the conservative schema subset accepted by codex-cli.

        The CLI's structured-output adapter does not consistently accept
        conditional/compositional JSON Schema.  Keep those failures explicit
        and fail before spawning a model process.
        """
        try:
            payload = json.loads(self.schema_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CodexError(
                "SCHEMA_DEFINITION_ERROR",
                "Review output schema is unreadable or invalid",
                signature="schema_file_invalid",
            ) from exc
        if payload.get("type") != "object" or payload.get("additionalProperties") is not False:
            raise CodexError(
                "SCHEMA_DEFINITION_ERROR",
                "Review schema root is unsupported",
                signature="schema_root",
            )
        unsupported = {"$ref", "$defs", "oneOf", "anyOf", "allOf", "if", "then", "else"}
        found: set[str] = set()
        mismatch = False

        def walk(value: object) -> None:
            nonlocal mismatch
            if isinstance(value, dict):
                found.update(unsupported.intersection(value))
                if "properties" in value:
                    nested_props = value.get("properties")
                    nested_required = value.get("required")
                    if (
                        not isinstance(nested_props, dict)
                        or not isinstance(nested_required, list)
                        or set(nested_required) != set(nested_props)
                    ):
                        mismatch = True
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(payload)
        if found:
            raise CodexError(
                "SCHEMA_DEFINITION_ERROR",
                "Review schema uses unsupported composition",
                signature="schema_unsupported_keyword",
            )
        props = payload.get("properties")
        required = payload.get("required")
        if (
            mismatch
            or not isinstance(props, dict)
            or not isinstance(required, list)
            or set(required) != set(props)
        ):
            raise CodexError(
                "SCHEMA_DEFINITION_ERROR",
                "Review schema required/properties mismatch",
                signature="schema_required_mismatch",
            )

    async def run(
        self,
        checkout: Path,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        execution_id: str | None = None,
        reasoning_effort: str | None = None,
        model_catalog_version: str | None = None,
        model_verification_id: str | None = None,
    ) -> ReviewOutput:
        self._validate_schema_definition()
        command = [
            self.settings.codex_command,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--config",
            'default_permissions="inryeok_review_read_only"',
            "--color",
            "never",
            "--output-schema",
            str(self.schema_path),
            "-",
        ]
        if model:
            if model_verification_id is None:
                try:
                    validate_model_effort(self.settings, model, reasoning_effort or "default")
                except ValueError as exc:
                    raise CodexError("CODEX_MODEL_NOT_ALLOWED", str(exc)) from exc
            elif not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", model_verification_id):
                raise CodexError(
                    "CODEX_MODEL_NOT_ALLOWED", "model verification snapshot is invalid"
                )
            command[2:2] = ["--model", model]
        if reasoning_effort:
            if reasoning_effort == "default":
                reasoning_effort = None
            elif reasoning_effort not in {"low", "medium", "high"}:
                raise CodexError(
                    "CODEX_REASONING_NOT_ALLOWED", "Codex reasoning effort is not allowed"
                )
            if reasoning_effort:
                command[2:2] = ["--config", f'model_reasoning_effort="{reasoning_effort}"']
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
            error = classify_codex_failure(process.returncode or 1, stdout, stderr)
            error.exit_code = process.returncode
            error.stderr_byte_length = len(stderr)
            error.safe_diagnostic = redact_diagnostic(
                stdout,
                stderr,
                sensitive_values=(prompt,),
                sensitive_paths=(checkout, self.settings.codex_home or Path("/var/lib/codex")),
            )
            raise error
        if not stdout.strip():
            raise CodexError("CODEX_OUTPUT_MISSING", "Codex returned no structured output")
        try:
            payload = _parse_structured_output(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexError(
                "CODEX_OUTPUT_INVALID_JSON", "Codex returned invalid JSON output"
            ) from exc
        try:
            return ReviewOutput.model_validate(payload)
        except ValidationError as exc:
            error = CodexError(
                "CODEX_OUTPUT_SCHEMA_MISMATCH",
                "Codex output did not match the review schema",
                signature="output_contract_mismatch",
            )
            error.safe_diagnostic, error.diagnostic_extraction_failed = normalize_schema_diagnostic(
                _validation_diagnostic(exc)
            )
            raise error from exc
