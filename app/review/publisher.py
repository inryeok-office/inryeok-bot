from collections import Counter
from typing import Any

from app.codex.schemas import Finding


def build_review_payload(
    findings: list[Finding], reviewed_file_count: int, head_sha: str, rerun: bool
) -> dict[str, Any]:
    counts = Counter(item.severity.value for item in findings)
    severity = ", ".join(
        f"{name}: {counts.get(name, 0)}" for name in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    )
    rerun_text = "yes" if rerun else "no"
    body = (
        f"Codex review: {reviewed_file_count} files, {len(findings)} findings "
        f"({severity}). Head `{head_sha[:12]}`. Rerun: {rerun_text}."
        "\n\n<!-- github-codex-review:v1 -->"
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
                "body": (
                    f"**{item.severity.value}: {item.title}**\n\n{item.body}"
                    f"\n\nConfidence: {item.confidence:.0%}"
                ),
            }
            for item in findings
        ],
    }
