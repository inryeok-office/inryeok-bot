import pytest
from pydantic import ValidationError

from app.codex.schemas import Category, Finding, ReviewOutput, Severity
from app.review.deduplicator import fingerprint
from app.review.diff import ChangedFile
from app.review.validator import validate_findings, validate_findings_with_diagnostics


def finding(**changes):
    values = {
        "path": "app.py",
        "line": 2,
        "category": Category.BUG,
        "severity": Severity.HIGH,
        "confidence": 0.9,
        "title": "Crash",
        "body": "None is dereferenced here.",
    }
    values.update(changes)
    return Finding(**values)


def test_schema_and_filters_sort_limit_deduplicate():
    changed = {"app.py": ChangedFile("app.py", frozenset({2, 3, 4}))}
    items = [
        finding(),
        finding(),
        finding(line=3, confidence=0.4),
        finding(line=4, severity=Severity.LOW, title="Low"),
    ]
    assert len(validate_findings(items, changed, 0.8, False, 10)) == 1
    assert len(validate_findings(items, changed, 0.0, True, 2)) == 2


def test_same_problem_is_not_duplicated_across_categories():
    changed = {"app.py": ChangedFile("app.py", frozenset({2}))}
    items = [finding(category=Category.BUG), finding(category=Category.CORRECTNESS)]
    assert len(validate_findings(items, changed, 0.9, False, 10)) == 1


def test_invalid_codex_json_rejected():
    with pytest.raises(ValidationError):
        ReviewOutput.model_validate({"summary": "x", "findings": [{"path": "a"}]})


def test_category_schema_rejects_unknown_value():
    with pytest.raises(ValidationError):
        finding(category="STYLE")


def test_unsubstantiated_simplification_and_performance_are_removed():
    changed = {"app.py": ChangedFile("app.py", frozenset({2, 3, 4}))}
    items = [
        finding(
            category=Category.SIMPLIFICATION,
            title="Shorter code",
            body="This could be written in fewer lines.",
        ),
        finding(
            line=3,
            category=Category.PERFORMANCE,
            title="Could be faster",
            body="This expression might be marginally faster another way.",
        ),
        finding(
            line=4,
            category=Category.PERFORMANCE,
            title="N+1 query in loop",
            body="Each row issues a database query, causing N+1 requests as the list grows.",
        ),
    ]
    accepted = validate_findings(items, changed, 0.9, False, 10)
    assert [(item.line, item.category) for item in accepted] == [(4, Category.PERFORMANCE)]


def test_unchanged_line_low_default_and_existing_fingerprint_are_removed():
    changed = {"app.py": ChangedFile("app.py", frozenset({2, 3}))}
    duplicate = finding()
    items = [duplicate, finding(line=3, severity=Severity.LOW, title="Low"), finding(line=99)]
    assert validate_findings(items, changed, 0.9, False, 10, {fingerprint(duplicate)}) == []


def test_validation_diagnostics_explain_each_filter_stage():
    changed = {"app.py": ChangedFile("app.py", frozenset({2, 3, 4}))}
    result = validate_findings_with_diagnostics(
        [
            finding(line=99, title="Unchanged"),
            finding(line=2, confidence=0.5, title="Low confidence"),
            finding(line=3, severity=Severity.LOW, title="Low severity"),
            finding(line=4, title="Accepted"),
        ],
        changed,
        0.9,
        False,
        10,
    )
    assert result.changed_file_count == 4
    assert result.changed_line_count == 3
    assert result.confidence_count == 2
    assert result.severity_count == 1
    assert result.evidence_count == 1
    assert result.deduplicated_count == 1
    assert result.published_count == 1
