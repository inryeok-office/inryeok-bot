# ruff: noqa: E501
import hashlib
from collections import Counter
from typing import Any

from app.codex.schemas import Finding
from app.review.i18n import SEVERITY_LABELS, TYPE_LABELS, message, normalize_locale

_SEVERITY_ICONS = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🔷", "LOW": "🔹"}


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


def _summary_item(finding: Finding, locale: str) -> str:
    return f"- **{TYPE_LABELS[normalize_locale(locale)][finding.review_type.value]} · {finding.severity.value}** {_inline_text(finding.title)}"


def _summary_finding(finding: Finding, locale: str) -> str:
    location = f"`{_inline_text(finding.path)}`" if finding.path else message(locale, "all_pr")
    parts = [f"#### {location} · {_inline_text(finding.title)}", finding.body.strip()]
    for key, value in (
        ("condition", finding.condition),
        ("impact", finding.impact),
        ("evidence", finding.evidence),
        ("why", finding.why_it_matters),
        ("suggestion", finding.suggested_action),
    ):
        if value:
            parts.append(f"**{message(locale, key)}**: {_inline_text(value)}")
    return "\n\n".join(parts)


def _finding_body(finding: Finding, locale: str) -> str:
    parts = [
        f"**{_SEVERITY_ICONS[finding.severity.value]} {TYPE_LABELS[normalize_locale(locale)][finding.review_type.value]} · {finding.severity.value}**",
        f"### {_inline_text(finding.title)}",
        finding.body.strip(),
    ]
    if finding.why_it_matters:
        parts.append(f"**{message(locale, 'why')}**: {finding.why_it_matters.strip()}")
    if finding.suggested_action:
        parts.append(f"**{message(locale, 'suggestion')}**: {finding.suggested_action.strip()}")
    if finding.style_guide_reference:
        parts.append(f"**Style guide**: {_inline_text(finding.style_guide_reference)}")
    return "\n\n".join(parts)


def _comment_body(finding: Finding, allow_suggested_changes: bool, locale: str) -> str:
    body = _finding_body(finding, locale)
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
    language = normalize_locale(language)
    inline = [item for item in findings if include_inline_comments and item.scope.value == "LINE"]
    summary = [
        item for item in findings if item.scope.value != "LINE" or not include_inline_comments
    ]
    severity, types = (
        Counter(item.severity.value for item in findings),
        Counter(item.review_type.value for item in findings),
    )
    table = "\n".join(
        f"| {SEVERITY_LABELS[language][key]} | {severity.get(key, 0)} |"
        for key in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    )
    type_text = ", ".join(
        f"{TYPE_LABELS[language][key]} {types.get(key, 0)}" for key in TYPE_LABELS[language]
    )
    overview = (
        message(language, "reviewed_inline", files=reviewed_file_count, inline=len(inline))
        if findings
        else message(language, "reviewed", files=reviewed_file_count)
    )
    details = (
        message(language, "key")
        + "\n\n"
        + "\n".join(_summary_item(item, language) for item in findings)
        if findings
        else message(language, "complete") + "\n\n" + message(language, "no_inline")
    )
    if summary:
        details += (
            "\n\n"
            + message(language, "file_pr")
            + "\n\n"
            + "\n\n".join(_summary_finding(item, language) for item in summary)
        )
    if no_reviewable_reason and not findings:
        details += f"\n\n{no_reviewable_reason}"
    if rerun and comparison:
        if language == "en":
            details += (
                "\n\n### Rerun comparison\n\n| Category | Count |\n| --- | ---: |\n"
                f"| New findings | {comparison.get('new', 0)} |\n"
                f"| Still detected | {comparison.get('still', 0)} |\n"
                f"| Not detected in this review | {comparison.get('not_detected', 0)} |\n\n"
                "> Not detected in this review does not confirm resolution."
            )
        else:
            details += (
                "\n\n### 재리뷰 비교\n\n| 구분 | 개수 |\n| --- | ---: |\n"
                f"| 새로운 Finding | {comparison.get('new', 0)} |\n"
                f"| 계속 확인됨 | {comparison.get('still', 0)} |\n"
                f"| 이번 리뷰에서 다시 발견하지 않음 | {comparison.get('not_detected', 0)} |\n\n"
                "> 이번 리뷰에서 다시 발견하지 않았다고 해결을 확정하지 않습니다."
            )
    body = f"{message(language, 'review_result')}\n\n{overview}\n\n| {message(language, 'severity')} | {message(language, 'count')} |\n| --- | ---: |\n{table}\n\n{message(language, 'types')}: {type_text}\n\n{details}\n\n{message(language, 'head', head=head_sha[:12])}\n\n<!-- inryeok-review:{marker} -->"
    return {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": body,
        "comments": [
            {
                "path": item.path,
                "line": item.line,
                "side": item.side or "RIGHT",
                "body": _comment_body(item, allow_suggested_changes, language),
            }
            for item in inline
        ],
    }
