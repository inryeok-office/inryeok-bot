from __future__ import annotations

import asyncio

from app.codex.runner import CodexError, classify_codex_failure, redact_diagnostic
from app.review.failures import failure_from_exception
from app.jobs.models import ReviewJob
from app.jobs.worker import _apply_failure
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


def test_diagnostic_redacts_credentials_and_limits_lines() -> None:
    lines = redact_diagnostic(b"Authorization: Bearer secret-token\n" * 20, b"stderr")
    assert len(lines) <= 10
    assert all("secret-token" not in line for line in lines)


def test_diagnostic_redacts_paths_pem_prompt_and_limits_bytes(tmp_path) -> None:
    prompt = "private prompt marker"
    payload = (
        f"{tmp_path} {prompt} DATABASE_URL=postgresql://user:password@db/app\n" + "x" * 6000
    ).encode()
    lines = redact_diagnostic(
        payload,
        ("-----BEGIN " + "PRIVATE KEY-----secret-----END PRIVATE KEY-----").encode(),
        sensitive_values=(prompt,),
        sensitive_paths=(tmp_path,),
    )
    rendered = "\n".join(lines)
    assert len(lines) <= 10
    assert len(rendered) <= 2048
    assert prompt not in rendered
    assert str(tmp_path) not in rendered
    assert "password@db" not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered


def test_failure_categories_are_safe_and_unknown_is_not_retryable() -> None:
    cases = [
        (b"unknown option", "CLI_ARGUMENT_ERROR", False),
        (b"permission denied", "FILE_PERMISSION_ERROR", False),
        (b"not a git repository", "GIT_REPOSITORY_ERROR", False),
        (b"schema validation failed", "SCHEMA_ERROR", False),
        (b"bwrap user namespace", "SANDBOX_START_ERROR", False),
        (b"authentication required", "CODEX_AUTH", False),
        (b"service unavailable", "CODEX_SERVICE_UNAVAILABLE", True),
        (b"unclassified failure", "CODEX_EXIT_NONZERO", False),
    ]
    for stderr, code, retryable in cases:
        error = classify_codex_failure(1, b"", stderr)
        assert error.code == code
        assert error.retryable is retryable


def test_schema_failure_fallback_is_persistable_without_original_value() -> None:
    error = CodexError("CODEX_OUTPUT_SCHEMA_MISMATCH", "schema mismatch")
    failure = failure_from_exception(error)
    assert failure.output_field == "__root__"
    assert failure.validation_type == "schema_validation_unknown"
    assert failure.retry_policy == "NEVER"
    assert failure.diagnostic_extraction_failed is True
    assert failure.safe_metadata["diagnostic_extraction_failed"] is True


def test_schema_failure_fields_reach_job_record_without_original_value() -> None:
    error = CodexError("CODEX_OUTPUT_SCHEMA_MISMATCH", "schema mismatch")
    job = ReviewJob(
        delivery_id="synthetic-schema-loss",
        installation_id=1,
        repository_owner="acme",
        repository_name="repo",
        pull_request_number=1,
        base_sha="a" * 40,
        head_sha="b" * 40,
        trigger_type="COMMAND",
    )
    failure = failure_from_exception(error, job=job)
    _apply_failure(job, failure)
    assert job.error_code == "OUTPUT_SCHEMA_MISMATCH"
    assert job.error_stage == "output_schema"
    assert job.output_field == "__root__"
    assert job.validation_type == "schema_validation_unknown"
    assert job.retry_policy == "NEVER"
    assert job.diagnostic_extraction_failed is True
