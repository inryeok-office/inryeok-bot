# ruff: noqa: E501
import hashlib
from collections import Counter
from typing import Any

from app.codex.schemas import Finding

_SEVERITY_LABELS = {
    "CRITICAL": "Critical",
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
}
_SEVERITY_ICONS = {
    "CRITICAL": "\U0001f6a8",
    "HIGH": "\U0001f534",
    "MEDIUM": "\U0001f7e0",
    "LOW": "\U0001f7e1",
}


def review_marker(
    owner: str, repository: str, pull_request: int, head_sha: str, prompt_version: str
) -> str:
    identity = f"{owner.casefold()}/{repository.casefold()}#{pull_request}:{head_sha}:{prompt_version}:review"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"v2:{digest}"


def _inline_text(value: str) -> str:
    """Keep model text out of Markdown control positions used by the publisher."""
    compact = " ".join(value.strip().splitlines())
    return compact.replace("\\", "\\\\").replace("`", "\\`").replace("#", "\\#")


def _summary_item(finding: Finding) -> str:
    prefix = f"- **{finding.severity.value} \u00b7 {finding.category.value}** \u2014 "
    return prefix + _inline_text(finding.title)


def _finding_body(finding: Finding) -> str:
    heading = _inline_text(finding.title)
    # The model body is validated Markdown and intentionally remains intact.
    return (
        f"**{_SEVERITY_ICONS[finding.severity.value]} "
        f"{finding.severity.value} \u00b7 {finding.category.value}**\n\n"
        f"### {heading}\n\n{finding.body.strip()}"
    )


def build_review_payload(
    findings: list[Finding],
    reviewed_file_count: int,
    head_sha: str,
    rerun: bool | None = None,
    language: str = "ko",
    marker: str = "v1",
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
            else "\uc218\uc815\uc774 \ud544\uc694\ud55c \ubb38\uc81c "
            f"**{len(findings)}\uac1c**\ub97c \ubc1c\uacac\ud588\uc2b5\ub2c8\ub2e4."
        )
        details = (
            ("### Key findings" if english else "### \uc8fc\uc694 \ub0b4\uc6a9")
            + "\n\n"
            + "\n".join(_summary_item(item) for item in findings)
        )
    else:
        overview = (
            "No issues requiring an inline comment were found."
            if english
            else "\uc218\uc815\uc774 \ud544\uc694\ud55c \ubb38\uc81c\ub97c "
            "\ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4."
        )
        details = (
            "### Complete\n\nThe changes were reviewed and no inline comments need to be posted."
            if english
            else (
                "### \uc644\ub8cc\n\n\ubcc0\uacbd \uc0ac\ud56d\uc744 \uac80\ud1a0\ud588\uc73c\uba70 "
                "\uac8c\uc2dc\ud560 \uc778\ub77c\uc778 \ucf54\uba58\ud2b8\uac00 \uc5c6\uc2b5\ub2c8\ub2e4."
            )
        )
    rerun_note = (
        (
            "\n\n> This is a rerun of the review."
            if english
            else "\n\n> \uc7ac\uc2e4\ud589\ud55c \uac80\ud1a0 \uacb0\uacfc\uc785\ub2c8\ub2e4."
        )
        if rerun
        else ""
    )
    body = (
        ("## Review result\n\n" if english else "## \ub9ac\ubdf0 \uacb0\uacfc\n\n")
        + (
            f"Reviewed **{reviewed_file_count} file(s)**. {overview}\n\n"
            if english
            else (
                f"\ubcc0\uacbd\ub41c **{reviewed_file_count}\uac1c \ud30c\uc77c**\uc744 \uac80\ud1a0\ud588\uc73c\uba70, "
                f"{overview}\n\n"
            )
        )
        + (
            "| Severity | Count |\n| --- | ---: |\n"
            if english
            else "| \uc2ec\uac01\ub3c4 | \uac1c\uc218 |\n| --- | ---: |\n"
        )
        + f"{table}\n\n{details}{rerun_note}\n\n"
        + (
            f"Reviewed head: `{head_sha[:12]}`"
            if english
            else f"\uac80\ud1a0\ud55c head: `{head_sha[:12]}`"
        )
        + f"\n\n<!-- inryeok-review:{marker} -->"
    )
    return {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": body,
        "comments": [
            {
                "path": item.path,
                "line": item.line,
                "side": "RIGHT",
                "body": _finding_body(item),
            }
            for item in findings
        ],
    }
