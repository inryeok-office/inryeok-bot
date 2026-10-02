# ruff: noqa: E501
import hashlib
from collections import Counter
from typing import Any

from app.codex.schemas import Finding

_SEVERITY_LABELS = {"CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}
_TYPE_LABELS = {
    "MUST_FIX": "필수 수정",
    "SHOULD_FIX": "수정 권장",
    "SUGGESTION": "제안",
    "QUESTION": "확인 질문",
    "POSITIVE": "좋았던 점",
}


def review_marker(
    owner: str, repository: str, pull_request: int, head_sha: str, prompt_version: str
) -> str:
    identity = f"{owner.casefold()}/{repository.casefold()}#{pull_request}:{head_sha}:{prompt_version}:review"
    return f"v3:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _inline_text(value: str) -> str:
    return (
        " ".join(value.strip().splitlines())
        .replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("#", "\\#")
    )


def _summary_item(finding: Finding) -> str:
    return f"- **{_TYPE_LABELS[finding.review_type.value]} · {finding.severity.value}** {_inline_text(finding.title)}"


def _summary_finding(finding: Finding) -> str:
    location = f"`{_inline_text(finding.path)}`" if finding.path else "PR 전체"
    parts = [f"#### {location} · {_inline_text(finding.title)}", finding.body.strip()]
    for label, value in (
        ("발생 조건", finding.condition),
        ("영향", finding.impact),
        ("근거", finding.evidence),
        ("이유", finding.why_it_matters),
        ("제안", finding.suggested_action),
    ):
        if value:
            parts.append(f"**{label}**: {_inline_text(value)}")
    return "\n\n".join(parts)


def _finding_body(finding: Finding) -> str:
    parts = [
        f"**{_TYPE_LABELS[finding.review_type.value]} · {finding.severity.value}**",
        f"### {_inline_text(finding.title)}",
        finding.body.strip(),
    ]
    if finding.why_it_matters:
        parts.append(f"**이유**: {finding.why_it_matters.strip()}")
    if finding.suggested_action:
        parts.append(f"**제안**: {finding.suggested_action.strip()}")
    if finding.style_guide_reference:
        parts.append(f"**스타일 가이드**: {_inline_text(finding.style_guide_reference)}")
    return "\n\n".join(parts)


def _comment_body(finding: Finding, allow_suggested_changes: bool) -> str:
    body = _finding_body(finding)
    if allow_suggested_changes and finding.suggested_patch:
        return body + "\n\n```suggestion\n" + finding.suggested_patch.strip() + "\n```"
    return body


def build_review_payload(
    findings: list[Finding],
    reviewed_file_count: int,
    head_sha: str,
    rerun: bool | None = None,
    language: str = "ko",
    marker: str = "v1",
    comparison: dict[str, int] | None = None,
    include_inline_comments: bool = True,
    no_reviewable_reason: str | None = None,
    allow_suggested_changes: bool = True,
) -> dict[str, Any]:
    inline = [item for item in findings if include_inline_comments and item.scope.value == "LINE"]
    summary_only = [
        item for item in findings if item.scope.value != "LINE" or not include_inline_comments
    ]
    severity_counts = Counter(item.severity.value for item in findings)
    type_counts = Counter(item.review_type.value for item in findings)
    severity_table = "\n".join(
        f"| {_SEVERITY_LABELS[value]} | {severity_counts.get(value, 0)} |"
        for value in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    )
    type_text = ", ".join(
        f"{_TYPE_LABELS[value]} {type_counts.get(value, 0)}" for value in _TYPE_LABELS
    )
    if language == "en":
        overview = (
            f"Reviewed **{reviewed_file_count} file(s)** and posted {len(inline)} inline observation(s)."
            if findings
            else f"Reviewed **{reviewed_file_count} file(s)**."
        )
        details = (
            "### Key observations\n\n" + "\n".join(_summary_item(item) for item in findings)
            if findings
            else "### Complete\n\nNo inline observation was published."
        )
        if summary_only:
            details += "\n\n### File and PR scope\n\n" + "\n\n".join(
                _summary_finding(item) for item in summary_only
            )
        comparison_block = ""
        if comparison and rerun:
            comparison_block = (
                "\n\n### Rerun comparison\n\n| Category | Count |\n| --- | ---: |\n"
                + f"| New findings | {comparison.get('new', 0)} |\n| Still detected | {comparison.get('still', 0)} |\n| Not detected in this review | {comparison.get('not_detected', 0)} |\n\n> Not detected in this review does not confirm resolution."
            )
        body_prefix, type_prefix, head_note = (
            "## Review result",
            "Types",
            f"Reviewed head: `{head_sha[:12]}` · use `/review` for the latest head.",
        )
    else:
        overview = (
            f"변경된 **{reviewed_file_count}개 파일**을 검토했고 인라인 관찰 {len(inline)}개를 남겼습니다."
            if findings
            else f"변경된 **{reviewed_file_count}개 파일**을 검토했습니다."
        )
        details = (
            "### 주요 관찰\n\n" + "\n".join(_summary_item(item) for item in findings)
            if findings
            else "### 완료\n\n수정이 필요한 문제를 찾지 못했습니다."
        )
        if summary_only:
            details += "\n\n### 파일·PR 단위 검토\n\n" + "\n\n".join(
                _summary_finding(item) for item in summary_only
            )
        if no_reviewable_reason and not findings:
            details += f" 검증 가능한 인라인 관찰은 생성하지 않았습니다 (`{no_reviewable_reason}`)."
        comparison_block = ""
        if comparison and rerun:
            comparison_block = (
                "\n\n### 재리뷰 비교\n\n| 구분 | 개수 |\n| --- | ---: |\n"
                + f"| 새로운 Finding | {comparison.get('new', 0)} |\n| 계속 확인된 Finding | {comparison.get('still', 0)} |\n| 이번 리뷰에서 다시 발견되지 않음 | {comparison.get('not_detected', 0)} |\n\n> 이번 리뷰에서 다시 발견되지 않았다고 해결을 확정하지는 않습니다."
            )
        body_prefix, type_prefix, head_note = (
            "## 리뷰 결과",
            "유형",
            f"검토한 head: `{head_sha[:12]}` · 최신 head에서 다시 검토하려면 `/review`를 작성하세요.",
        )
    body = f"{body_prefix}\n\n{overview}\n\n| 심각도 | 개수 |\n| --- | ---: |\n{severity_table}\n\n{type_prefix}: {type_text}\n\n{details}{comparison_block}\n\n{head_note}\n\n<!-- inryeok-review:{marker} -->"
    return {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": body,
        "comments": [
            {
                "path": item.path,
                "line": item.line,
                "side": item.side or "RIGHT",
                "body": _comment_body(item, allow_suggested_changes),
            }
            for item in inline
        ],
    }
