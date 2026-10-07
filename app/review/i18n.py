"""Typed system-message catalog for reviewer-facing GitHub text."""
# ruff: noqa: E501

from typing import Final, Literal

Locale = Literal["ko", "en"]
DEFAULT_LOCALE: Final[Locale] = "ko"
CATALOG_VERSION: Final[str] = "1"
MESSAGES: Final[dict[Locale, dict[str, str]]] = {
    "en": {
        "review_result": "## Review result",
        "reviewed": "Reviewed **{files} file(s)**.",
        "reviewed_inline": "Reviewed **{files} file(s)** and posted {inline} inline observation(s).",
        "severity": "Severity",
        "count": "Count",
        "types": "Types",
        "complete": "### Complete",
        "no_inline": "No inline observation was published.",
        "key": "### Key observations",
        "head": "Reviewed head: `{head}` · use `/review` for the latest head.",
        "file_pr": "### File and PR scope",
        "condition": "Condition",
        "impact": "Impact",
        "evidence": "Evidence",
        "why": "Why it matters",
        "suggestion": "Suggested action",
        "all_pr": "Entire PR",
    },
    "ko": {
        "review_result": "## 리뷰 결과",
        "reviewed": "변경된 **{files}개 파일**을 검토했습니다.",
        "reviewed_inline": "변경된 **{files}개 파일**을 검토했습니다. 인라인 관찰 {inline}개를 게시했습니다.",
        "severity": "심각도",
        "count": "개수",
        "types": "유형",
        "complete": "### 완료",
        "no_inline": "수정이 필요한 문제를 찾지 못했습니다.",
        "key": "### 주요 관찰",
        "head": "검토한 head: `{head}` · 최신 head는 `/review`로 검토하세요.",
        "file_pr": "### 파일·PR 단위 검토",
        "condition": "발생 조건",
        "impact": "영향",
        "evidence": "근거",
        "why": "이유",
        "suggestion": "제안",
        "all_pr": "PR 전체",
    },
}
TYPE_LABELS: Final[dict[Locale, dict[str, str]]] = {
    "en": {
        "MUST_FIX": "Must fix",
        "SHOULD_FIX": "Should fix",
        "SUGGESTION": "Suggestion",
        "QUESTION": "Question",
        "POSITIVE": "Positive",
    },
    "ko": {
        "MUST_FIX": "필수 수정",
        "SHOULD_FIX": "수정 권장",
        "SUGGESTION": "제안",
        "QUESTION": "확인 질문",
        "POSITIVE": "좋았던 점",
    },
}
SEVERITY_LABELS: Final[dict[Locale, dict[str, str]]] = {
    "en": {"CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"},
    "ko": {"CRITICAL": "치명적", "HIGH": "높음", "MEDIUM": "중간", "LOW": "낮음"},
}


def normalize_locale(value: object) -> Locale:
    return value if value in MESSAGES else DEFAULT_LOCALE


def message(locale: object, key: str, **values: object) -> str:
    try:
        return MESSAGES[normalize_locale(locale)][key].format(**values)
    except KeyError as exc:
        raise KeyError(f"missing review message key: {normalize_locale(locale)}.{key}") from exc
