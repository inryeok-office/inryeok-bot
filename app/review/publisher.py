from collections import Counter
from typing import Any

from app.codex.schemas import Finding


def build_review_payload(
    findings: list[Finding], reviewed_file_count: int, head_sha: str, rerun: bool | None = None
) -> dict[str, Any]:
    counts = Counter(item.severity.value for item in findings)
    if findings:
        result = (
            f"Inryeok Bot이 {reviewed_file_count}개 파일을 검토했습니다.\n\n"
            f"발견 사항 {len(findings)}개\n"
            f"- 심각: {counts.get('CRITICAL', 0)}\n"
            f"- 높음: {counts.get('HIGH', 0)}\n"
            f"- 중간: {counts.get('MEDIUM', 0)}\n"
            f"- 낮음: {counts.get('LOW', 0)}\n\n"
        )
    else:
        result = (
            f"Inryeok Bot이 {reviewed_file_count}개 파일을 검토했습니다.\n\n"
            "게시할 문제를 찾지 못했습니다.\n\n"
        )
    rerun_note = "재실행 검토입니다.\n\n" if rerun else ""
    body = f"{result}{rerun_note}검토한 head: `{head_sha[:12]}`\n\n<!-- inryeok-review:v1 -->"
    icons = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}
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
