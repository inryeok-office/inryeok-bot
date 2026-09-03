from collections import Counter
from typing import Any

from app.codex.schemas import Finding


def build_review_payload(
    findings: list[Finding], reviewed_file_count: int, head_sha: str, rerun: bool | None = None
) -> dict[str, Any]:
    counts = Counter(item.severity.value for item in findings)
    body = (
        f"Inryeok Bot reviewed {reviewed_file_count} files\n\n"
        f"{len(findings)} findings\n"
        f"- Critical: {counts.get('CRITICAL', 0)}\n"
        f"- High: {counts.get('HIGH', 0)}\n"
        f"- Medium: {counts.get('MEDIUM', 0)}\n"
        f"- Low: {counts.get('LOW', 0)}\n\n"
        f"Head: `{head_sha[:12]}`\n\n"
        "<!-- inryeok-review:v1 -->"
    )
    icons = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}
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
                    f"{icons[item.severity.value]} {item.severity.value} · {item.title}"
                    f"\n\n{item.body}"
                ),
            }
            for item in findings
        ],
    }
