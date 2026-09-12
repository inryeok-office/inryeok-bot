"""Presentation helpers for the administrator console.

These helpers keep display formatting out of templates while leaving the
stored timestamps and canonical policy values untouched.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.jobs.models import JobStatus, ReviewJob, ReviewRun

_KST = ZoneInfo("Asia/Seoul")


def format_admin_datetime(value: datetime | None) -> str:
    """Render a stored UTC timestamp as a compact, human-readable KST value."""

    if value is None:
        return "기록 없음"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    local = aware.astimezone(_KST)
    return f"{local.year}. {local.month}. {local.day}. {local:%H:%M}"


def review_outcome_code(job: ReviewJob, run: ReviewRun | None) -> str:
    if run is None:
        return "HISTORICAL_NO_DETAILS" if job.status != JobStatus.PENDING else "NO_RESULT"
    if run.raw_findings_count == 0:
        return "MODEL_NO_FINDINGS"
    if run.rejected_findings_count > 0 and run.published_findings_count == 0:
        return "FINDINGS_REJECTED"
    if run.published_findings_count > 0 and run.github_review_id is None:
        return "PUBLISH_FAILED" if job.status == JobStatus.FAILED else "PUBLISH_PENDING"
    if run.github_review_id is not None:
        return "PUBLISHED"
    return "RESULT_WITHOUT_REVIEW"


def review_outcome_label(code: str) -> str:
    return {
        "MODEL_NO_FINDINGS": "모델이 Finding을 생성하지 않음",
        "FINDINGS_REJECTED": "모델이 생성했지만 검증에서 거부됨",
        "PUBLISH_FAILED": "게시 가능한 Finding이 있었으나 게시 실패",
        "PUBLISH_PENDING": "게시 가능한 Finding이 있으나 아직 게시되지 않음",
        "PUBLISHED": "검증된 Finding을 게시함",
        "HISTORICAL_NO_DETAILS": "과거 실행이라 세부 기록 없음",
        "NO_RESULT": "아직 결과가 없음",
        "RESULT_WITHOUT_REVIEW": "결과는 기록됐으나 Review 상태를 확인해야 함",
    }.get(code, "상태를 확인해야 함")
