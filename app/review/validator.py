from collections.abc import Iterable

from app.codex.schemas import Finding, Severity
from app.review.deduplicator import fingerprint
from app.review.diff import ChangedFile, normalize_path

ORDER = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}


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
            or mark in existing
            or mark in seen
        ):
            continue
        seen.add(mark)
        accepted.append(finding)
    accepted.sort(key=lambda item: (-ORDER[item.severity], -item.confidence, item.path, item.line))
    return accepted[:max_findings]
