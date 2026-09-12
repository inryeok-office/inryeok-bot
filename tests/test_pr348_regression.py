import hashlib
import json
from pathlib import Path

from app.codex.schemas import (
    Category,
    ChangedAnchorKind,
    ChangedFileAnchor,
    ChangeRelation,
    Finding,
    FindingScope,
    Severity,
)
from app.review.diff import ChangedFile, parse_unified_diff
from app.review.validator import validate_findings_with_diagnostics

FIXTURE = Path("tests/fixtures/pr348.diff")
MANIFEST = Path("tests/fixtures/pr348_manifest.json")


def _changed() -> dict[str, ChangedFile]:
    return parse_unified_diff(FIXTURE.read_text(encoding="utf-8"))


def _file_finding(path: str, **values: object) -> Finding:
    base: dict[str, object] = {
        "scope": FindingScope.FILE,
        "path": path,
        "line": None,
        "category": Category.CORRECTNESS,
        "severity": Severity.MEDIUM,
        "confidence": 0.94,
        "title": "Concrete changed-file problem",
        "body": "The changed contract has a concrete behavior impact under a stated condition.",
        "condition": "the changed path is used by an affected request",
        "impact": "the request can return an incorrect result",
        "evidence": "the changed implementation contains the relevant behavior",
    }
    base.update(values)
    return Finding(**base)


def _cross_finding(anchor: ChangedFileAnchor, **values: object) -> Finding:
    base: dict[str, object] = {
        "scope": FindingScope.PR,
        "path": None,
        "category": Category.API_CONTRACT,
        "severity": Severity.MEDIUM,
        "confidence": 0.94,
        "title": "Concrete cross-file contract problem",
        "body": (
            "The changed API creates a concrete compatibility failure for an unchanged consumer."
        ),
        "condition": "the unchanged consumer calls the changed contract",
        "impact": "the consumer receives an incompatible result",
        "evidence": (
            "the caller expects a contract that the changed implementation no longer provides"
        ),
        "relation_to_change": ChangeRelation.CROSS_FILE_IMPACT,
        "introduced_by_pr": True,
        "causal_evidence": (
            "changed API contract causes the unchanged caller to receive an incompatible result"
        ),
        "changed_file_anchor": anchor,
    }
    base.update(values)
    return Finding(**base)


def test_pr348_fixture_is_the_historical_range_and_contains_all_review_surfaces() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["base_sha"] == "69f1c11e9c8594af8ed9f5bdf34fc18864983009"
    assert manifest["head_sha"] == "237b5590896b795175aaecf7d0d45a204ec78286"
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == manifest["diff_sha256"]
    changed = _changed()
    assert len(changed) == 13
    assert any(path.endswith("DiscordDeliveryAdminQueryServiceImpl.kt") for path in changed)
    diff = FIXTURE.read_text(encoding="utf-8")
    assert "@Transactional(readOnly = true)" in diff
    assert "findRecentByTargetGrade" in diff
    assert "DISCORD_DELIVERY_INVALID_TARGET_GRADE" in diff
    assert "findRecentByTargetGrade" in diff.split("diff --git", 8)[-1]


def test_changed_file_finding_survives_without_a_fabricated_inline_line() -> None:
    changed = _changed()
    path = (
        "src/main/kotlin/team/inreok/getiserver/domain/notification/service/impl/"
        "DiscordDeliveryAdminQueryServiceImpl.kt"
    )
    result = validate_findings_with_diagnostics(
        [_file_finding(path, category=Category.TRANSACTION, title="Transaction guard removed")],
        changed,
        0.9,
        True,
        10,
    )
    assert len(result.findings) == 1
    assert result.findings[0].scope == FindingScope.FILE


def test_changed_api_anchor_allows_concrete_cross_file_impact() -> None:
    changed = _changed()
    path = (
        "src/main/kotlin/team/inreok/getiserver/domain/notification/repository/"
        "DiscordDeliveryRepository.kt"
    )
    line = min(changed[path].added_lines)
    result = validate_findings_with_diagnostics(
        [
            _cross_finding(
                ChangedFileAnchor(kind=ChangedAnchorKind.ADDED_LINE, path=path, line=line)
            )
        ],
        changed,
        0.9,
        True,
        10,
    )
    assert len(result.findings) == 1
    assert result.rejection_counts == {}


def test_api_contract_finding_is_allowed_when_its_changed_file_evidence_is_concrete() -> None:
    changed = _changed()
    path = (
        "src/main/kotlin/team/inreok/getiserver/domain/notification/controller/"
        "DiscordDeliveryAdminController.kt"
    )
    result = validate_findings_with_diagnostics(
        [
            _file_finding(
                path,
                category=Category.API_CONTRACT,
                title="Error code and API contract diverge",
                condition="clients receive the new invalid-grade response",
                impact="the documented 400 response does not describe the new error code",
                evidence=(
                    "the controller and error enum changed without the corresponding "
                    "API contract update"
                ),
            )
        ],
        changed,
        0.9,
        True,
        10,
    )
    assert len(result.findings) == 1


def test_query_similarity_is_not_an_independent_required_finding() -> None:
    changed = _changed()
    result = validate_findings_with_diagnostics([], changed, 0.9, True, 10)
    assert result.findings == []
    assert (
        "similar-query-maintenance-risk"
        in json.loads(MANIFEST.read_text(encoding="utf-8"))["non_required_case"]
    )


def test_unchanged_file_bug_and_filename_only_causality_are_rejected() -> None:
    changed = _changed()
    unchanged = _file_finding(
        "src/main/kotlin/team/inreok/getiserver/domain/job/entity/Job.kt",
        relation_to_change=ChangeRelation.DIRECT_CHANGE,
        introduced_by_pr=True,
    )
    filename_only = _cross_finding(
        ChangedFileAnchor(
            kind=ChangedAnchorKind.CHANGED_FILE,
            path="src/main/kotlin/team/inreok/getiserver/domain/notification/repository/DiscordDeliveryRepository.kt",
            line=None,
        ),
        causal_evidence="DiscordDeliveryRepository.kt",
    )
    result = validate_findings_with_diagnostics([unchanged, filename_only], changed, 0.9, True, 10)
    assert result.findings == []
    assert result.rejection_counts == {
        "PATH_NOT_CHANGED": 1,
        "INVALID_CROSS_FILE_IMPACT": 1,
    }
    assert [item.finding_index for item in result.rejection_diagnostics] == [1, 2]
    assert result.rejection_diagnostics[1].expected_anchor_kind == "ADDED_LINE"
    assert result.rejection_diagnostics[1].anchor_matches is False


def test_deleted_guard_can_use_file_scope_and_missing_anchor_is_diagnosed() -> None:
    changed = {"src/guard.kt": ChangedFile("src/guard.kt", frozenset())}
    file_result = validate_findings_with_diagnostics(
        [_file_finding("src/guard.kt", category=Category.SECURITY)],
        changed,
        0.9,
        True,
        10,
    )
    assert len(file_result.findings) == 1
    cross = _cross_finding(
        ChangedFileAnchor(kind=ChangedAnchorKind.ADDED_LINE, path="src/guard.kt", line=1)
    )
    missing = cross.model_copy(
        update={
            "scope": FindingScope.FILE,
            "path": "src/guard.kt",
            "changed_file_anchor": None,
        }
    )
    rejected = validate_findings_with_diagnostics([missing], changed, 0.9, True, 10)
    assert rejected.rejection_counts == {"CAUSAL_ANCHOR_MISSING": 1}
    diagnostic = rejected.rejection_diagnostics[0]
    assert diagnostic.expected_anchor_kind == "CHANGED_FILE"
    assert diagnostic.anchor_matches is False
