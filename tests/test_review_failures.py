import httpx

from app.codex.runner import CodexError
from app.github.client import GitHubAPIError
from app.review.failures import failure_from_exception


def test_schema_failure_keeps_only_safe_field_diagnostic() -> None:
    error = CodexError(
        "CODEX_OUTPUT_SCHEMA_MISMATCH",
        "do not persist this value",
        signature="output_contract_mismatch",
    )
    error.safe_diagnostic = ("output_field=findings.0.line;type=int_type",)
    failure = failure_from_exception(error)
    assert failure.error_code == "OUTPUT_SCHEMA_MISMATCH"
    assert failure.category == "OUTPUT"
    assert failure.stage == "output_schema"
    assert failure.retry_policy == "NEVER"
    assert failure.output_field == "findings.0.line"
    assert failure.validation_type == "int_type"
    assert "do not persist" not in failure.operator_message_ko


def test_github_statuses_are_stable_and_actionable() -> None:
    assert failure_from_exception(GitHubAPIError(403)).error_code == "GITHUB_PERMISSION_DENIED"
    assert failure_from_exception(GitHubAPIError(422)).error_code == "GITHUB_VALIDATION_FAILED"
    limited = failure_from_exception(GitHubAPIError(429))
    assert limited.error_code == "GITHUB_RATE_LIMITED"
    assert limited.retry_policy == "DELAYED"


def test_transport_timeout_never_exposes_exception_text() -> None:
    failure = failure_from_exception(httpx.TimeoutException("secret/path/token"))
    assert failure.error_code == "EXECUTOR_TIMEOUT"
    assert "secret/path/token" not in failure.operator_message_ko
