from __future__ import annotations

import asyncio

from app.codex.runner import CodexError
from scripts.diagnose_codex_failure import _one_codex_call, _sandbox_status


def test_diagnostic_reduces_nonzero_to_safe_fields(monkeypatch, tmp_path) -> None:
    async def fake_run(*args, **kwargs):
        raise CodexError("CODEX_EXIT_NONZERO", "safe", signature="unknown_nonzero")

    monkeypatch.setattr("scripts.diagnose_codex_failure.ExecutorRunner.run", fake_run)
    result = asyncio.run(_one_codex_call(tmp_path))
    assert result["error_code"] == "CODEX_EXIT_NONZERO"
    assert result["matched_safe_signature"] == "unknown_nonzero"
    assert result["retryable"] is False


def test_sandbox_check_uses_command_after_separator(monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return 0, b"", b""

    monkeypatch.setattr("scripts.diagnose_codex_failure._as_executor", fake_run)
    ok, details = _sandbox_status("/usr/bin/true")
    assert ok is True
    assert details["exit_code"] == 0
    assert seen == [["/usr/local/bin/codex", "sandbox", "--", "/usr/bin/true"]]


def test_sandbox_command_not_found_is_not_sandbox_failure(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        return 101, b"", b"execvp missing: no such file"

    monkeypatch.setattr("scripts.diagnose_codex_failure._as_executor", fake_run)
    ok, details = _sandbox_status("/missing/command")
    assert ok is False
    assert details["error_code"] == "DIAGNOSTIC_COMMAND_ERROR"
