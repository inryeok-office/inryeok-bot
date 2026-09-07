"""One-shot, operator-only Codex failure diagnosis.

The command is deliberately not reachable through the executor API.  It keeps
process output in bounded memory, classifies it with the existing safe
classifier, and prints only fixed fields.  Raw diagnostics are never written
to disk, a journal, or the application.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from app.codex.runner import MAX_PROCESS_OUTPUT, classify_codex_failure

_pwd: Any = None
try:
    _pwd = importlib.import_module("pwd")
except ImportError:  # pragma: no cover - Windows has no pwd module.
    pass

EXECUTOR_USER = "inryeok-executor"
EXECUTOR_GROUP = "inryeok-worker"
CODEX = "/usr/local/bin/codex"
CODEX_HOME = Path("/var/lib/inryeok-bot-executor/codex-home")
APP_ROOT = Path("/opt/inryeok-bot/app")
SCHEMA = APP_ROOT / "review-schema.json"
REQUIREMENTS = APP_ROOT / "deploy/codex-requirements.toml"
MANAGED_CONFIG = APP_ROOT / "deploy/codex-managed.toml"
SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
MAX_DIAGNOSTIC_BYTES = 64_000


def _uid() -> int:
    return int(getattr(os, "geteuid", lambda: -1)())


def _bounded_run(
    argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> tuple[int, bytes, bytes]:
    """Run one no-secret command and retain only bounded output in memory."""
    completed = subprocess.run(  # noqa: S603 - argv is built from fixed diagnostic commands
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    return (
        completed.returncode,
        completed.stdout[:MAX_DIAGNOSTIC_BYTES],
        completed.stderr[:MAX_DIAGNOSTIC_BYTES],
    )


def _executor_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in SAFE_ENV if os.environ.get(key)}
    env.update({"CODEX_HOME": str(CODEX_HOME), "HOME": str(CODEX_HOME.parent)})
    return env


def _as_executor(argv: list[str], *, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
    if os.name == "nt" or _uid() != 0:
        return _bounded_run(argv, cwd=cwd, env=_executor_env())
    return _bounded_run(
        ["runuser", "--user", EXECUTOR_USER, "--", "env", "-i", *_env_args(), *argv],
        cwd=cwd,
    )


def _env_args() -> list[str]:
    return [f"{key}={value}" for key, value in _executor_env().items()]


def _file_state(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"present": False}
    return {
        "present": True,
        "mode": oct(stat.st_mode & 0o777),
        "readable": os.access(path, os.R_OK),
    }


def _stage(name: str, ok: bool, **details: Any) -> dict[str, Any]:
    return {"stage": name, "ok": ok, **details}


def _static_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append(
        _stage(
            "config_load",
            MANAGED_CONFIG.is_file() and REQUIREMENTS.is_file(),
            managed_config=_file_state(MANAGED_CONFIG),
            requirements=_file_state(REQUIREMENTS),
        )
    )
    checks.append(
        _stage(
            "schema_validation",
            SCHEMA.is_file() and os.access(SCHEMA, os.R_OK),
            schema=_file_state(SCHEMA),
        )
    )
    for name, argv in (
        ("codex_start", [CODEX, "--version"]),
        ("codex_options", [CODEX, "exec", "--help"]),
        ("sandbox_options", [CODEX, "sandbox", "linux", "--help"]),
        ("authentication", [CODEX, "login", "status"]),
    ):
        ok, details = _command_status(argv)
        checks.append(_stage(name, ok, **details))
    try:
        with MANAGED_CONFIG.open("rb") as stream:
            tomllib.load(stream)
        with REQUIREMENTS.open("rb") as stream:
            tomllib.load(stream)
        profile_ok = True
    except (OSError, tomllib.TOMLDecodeError):
        profile_ok = False
    checks.append(_stage("permission_profile", profile_ok))
    checks.append(_stage("codex_home", CODEX_HOME.is_dir(), state=_file_state(CODEX_HOME)))
    checks.append(_stage("systemd_paths", _unit_has_expected_paths()))
    return checks


def _command_status(argv: list[str]) -> tuple[bool, dict[str, Any]]:
    code, stdout, stderr = _as_executor(argv, cwd=APP_ROOT)
    return code == 0, {"exit_code": code, "stdout_bytes": len(stdout), "stderr_bytes": len(stderr)}


def _unit_has_expected_paths() -> bool:
    unit = Path("/etc/systemd/system/inryeok-codex-executor.service")
    try:
        text = unit.read_text(encoding="utf-8")
    except OSError:
        return False
    required = (
        "ProtectHome=yes",
        "ProtectSystem=strict",
        "ReadWritePaths=/var/lib/inryeok-bot-executor /run/inryeok-bot",
    )
    return all(item in text for item in required)


def _fixture(root: Path) -> Path:
    repo = root / "fixture"
    repo.mkdir()
    (repo / "sample.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    for argv in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "diagnostic@example.invalid"),
        ("git", "config", "user.name", "diagnostic"),
        ("git", "add", "sample.py"),
        ("git", "commit", "-qm", "diagnostic"),
    ):
        code, _, _ = _as_executor(list(argv), cwd=repo)
        if code != 0:
            raise RuntimeError("git fixture preparation failed")
    return repo


async def _one_codex_call(repo: Path) -> dict[str, Any]:
    command = [
        CODEX,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--ask-for-approval",
        "never",
        "--config",
        'default_permissions="inryeok_review_read_only"',
        "--color",
        "never",
        "--output-schema",
        str(SCHEMA),
        "-",
    ]
    env = _executor_env()
    if os.name != "nt" and _uid() == 0:
        command = ["runuser", "--user", EXECUTOR_USER, "--", "env", "-i", *_env_args(), *command]
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=repo,
        env=None if command[0] == "runuser" else env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(
        b"Review this small fixture and return structured output."
    )
    stdout = stdout[:MAX_PROCESS_OUTPUT]
    stderr = stderr[:MAX_PROCESS_OUTPUT]
    if process.returncode:
        error = classify_codex_failure(process.returncode, stdout, stderr)
        return {
            "stage": "codex_exec",
            "ok": False,
            "exit_code": process.returncode,
            "error_code": error.code,
            "retryable": error.retryable,
            "matched_safe_signature": error.signature,
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
        }
    try:
        payload = json.loads(stdout)
        ok = isinstance(payload, dict)
    except json.JSONDecodeError:
        ok = False
    return {
        "stage": "structured_output",
        "ok": ok,
        "exit_code": 0,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="one-shot root-only Codex diagnosis")
    parser.add_argument(
        "--run-codex", action="store_true", help="perform the single final diagnostic call"
    )
    args = parser.parse_args()
    if (
        os.name != "nt"
        and _uid() != 0
        and _pwd is not None
        and _pwd.getpwuid(_uid()).pw_name != EXECUTOR_USER
    ):
        raise SystemExit("root is required")
    result: dict[str, Any] = {
        "diagnostic": "one-shot",
        "codex_calls": 0,
        "stages": _static_checks(),
    }
    if args.run_codex:
        root = Path(tempfile.mkdtemp(prefix="inrye-codex-diagnostic-"))
        try:
            os.chmod(root, 0o700)
            if os.name != "nt" and _uid() == 0 and _pwd is not None:
                account = _pwd.getpwnam(EXECUTOR_USER)
                os.chown(root, account.pw_uid, account.pw_gid)  # type: ignore[attr-defined]
            repo = _fixture(root)
            result["stages"].append(_stage("workspace_prepare", True))
            result["stages"].append(_stage("git_validation", (repo / ".git/HEAD").is_file()))
            result["codex_calls"] = 1
            result["result"] = asyncio.run(_one_codex_call(repo))
        finally:
            shutil.rmtree(root, ignore_errors=True)
            result["temporary_files_removed"] = not root.exists()
    print(json.dumps(result, sort_keys=True))
    result_ok = result.get("result", {}).get("ok", True)
    return 0 if result_ok and all(stage.get("ok", False) for stage in result["stages"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
