"""Structured, safe failure contracts shared by worker and administrator views."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from app.codex.runner import CodexError, normalize_schema_diagnostic
from app.github.client import GitHubAPIError


class FailureCategory(StrEnum):
    WEBHOOK = "WEBHOOK"
    COMMAND = "COMMAND"
    POLICY = "POLICY"
    PR = "PR"
    QUEUE = "QUEUE"
    GITHUB = "GITHUB"
    WORKSPACE = "WORKSPACE"
    EXECUTOR = "EXECUTOR"
    CODEX = "CODEX"
    OUTPUT = "OUTPUT"
    FINDING = "FINDING"
    REVIEW = "REVIEW"
    DATABASE = "DATABASE"
    INTERNAL = "INTERNAL"


class RetryPolicy(StrEnum):
    NEVER = "NEVER"
    SAFE_SAME_EXECUTION = "SAFE_SAME_EXECUTION"
    DELAYED = "DELAYED"


_SCHEMA_CODES = {
    "CODEX_OUTPUT_SCHEMA_MISMATCH": "OUTPUT_SCHEMA_MISMATCH",
    "SCHEMA_ERROR": "JSON_SCHEMA_VALIDATION_FAILED",
    "CODEX_OUTPUT_INVALID_JSON": "OUTPUT_JSON_INVALID",
    "CODEX_OUTPUT_MISSING": "OUTPUT_EMPTY",
    "OUTPUT_MODEL_VALIDATION_FAILED": "OUTPUT_MODEL_VALIDATION_FAILED",
}
_MESSAGES = {
    "OUTPUT_SCHEMA_MISMATCH": "리뷰 결과 형식을 검증하는 과정에서 오류가 발생했습니다. 동일 요청을 반복하지 말고 관리자 확인 후 다시 시도해 주세요.",  # noqa: E501
    "OUTPUT_JSON_INVALID": "리뷰 결과를 읽는 과정에서 오류가 발생했습니다. 동일 요청을 반복하지 말고 관리자 확인 후 다시 시도해 주세요.",  # noqa: E501
    "CODEX_QUOTA_EXCEEDED": "현재 Codex 사용 한도에 도달해 리뷰를 실행하지 못했습니다. 사용량이 갱신된 뒤 다시 요청해 주세요.",  # noqa: E501
    "CODEX_QUOTA": "현재 Codex 사용 한도에 도달해 리뷰를 실행하지 못했습니다. 사용량이 갱신된 뒤 다시 요청해 주세요.",  # noqa: E501
    "CODEX_RATE_LIMIT": "현재 Codex 요청이 제한되어 리뷰를 완료하지 못했습니다. 잠시 후 다시 안내하겠습니다.",  # noqa: E501
    "GITHUB_PERMISSION_DENIED": "이 저장소에서 리뷰를 게시할 권한을 확인할 수 없습니다. GitHub App 설치 권한을 관리자에게 확인해 주세요.",  # noqa: E501
    "GITHUB_RATE_LIMITED": "GitHub 요청 한도에 도달해 리뷰를 완료하지 못했습니다. 잠시 후 다시 안내하겠습니다.",  # noqa: E501
    "EXECUTOR_TIMEOUT": "리뷰 처리 시간이 초과되었습니다. 관리자 확인 후 다시 시도해 주세요.",
    "EXECUTOR_UNKNOWN_OUTCOME": "리뷰 처리 결과를 확인하지 못했습니다. 중복 실행을 막기 위해 관리자 확인이 필요합니다.",  # noqa: E501
    "REPOSITORY_DISABLED": "이 저장소의 자동 리뷰가 비활성화되어 요청을 처리하지 않았습니다.",
    "PULL_REQUEST_HEAD_CHANGED": "리뷰 요청 이후 PR의 변경 내용이 갱신되어 현재 요청을 중단했습니다. 최신 변경사항에서 다시 요청해 주세요.",  # noqa: E501
    "INTERNAL_INVARIANT_VIOLATION": "리뷰 처리 중 내부 상태 오류가 발생했습니다. 관리자 확인 후 다시 시도해 주세요.",  # noqa: E501
    "UNEXPECTED_INTERNAL_ERROR": "리뷰를 완료하지 못했습니다. 관리자 확인 후 다시 시도해 주세요.",
}


@dataclass(frozen=True)
class ReviewFailure:
    error_code: str
    category: str
    stage: str
    retryable: bool = False
    retry_policy: str = RetryPolicy.NEVER.value
    user_action_required: bool = True
    user_message_ko: str = _MESSAGES["UNEXPECTED_INTERNAL_ERROR"]
    operator_message_ko: str = (
        "세부 진단을 확인할 수 없습니다. 원본 예외는 보안 정책에 따라 저장하지 않았습니다."
    )
    safe_signature: str = "unknown"
    correlation_id: str | None = None
    execution_id: str | None = None
    job_id: int | None = None
    repository_id: int | None = None
    installation_id: int | None = None
    pr_number: int | None = None
    attempt: int | None = None
    http_status: int | None = None
    process_exit_code: int | None = None
    output_field: str | None = None
    validation_type: str | None = None
    diagnostic_extraction_failed: bool = False
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    safe_metadata: dict[str, str | int | bool] = field(default_factory=dict)


def _diagnostic_parts(
    diagnostic: tuple[str, ...] | list[str] | None,
) -> tuple[str | None, str | None]:
    output_field = validation_type = None
    for item in diagnostic or ():
        if item.startswith("output_field="):
            output_field = item.split("=", 1)[1].split(";", 1)[0][:128]
        if ";type=" in item:
            validation_type = item.split(";type=", 1)[1][:64]
    return output_field, validation_type


def _defaults(code: str) -> tuple[str, str, bool, str, bool]:
    if code in _SCHEMA_CODES:
        return FailureCategory.OUTPUT.value, "output_schema", False, RetryPolicy.NEVER.value, True
    if code in {
        "CODEX_QUOTA_EXCEEDED",
        "CODEX_QUOTA",
        "CODEX_RATE_LIMITED",
        "CODEX_RATE_LIMIT",
        "GITHUB_RATE_LIMITED",
    }:
        return (
            FailureCategory.CODEX.value
            if code.startswith("CODEX")
            else FailureCategory.GITHUB.value,
            "codex_exec" if code.startswith("CODEX") else "review_publish",
            True,
            RetryPolicy.DELAYED.value,
            False,
        )
    if code in {"CODEX_TIMEOUT", "EXECUTOR_TIMEOUT"}:
        return (
            FailureCategory.EXECUTOR.value,
            "executor_transport",
            True,
            RetryPolicy.DELAYED.value,
            False,
        )
    if code == "GITHUB_PERMISSION_DENIED":
        return FailureCategory.GITHUB.value, "review_publish", False, RetryPolicy.NEVER.value, True
    if code in {"GITHUB_AUTH_FAILED", "GITHUB_RESOURCE_NOT_FOUND", "GITHUB_VALIDATION_FAILED"}:
        return FailureCategory.GITHUB.value, "review_publish", False, RetryPolicy.NEVER.value, True
    if code == "GITHUB_SERVICE_UNAVAILABLE":
        return (
            FailureCategory.GITHUB.value,
            "review_publish",
            True,
            RetryPolicy.DELAYED.value,
            False,
        )
    if code == "EXECUTOR_UNAVAILABLE":
        return (
            FailureCategory.EXECUTOR.value,
            "executor_transport",
            True,
            RetryPolicy.DELAYED.value,
            False,
        )
    if code == "REPOSITORY_DISABLED":
        return FailureCategory.POLICY.value, "policy_resolve", False, RetryPolicy.NEVER.value, False
    if code == "PULL_REQUEST_HEAD_CHANGED":
        return FailureCategory.PR.value, "pull_request_fetch", False, RetryPolicy.NEVER.value, True
    if code == "EXECUTOR_UNKNOWN_OUTCOME":
        return (
            FailureCategory.EXECUTOR.value,
            "executor_transport",
            False,
            RetryPolicy.NEVER.value,
            True,
        )
    return FailureCategory.INTERNAL.value, "result_finalize", False, RetryPolicy.NEVER.value, True


def failure_from_exception(exc: Exception, *, job: Any = None) -> ReviewFailure:
    """Convert an exception to a bounded, typed failure without retaining values."""
    code = getattr(exc, "code", None)
    http_status = None
    if isinstance(exc, GitHubAPIError):
        http_status = exc.status_code
        if exc.status_code in {401}:
            code = "GITHUB_AUTH_FAILED"
        elif exc.status_code == 403:
            code = (
                "GITHUB_RATE_LIMITED"
                if exc.category == "GITHUB_RATE_LIMIT"
                else "GITHUB_PERMISSION_DENIED"
            )
        elif exc.status_code == 404:
            code = "GITHUB_RESOURCE_NOT_FOUND"
        elif exc.status_code == 422:
            code = "GITHUB_VALIDATION_FAILED"
        elif exc.status_code == 429 or exc.category == "GITHUB_RATE_LIMIT":
            code = "GITHUB_RATE_LIMITED"
        elif exc.status_code >= 500:
            code = "GITHUB_SERVICE_UNAVAILABLE"
        else:
            code = code or "GITHUB_UNKNOWN_RESPONSE"
    elif isinstance(exc, httpx.TimeoutException):
        code = "EXECUTOR_TIMEOUT"
    elif isinstance(exc, httpx.NetworkError):
        code = "EXECUTOR_UNAVAILABLE"
    code = str(code or "UNEXPECTED_INTERNAL_ERROR")
    category, stage, retryable, retry_policy, action = _defaults(code)
    diagnostic_failed = False
    if isinstance(exc, CodexError):
        diagnostic = exc.safe_diagnostic
        diagnostic_failed = bool(getattr(exc, "diagnostic_extraction_failed", False))
        if code in _SCHEMA_CODES:
            diagnostic, fallback_used = normalize_schema_diagnostic(diagnostic)
            diagnostic_failed = diagnostic_failed or fallback_used
        output_field, validation_type = _diagnostic_parts(diagnostic)
        signature = exc.signature[:64]
        exit_code = exc.exit_code
        correlation = exc.correlation_id
        stage = exc.stage
        if exc.retryable and retry_policy == RetryPolicy.NEVER.value:
            retryable, retry_policy = True, RetryPolicy.DELAYED.value
    if code in {"CODEX_OUTPUT_SCHEMA_MISMATCH", "SCHEMA_ERROR"}:
        stage = "output_schema"
    elif code == "OUTPUT_MODEL_VALIDATION_FAILED":
        stage = "output_model_validation"
    elif code == "CODEX_OUTPUT_INVALID_JSON":
        stage = "output_extract"
    else:
        output_field = validation_type = None
        signature = getattr(exc, "category", code)[:64]
        exit_code = None
        correlation = None
    public_code = _SCHEMA_CODES.get(code, code)
    user_message = _MESSAGES.get(
        public_code, _MESSAGES.get(code, _MESSAGES["UNEXPECTED_INTERNAL_ERROR"])
    )
    operator = (
        f"{public_code} 단계에서 처리에 실패했습니다. "
        "원본 예외는 보안 정책에 따라 저장하지 않았습니다."
    )
    return ReviewFailure(
        error_code=public_code,
        category=category,
        stage=stage,
        retryable=retryable,
        retry_policy=retry_policy,
        user_action_required=action,
        user_message_ko=user_message,
        operator_message_ko=operator,
        safe_signature=signature,
        correlation_id=correlation or getattr(job, "correlation_id", None),
        execution_id=getattr(job, "execution_id", None),
        job_id=getattr(job, "id", None),
        repository_id=None,
        installation_id=getattr(job, "installation_id", None),
        pr_number=getattr(job, "pull_request_number", None),
        attempt=getattr(job, "attempts", None),
        http_status=http_status,
        process_exit_code=exit_code,
        output_field=output_field,
        validation_type=validation_type,
        diagnostic_extraction_failed=diagnostic_failed,
        safe_metadata={
            "stderr_byte_length": int(getattr(exc, "stderr_byte_length", 0) or 0),
            "diagnostic_extraction_failed": diagnostic_failed,
        },
    )
