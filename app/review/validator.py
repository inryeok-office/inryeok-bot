import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.codex.schemas import (
    Category,
    ChangedAnchorKind,
    ChangeRelation,
    Finding,
    FindingScope,
    Severity,
)
from app.review.deduplicator import fingerprint
from app.review.diff import ChangedFile, normalize_path

ORDER = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}
DIAGNOSTIC_SCHEMA_VERSION = "1"


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
    rejection_diagnostics: list["FindingRejectionDiagnostic"] = field(default_factory=list)

    @property
    def published_count(self) -> int:
        return len(self.findings)

    @property
    def inline_count(self) -> int:
        return sum(item.scope == FindingScope.LINE for item in self.findings)

    @property
    def summary_count(self) -> int:
        return self.published_count - self.inline_count


@dataclass(frozen=True)
class FindingRejectionDiagnostic:
    """Safe metadata for one schema-valid finding rejected before publication."""

    finding_index: int
    scope: str
    category: str
    relation_to_change: str | None
    introduced_by_pr: bool | None
    severity: str
    confidence: float
    rejection_stage: str
    rejection_reason: str
    path_is_changed: bool
    changed_symbol_present: bool
    causal_evidence_present: bool
    expected_anchor_kind: str | None
    anchor_matches: bool | None
    diagnostic_schema_version: str = DIAGNOSTIC_SCHEMA_VERSION


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
CAUSAL_MARKERS = {
    "because",
    "causes",
    "caused",
    "caller",
    "callee",
    "contract",
    "depends",
    "expects",
    "returns",
    "fails",
    "failure",
    "breaks",
    "incompatible",
    "when",
    "due",
    "remove",
    "bypass",
    "호출",
    "호출자",
    "계약",
    "반환",
    "기대",
    "실패",
    "영향",
    "제거",
    "우회",
    "때문",
    "의존",
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


def _relation(finding: Finding) -> ChangeRelation:
    if finding.relation_to_change is not None:
        return finding.relation_to_change
    return {
        FindingScope.LINE: ChangeRelation.DIRECT_CHANGE,
        FindingScope.FILE: ChangeRelation.CHANGED_FILE_CONTEXT,
        FindingScope.PR: ChangeRelation.PR_WIDE,
    }[finding.scope]


def _path_is_changed(finding: Finding, changed: dict[str, ChangedFile]) -> bool:
    if finding.path is None:
        return False
    try:
        return normalize_path(finding.path) in changed
    except ValueError:
        return False


def _expected_anchor_kind(
    finding: Finding, changed: dict[str, ChangedFile]
) -> ChangedAnchorKind | None:
    relation = _relation(finding)
    if relation not in {ChangeRelation.CROSS_FILE_IMPACT, ChangeRelation.PR_WIDE}:
        return None
    candidate_path = None
    if finding.changed_file_anchor is not None:
        try:
            candidate_path = normalize_path(finding.changed_file_anchor.path)
        except ValueError:
            candidate_path = None
    if candidate_path is None and finding.path is not None:
        try:
            candidate_path = normalize_path(finding.path)
        except ValueError:
            candidate_path = None
    candidate = changed.get(candidate_path) if candidate_path else None
    if candidate is not None and not candidate.added_lines:
        return ChangedAnchorKind.CHANGED_FILE
    return ChangedAnchorKind.ADDED_LINE


def _anchor_matches(
    finding: Finding, changed: dict[str, ChangedFile], expected: ChangedAnchorKind | None
) -> bool:
    anchor = finding.changed_file_anchor
    if anchor is None or expected is None:
        return False
    try:
        path = normalize_path(anchor.path)
    except ValueError:
        return False
    file = changed.get(path)
    if file is None or anchor.kind != expected:
        return False
    if finding.scope == FindingScope.FILE and finding.path is not None:
        try:
            if normalize_path(finding.path) != path:
                return False
        except ValueError:
            return False
    if anchor.kind == ChangedAnchorKind.CHANGED_FILE:
        return anchor.line is None
    return anchor.line is not None and anchor.line in file.added_lines


def _causal_evidence_is_specific(finding: Finding) -> bool:
    if not finding.causal_evidence:
        return False
    text = finding.causal_evidence.casefold()
    for value in (
        finding.changed_file_anchor.path if finding.changed_file_anchor else None,
        finding.changed_symbol,
    ):
        if value:
            text = text.replace(value.casefold(), " ")
    words = re.findall(r"[a-z0-9_./:-]+|[^\W\d_]+", text)
    return len(words) >= 3 and any(marker in text for marker in CAUSAL_MARKERS)


def _scope_relation_is_valid(finding: Finding, relation: ChangeRelation) -> bool:
    if finding.scope == FindingScope.LINE:
        return relation in {ChangeRelation.DIRECT_CHANGE, ChangeRelation.CROSS_FILE_IMPACT}
    if finding.scope == FindingScope.FILE:
        return relation in {
            ChangeRelation.DIRECT_CHANGE,
            ChangeRelation.CHANGED_FILE_CONTEXT,
            ChangeRelation.CROSS_FILE_IMPACT,
        }
    return relation in {ChangeRelation.CROSS_FILE_IMPACT, ChangeRelation.PR_WIDE}


def _suggested_fix_is_in_scope(finding: Finding, changed: dict[str, ChangedFile]) -> bool:
    if finding.relation_to_change is None or not finding.suggested_fix:
        return True
    fix = finding.suggested_fix.casefold()
    broad = (
        "repository-wide",
        "entire repository",
        "all files",
        "other files",
        "unrelated",
        "refactor",
    )
    if not any(token in fix for token in broad):
        return True
    anchors = [path.casefold() for path in changed]
    if finding.changed_symbol:
        anchors.append(finding.changed_symbol.casefold())
    return any(anchor in fix for anchor in anchors)


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
    rejection_diagnostics: list[FindingRejectionDiagnostic] = []
    accepted_indices: dict[int, int] = {}

    def reject(
        reason: str,
        stage: str,
        finding: Finding,
        finding_index: int,
        expected_anchor_kind: ChangedAnchorKind | None = None,
        anchor_matches: bool | None = None,
    ) -> None:
        rejected[reason] += 1
        rejection_diagnostics.append(
            FindingRejectionDiagnostic(
                finding_index=finding_index,
                scope=finding.scope.value,
                category=finding.category.value,
                relation_to_change=(
                    finding.relation_to_change.value if finding.relation_to_change else None
                ),
                introduced_by_pr=finding.introduced_by_pr,
                severity=finding.severity.value,
                confidence=finding.confidence,
                rejection_stage=stage,
                rejection_reason=reason,
                path_is_changed=_path_is_changed(finding, changed),
                changed_symbol_present=bool(finding.changed_symbol),
                causal_evidence_present=bool(finding.causal_evidence),
                expected_anchor_kind=(expected_anchor_kind.value if expected_anchor_kind else None),
                anchor_matches=anchor_matches,
            )
        )

    minimum_order = ORDER[Severity(minimum_severity)]
    for finding_index, finding in enumerate(findings, 1):
        if finding.relation_to_change is None and finding.causal_evidence is not None:
            reject("CHANGE_RELATION_MISSING", "relation", finding, finding_index)
            continue
        relation = _relation(finding)
        if relation == ChangeRelation.PRE_EXISTING_UNRELATED or finding.introduced_by_pr is False:
            reject("PRE_EXISTING_UNRELATED", "relation", finding, finding_index)
            continue
        if finding.relation_to_change is not None and finding.introduced_by_pr is not True:
            reject("CHANGE_RELATION_MISSING", "relation", finding, finding_index)
            continue
        if not _scope_relation_is_valid(finding, relation):
            reject("INVALID_SCOPE_RELATION", "relation", finding, finding_index)
            continue
        if finding.relation_to_change is not None and relation in {
            ChangeRelation.CROSS_FILE_IMPACT,
            ChangeRelation.PR_WIDE,
        }:
            expected_anchor = _expected_anchor_kind(finding, changed)
            anchor_matches = _anchor_matches(finding, changed, expected_anchor)
            if finding.changed_file_anchor is None:
                reject(
                    "CAUSAL_ANCHOR_MISSING",
                    "grounding",
                    finding,
                    finding_index,
                    expected_anchor,
                    False,
                )
                continue
            if not anchor_matches:
                reject(
                    "INVALID_CROSS_FILE_IMPACT",
                    "grounding",
                    finding,
                    finding_index,
                    expected_anchor,
                    False,
                )
                continue
            if not finding.causal_evidence:
                reject(
                    "CAUSAL_EVIDENCE_MISSING",
                    "grounding",
                    finding,
                    finding_index,
                    expected_anchor,
                    True,
                )
                continue
            if not _causal_evidence_is_specific(finding):
                reject(
                    "CAUSAL_EVIDENCE_NOT_SPECIFIC",
                    "grounding",
                    finding,
                    finding_index,
                    expected_anchor,
                    True,
                )
                continue
        if not _suggested_fix_is_in_scope(finding, changed):
            reject("OUT_OF_SCOPE_SUGGESTED_FIX", "scope", finding, finding_index)
            continue
        if not _structured_evidence_is_complete(finding):
            reject("MISSING_STRUCTURED_EVIDENCE", "evidence", finding, finding_index)
            continue
        if finding.scope == FindingScope.LINE:
            if finding.path is None or finding.line is None or finding.side != "RIGHT":
                reject("INVALID_LOCATION", "scope", finding, finding_index)
                continue
            try:
                finding.path = normalize_path(finding.path)
            except ValueError:
                reject("INVALID_PATH", "scope", finding, finding_index)
                continue
            file = changed.get(finding.path)
            if not file:
                reject(
                    "PATH_NOT_CHANGED"
                    if finding.relation_to_change is not None
                    else "FILE_NOT_CHANGED",
                    "scope",
                    finding,
                    finding_index,
                )
                continue
        elif finding.scope == FindingScope.FILE:
            if finding.path is None:
                reject("INVALID_LOCATION", "scope", finding, finding_index)
                continue
            try:
                finding.path = normalize_path(finding.path)
            except ValueError:
                reject("INVALID_PATH", "scope", finding, finding_index)
                continue
            file = changed.get(finding.path)
            if not file:
                reject(
                    "PATH_NOT_CHANGED"
                    if finding.relation_to_change is not None
                    else "FILE_NOT_CHANGED",
                    "scope",
                    finding,
                    finding_index,
                )
                continue
        elif finding.scope == FindingScope.PR:
            if finding.path is not None:
                try:
                    finding.path = normalize_path(finding.path)
                except ValueError:
                    reject("INVALID_PATH", "scope", finding, finding_index)
                    continue
                if finding.path not in changed:
                    reject(
                        "PATH_NOT_CHANGED"
                        if finding.relation_to_change is not None
                        else "FILE_NOT_CHANGED",
                        "scope",
                        finding,
                        finding_index,
                    )
                    continue
            if not changed:
                reject("NO_CHANGED_FILES", "scope", finding, finding_index)
                continue
        else:
            reject("UNSUPPORTED_SCOPE", "scope", finding, finding_index)
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
                reject(
                    "LINE_NOT_CHANGED"
                    if finding.relation_to_change is not None
                    else "LINE_NOT_RIGHT_SIDE",
                    "scope",
                    finding,
                    finding_index,
                )
                continue
            changed_line_count += 1
        if finding.confidence < min_confidence:
            reject("BELOW_CONFIDENCE", "confidence", finding, finding_index)
            continue
        confidence_count += 1
        if finding.severity == Severity.LOW and not include_low:
            reject("LOW_DISABLED", "severity", finding, finding_index)
            continue
        if ORDER[finding.severity] < minimum_order:
            reject("BELOW_SEVERITY", "severity", finding, finding_index)
            continue
        if enabled_categories and finding.category.value not in enabled_categories:
            reject("UNKNOWN_CATEGORY", "policy", finding, finding_index)
            continue
        if review_profile == "CONSERVATIVE" and finding.category in {
            Category.PERFORMANCE,
            Category.SIMPLIFICATION,
            Category.TESTING,
        }:
            reject("PROFILE_EXCLUDED", "policy", finding, finding_index)
            continue
        severity_count += 1
        if not _has_policy_evidence(finding):
            reject("EVIDENCE_NOT_SUPPORTED", "evidence", finding, finding_index)
            continue
        evidence_count += 1
        if mark in existing or mark in seen:
            reject("DUPLICATE", "deduplication", finding, finding_index)
            continue
        seen.add(mark)
        accepted.append(finding)
        accepted_indices[id(finding)] = finding_index
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
        overflow = accepted[max_findings:]
        rejected["MAX_FINDINGS_EXCEEDED"] += len(overflow)
        for finding in overflow:
            overflow_anchor_kind = _expected_anchor_kind(finding, changed)
            rejection_diagnostics.append(
                FindingRejectionDiagnostic(
                    finding_index=accepted_indices[id(finding)],
                    scope=finding.scope.value,
                    category=finding.category.value,
                    relation_to_change=(
                        finding.relation_to_change.value if finding.relation_to_change else None
                    ),
                    introduced_by_pr=finding.introduced_by_pr,
                    severity=finding.severity.value,
                    confidence=finding.confidence,
                    rejection_stage="limit",
                    rejection_reason="MAX_FINDINGS_EXCEEDED",
                    path_is_changed=_path_is_changed(finding, changed),
                    changed_symbol_present=bool(finding.changed_symbol),
                    causal_evidence_present=bool(finding.causal_evidence),
                    expected_anchor_kind=(
                        overflow_anchor_kind.value if overflow_anchor_kind else None
                    ),
                    anchor_matches=(
                        _anchor_matches(
                            finding,
                            changed,
                            overflow_anchor_kind,
                        )
                        if overflow_anchor_kind
                        else None
                    ),
                )
            )
    return FindingValidationResult(
        findings=limited,
        changed_file_count=changed_file_count,
        changed_line_count=changed_line_count,
        confidence_count=confidence_count,
        severity_count=severity_count,
        evidence_count=evidence_count,
        deduplicated_count=deduplicated_count,
        rejection_counts=dict(rejected),
        rejection_diagnostics=rejection_diagnostics,
    )
