import pytest
from pydantic import ValidationError

from app.codex.schemas import Finding, ReviewOutput, Severity
from app.review.diff import ChangedFile
from app.review.validator import validate_findings


def finding(**changes):
    values = {
        "path": "app.py",
        "line": 2,
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


def test_invalid_codex_json_rejected():
    with pytest.raises(ValidationError):
        ReviewOutput.model_validate({"summary": "x", "findings": [{"path": "a"}]})
