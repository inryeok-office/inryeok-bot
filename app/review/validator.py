from collections.abc import Iterable
from dataclasses import dataclass

from app.codex.schemas import Category, Finding, Severity
from app.review.deduplicator import fingerprint
from app.review.diff import ChangedFile, normalize_path

ORDER = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}


@dataclass(frozen=True)
class FindingValidationResult:
    findings: list[Finding]
    changed_file_count: int
    changed_line_count: int
    confidence_count: int
    severity_count: int
    evidence_count: int
    deduplicated_count: int

    @property
    def published_count(self) -> int:
        return len(self.findings)


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
    return validate_findings_with_diagnostics(
        findings,
        changed,
        min_confidence,
        include_low,
        max_findings,
        existing_fingerprints,
    ).findings


def validate_findings_with_diagnostics(
    findings: Iterable[Finding],
    changed: dict[str, ChangedFile],
    min_confidence: float,
    include_low: bool,
    max_findings: int,
    existing_fingerprints: set[str] | None = None,
) -> FindingValidationResult:
    existing = existing_fingerprints or set()
    accepted: list[Finding] = []
    seen: set[str] = set()
    changed_file_count = 0
    changed_line_count = 0
    confidence_count = 0
    severity_count = 0
    evidence_count = 0
    deduplicated_count = 0
    for finding in findings:
        try:
            finding.path = normalize_path(finding.path)
        except ValueError:
            continue
        file = changed.get(finding.path)
        mark = fingerprint(finding)
        if not file:
            continue
        changed_file_count += 1
        if finding.line not in file.added_lines:
            continue
        changed_line_count += 1
        if finding.confidence < min_confidence:
            continue
        confidence_count += 1
        if finding.severity == Severity.LOW and not include_low:
            continue
        severity_count += 1
        if not _has_policy_evidence(finding):
            continue
        evidence_count += 1
        if mark in existing or mark in seen:
            continue
        seen.add(mark)
        accepted.append(finding)
        deduplicated_count += 1
    accepted.sort(key=lambda item: (-ORDER[item.severity], -item.confidence, item.path, item.line))
    return FindingValidationResult(
        findings=accepted[:max_findings],
        changed_file_count=changed_file_count,
        changed_line_count=changed_line_count,
        confidence_count=confidence_count,
        severity_count=severity_count,
        evidence_count=evidence_count,
        deduplicated_count=deduplicated_count,
    )
