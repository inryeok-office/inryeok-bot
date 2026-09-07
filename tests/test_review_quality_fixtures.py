"""Deterministic, non-Codex coverage for the broader review lenses.

These checks intentionally inspect the fixture corpus rather than asking a model
to review it.  They keep the evaluation useful in CI without spending Codex
quota and ensure that controls, prompt-injection data, and out-of-scope changes
remain represented.
"""

from app.codex.schemas import Category, Finding, Severity
from app.review.diff import ChangedFile
from app.review.evaluation import _groups, _expected_status
from app.review.validator import validate_findings_with_diagnostics


def test_fixture_corpus_covers_noncritical_review_conditions() -> None:
    source = "\n".join(
        contents
        for group in _groups()
        for contents in group.changed_files.values()
    ).casefold()
    required_signals = (
        "none",  # nullable and boundary handling
        "delete_all",  # data-scope correctness
        "timeout",  # external failure handling
        "dispose",  # lifecycle/concurrency
        "dangerouslysetinnerhtml",  # client-side trust boundary
        "secret",  # deployment exposure
        "train_rows",  # data leakage
        "write_output",  # CLI behavior
    )
    assert all(signal in source for signal in required_signals)


def test_fixture_corpus_includes_controls_and_out_of_scope_issue() -> None:
    groups = _groups()
    assert any("def ok" in value for value in groups[0].base_files.values())
    assert any("stable" in value for value in groups[2].base_files.values())
    # An unchanged baseline problem must not be treated as a changed finding.
    assert all(issue.path in group.changed_files for group in groups for issue in group.expected)


def test_prompt_injection_fixture_is_not_a_review_instruction() -> None:
    group = next(group for group in _groups() if group.name == "C-infra-data-library")
    payload = group.changed_files["ml/training.py"]
    assert "ignore review policy" in payload.casefold()
    assert "environment data" in payload.casefold()
    assert not any("environment data" in " ".join(issue.keywords) for issue in group.expected)


def test_expected_status_surfaces_ambiguous_matches_for_manual_review() -> None:
    group = next(group for group in _groups() if group.name == "B-web-mobile")
    finding = Finding(
        path="web/src/Search.tsx",
        line=5,
        category=Category.BUG,
        severity=Severity.MEDIUM,
        confidence=0.86,
        title="검색 요청 문제",
        body="변경된 검색 코드에서 요청 상태를 확인해야 합니다.",
    )
    statuses, unexpected = _expected_status(group, [finding])
    assert statuses["stale-response"] == "NEEDS_MANUAL_REVIEW"
    assert statuses["unsafe-html"] == "NEEDS_MANUAL_REVIEW"
    assert unexpected == 1


def test_broader_policy_keeps_evidence_and_line_gates() -> None:
    changed = {"app.py": ChangedFile("app.py", frozenset({2, 3}))}
    findings = [
        Finding(
            path="app.py",
            line=2,
            category=Category.PERFORMANCE,
            severity=Severity.LOW,
            confidence=0.86,
            title="반복 조회",
            body="루프마다 database query가 발생해 목록이 커질 때 느려집니다.",
        ),
        Finding(
            path="app.py",
            line=99,
            category=Category.BUG,
            severity=Severity.HIGH,
            confidence=0.99,
            title="기존 코드 문제",
            body="변경 라인이 아닌 기존 코드의 문제입니다.",
        ),
    ]
    result = validate_findings_with_diagnostics(
        findings,
        changed,
        min_confidence=0.8,
        include_low=True,
        max_findings=10,
        minimum_severity="LOW",
        review_profile="THOROUGH",
    )
    assert [item.line for item in result.findings] == [2]
    assert result.changed_file_count == 2
    assert result.changed_line_count == 1

