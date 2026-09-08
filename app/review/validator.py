from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.codex.schemas import Category, Finding, FindingScope, Severity
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
    rejection_counts: dict[str, int] = field(default_factory=dict)

    @property
    def published_count(self) -> int:
        return len(self.findings)

    @property
    def inline_count(self) -> int:
        return sum(item.scope == FindingScope.LINE for item in self.findings)

    @property
    def summary_count(self) -> int:
        return self.published_count - self.inline_count


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


def _structured_evidence_is_complete(finding: Finding) -> bool:
    """Require context fields for non-line findings without breaking legacy LINE output."""
    if finding.scope == FindingScope.LINE:
        return True
    return bool(finding.condition and finding.impact and finding.evidence)


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
    minimum_severity: str = "LOW",
    enabled_categories: tuple[str, ...] = (),
    review_profile: str = "BALANCED",
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
    rejected: Counter[str] = Counter()

    def reject(reason: str) -> None:
        rejected[reason] += 1

    minimum_order = ORDER[Severity(minimum_severity)]
    for finding in findings:
        if not _structured_evidence_is_complete(finding):
            reject("MISSING_STRUCTURED_EVIDENCE")
            continue
        if finding.scope == FindingScope.LINE:
            if finding.path is None or finding.line is None:
                reject("INVALID_LOCATION")
                continue
            try:
                finding.path = normalize_path(finding.path)
            except ValueError:
                reject("INVALID_PATH")
                continue
            file = changed.get(finding.path)
            if not file:
                reject("FILE_NOT_CHANGED")
                continue
        elif finding.scope == FindingScope.FILE:
            if finding.path is None:
                reject("INVALID_LOCATION")
                continue
            try:
                finding.path = normalize_path(finding.path)
            except ValueError:
                reject("INVALID_PATH")
                continue
            file = changed.get(finding.path)
            if not file:
                reject("FILE_NOT_CHANGED")
                continue
        elif finding.scope == FindingScope.PR:
            if finding.path is not None:
                try:
                    finding.path = normalize_path(finding.path)
                except ValueError:
                    reject("INVALID_PATH")
                    continue
                if finding.path not in changed:
                    reject("FILE_NOT_CHANGED")
                    continue
            if not changed:
                reject("NO_CHANGED_FILES")
                continue
        else:
            reject("UNSUPPORTED_SCOPE")
            continue
        mark = fingerprint(finding)
        if finding.scope == FindingScope.LINE:
            assert finding.path is not None and finding.line is not None
            file = changed[finding.path]
        else:
            file = changed.get(finding.path) if finding.path else None
        changed_file_count += 1
        if finding.scope == FindingScope.LINE:
            assert file is not None and finding.line is not None
            if finding.line not in file.added_lines:
                reject("LINE_NOT_RIGHT_SIDE")
                continue
            changed_line_count += 1
        if finding.confidence < min_confidence:
            reject("BELOW_CONFIDENCE")
            continue
        confidence_count += 1
        if finding.severity == Severity.LOW and not include_low:
            reject("LOW_DISABLED")
            continue
        if ORDER[finding.severity] < minimum_order:
            reject("BELOW_SEVERITY")
            continue
        if enabled_categories and finding.category.value not in enabled_categories:
            reject("UNKNOWN_CATEGORY")
            continue
        if review_profile == "CONSERVATIVE" and finding.category in {
            Category.PERFORMANCE,
            Category.SIMPLIFICATION,
            Category.TESTING,
        }:
            reject("PROFILE_EXCLUDED")
            continue
        severity_count += 1
        if not _has_policy_evidence(finding):
            reject("EVIDENCE_NOT_SUPPORTED")
            continue
        evidence_count += 1
        if mark in existing or mark in seen:
            reject("DUPLICATE")
            continue
        seen.add(mark)
        accepted.append(finding)
        deduplicated_count += 1
    accepted.sort(
        key=lambda item: (
            -ORDER[item.severity],
            -item.confidence,
            item.path or "",
            item.line or 0,
        )
    )
    limited = accepted[:max_findings]
    if len(accepted) > len(limited):
        rejected["MAX_FINDINGS_EXCEEDED"] += len(accepted) - len(limited)
    return FindingValidationResult(
        findings=limited,
        changed_file_count=changed_file_count,
        changed_line_count=changed_line_count,
        confidence_count=confidence_count,
        severity_count=severity_count,
        evidence_count=evidence_count,
        deduplicated_count=deduplicated_count,
        rejection_counts=dict(rejected),
    )
