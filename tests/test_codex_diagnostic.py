from __future__ import annotations

import asyncio

from scripts.diagnose_codex_failure import _one_codex_call


class _Process:
    returncode = 17

    async def communicate(self, _: bytes) -> tuple[bytes, bytes]:
        return b"safe output", b"secret-token and private diagnostic"


def test_diagnostic_reduces_nonzero_to_safe_fields(monkeypatch, tmp_path) -> None:
    async def fake_process(*args, **kwargs) -> _Process:
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_process)
    result = asyncio.run(_one_codex_call(tmp_path))
    assert result["error_code"] == "CODEX_EXIT_NONZERO"
    assert result["matched_safe_signature"] == "unknown_nonzero"
    assert result["retryable"] is False
    assert "secret-token" not in str(result)
    assert "private diagnostic" not in str(result)
