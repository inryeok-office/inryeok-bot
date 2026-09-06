# ruff: noqa: E501
import hashlib
from collections import Counter
from typing import Any

from app.codex.schemas import Finding

_SEVERITY_LABELS = {"CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}
_SEVERITY_ICONS = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}


def review_marker(
    owner: str, repository: str, pull_request: int, head_sha: str, prompt_version: str
) -> str:
    identity = f"{owner.casefold()}/{repository.casefold()}#{pull_request}:{head_sha}:{prompt_version}:review"
    return f"v2:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _inline_text(value: str) -> str:
    compact = " ".join(value.strip().splitlines())
    return compact.replace("\\", "\\\\").replace("`", "\\`").replace("#", "\\#")


def _summary_item(finding: Finding) -> str:
    return (
        f"- **{finding.severity.value} · {finding.category.value}** — {_inline_text(finding.title)}"
    )


def _finding_body(finding: Finding) -> str:
    return (
        f"**{_SEVERITY_ICONS[finding.severity.value]} {finding.severity.value} · {finding.category.value}**\n\n"
        f"### {_inline_text(finding.title)}\n\n{finding.body.strip()}"
    )


def build_review_payload(
    findings: list[Finding],
    reviewed_file_count: int,
    head_sha: str,
    rerun: bool | None = None,
    language: str = "ko",
    marker: str = "v1",
    comparison: dict[str, int] | None = None,
) -> dict[str, Any]:
    counts = Counter(item.severity.value for item in findings)
    table = "\n".join(
        f"| {_SEVERITY_LABELS[severity]} | {counts.get(severity, 0)} |"
        for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    )
    english = language == "en"
    if findings:
        overview = (
            f"Found **{len(findings)}** issue(s) that need attention."
            if english
            else f"수정이 필요한 문제 **{len(findings)}개**를 발견했습니다."
        )
        details = (
            ("### Key findings" if english else "### 주요 내용")
            + "\n\n"
            + "\n".join(_summary_item(item) for item in findings)
        )
    else:
        overview = (
            "No issues requiring an inline comment were found."
            if english
            else "수정이 필요한 문제를 찾지 못했습니다."
        )
        details = (
            "### Complete\n\nThe changes were reviewed and no inline comments need to be posted."
            if english
            else "### 완료\n\n변경 사항을 검토했으며 게시할 인라인 코멘트가 없습니다."
        )
    rerun_note = (
        ("\n\n> This is a rerun of the review." if english else "\n\n> 재실행한 검토 결과입니다.")
        if rerun
        else ""
    )
    comparison_block = ""
    if comparison and rerun:
        if english:
            comparison_block = (
                "\n\n### Rerun comparison\n\n"
                "| Category | Count |\n| --- | ---: |\n"
                f"| New findings | {comparison.get('new', 0)} |\n"
                f"| Still detected | {comparison.get('still', 0)} |\n"
                f"| Not detected in this review | {comparison.get('not_detected', 0)} |\n\n"
                "> Not detected in this review does not confirm that the issue is resolved."
            )
        else:
            comparison_block = (
                "\n\n### 재리뷰 비교\n\n"
                "| 구분 | 개수 |\n| --- | ---: |\n"
                f"| 새로운 Finding | {comparison.get('new', 0)} |\n"
                f"| 계속 확인된 Finding | {comparison.get('still', 0)} |\n"
                f"| 이번 리뷰에서 다시 발견되지 않음 | {comparison.get('not_detected', 0)} |\n\n"
                "> 이번 리뷰에서 다시 발견되지 않았다는 것이 해결을 확정하는 것은 아닙니다."
            )
    body = (
        ("## Review result\n\n" if english else "## 리뷰 결과\n\n")
        + (
            f"Reviewed **{reviewed_file_count} file(s)**. {overview}\n\n"
            if english
            else f"변경된 **{reviewed_file_count}개 파일**을 검토했으며, {overview}\n\n"
        )
        + (
            "| Severity | Count |\n| --- | ---: |\n"
            if english
            else "| 심각도 | 개수 |\n| --- | ---: |\n"
        )
        + f"{table}\n\n{details}{comparison_block}{rerun_note}\n\n"
        + (f"Reviewed head: `{head_sha[:12]}`" if english else f"검토한 head: `{head_sha[:12]}`")
        + f"\n\n<!-- inryeok-review:{marker} -->"
    )
    return {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": body,
        "comments": [
            {"path": item.path, "line": item.line, "side": "RIGHT", "body": _finding_body(item)}
            for item in findings
        ],
    }
