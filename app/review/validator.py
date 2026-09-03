from collections.abc import Iterable

from app.codex.schemas import Category, Finding, Severity
from app.review.deduplicator import fingerprint
from app.review.diff import ChangedFile, normalize_path

ORDER = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}

SIMPLIFICATION_RISKS = {
    "bug",
    "defect",
    "diverge",
    "inconsistent",
    "duplicate",
    "missed update",
    "query",
    "i/o",
    "standard library",
    "결함",
    "불일치",
    "중복",
    "수정 누락",
    "공통 기능",
    "표준 라이브러리",
}
SIMPLIFICATION_ACTIONS = {
    "replace",
    "reuse",
    "remove",
    "consolidate",
    "대체",
    "재사용",
    "제거",
    "통합",
}
PERFORMANCE_CAUSES = {
    "n+1",
    "query",
    "database",
    "network",
    "file i/o",
    "disk",
    "unbounded",
    "memory",
    "쿼리",
    "데이터베이스",
    "네트워크",
    "파일 i/o",
    "무제한",
    "메모리",
}
PERFORMANCE_CONDITIONS = {
    "each",
    "every",
    "loop",
    "when",
    "per ",
    "grows",
    "마다",
    "반복",
    "경우",
    "증가",
}


def _has_policy_evidence(finding: Finding) -> bool:
    text = f"{finding.title} {finding.body}".casefold()
    if finding.category == Category.SIMPLIFICATION:
        return any(value in text for value in SIMPLIFICATION_RISKS) and any(
            value in text for value in SIMPLIFICATION_ACTIONS
        )
    if finding.category == Category.PERFORMANCE:
        return any(value in text for value in PERFORMANCE_CAUSES) and any(
            value in text for value in PERFORMANCE_CONDITIONS
        )
    return True


def validate_findings(
    findings: Iterable[Finding],
    changed: dict[str, ChangedFile],
    min_confidence: float,
    include_low: bool,
    max_findings: int,
    existing_fingerprints: set[str] | None = None,
) -> list[Finding]:
    existing = existing_fingerprints or set()
    accepted: list[Finding] = []
    seen: set[str] = set()
    for finding in findings:
        try:
            finding.path = normalize_path(finding.path)
        except ValueError:
            continue
        file = changed.get(finding.path)
        mark = fingerprint(finding)
        if (
            not file
            or finding.line not in file.added_lines
            or finding.confidence < min_confidence
            or (finding.severity == Severity.LOW and not include_low)
            or not _has_policy_evidence(finding)
            or mark in existing
            or mark in seen
        ):
            continue
        seen.add(mark)
        accepted.append(finding)
    accepted.sort(key=lambda item: (-ORDER[item.severity], -item.confidence, item.path, item.line))
    return accepted[:max_findings]
