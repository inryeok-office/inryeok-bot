import json
from pathlib import Path

import pytest

from app.codex.prompt import build_prompt
from app.codex.schemas import Finding
from app.review.contracts import PROMPT_VERSION_MAX_LENGTH, validate_prompt_version
from app.review.diff import parse_unified_diff
from app.review.domains import PROMPT_VERSION
from app.review.risks import detect_risks
from app.review.validator import validate_findings_with_diagnostics


def test_prompt_version_and_ordered_risk_questions():
    plain = build_prompt("a" * 40, "b" * 40, ["a.py"], {})
    assert PROMPT_VERSION == "detailed-review-v5-collaborative-inline"
    assert len(PROMPT_VERSION) == 39
    assert validate_prompt_version(PROMPT_VERSION) == PROMPT_VERSION
    assert PROMPT_VERSION_MAX_LENGTH == 128
    assert "Do not return empty" in plain and "Never invent a defect" in plain
    assert "webhook URL tokens" not in plain
    diff = Path("tests/fixtures/pr190_initial.diff").read_text(encoding="utf-8")
    prompt = build_prompt("a" * 40, "b" * 40, [], {"risk_signals": detect_risks(diff)}, diff)
    assert prompt.index("webhook URL tokens") < prompt.index("whole batch exceed")
    assert "root cause of an exception chain" in prompt
    assert "SQL/JDBC" in prompt


def test_prompt_version_contract_rejects_oversized_identifier() -> None:
    with pytest.raises(ValueError, match="exceeds 128"):
        validate_prompt_version("p" * 129)


def test_all_five_pr190_candidates_survive_grounding_contract():
    manifest = json.loads(Path("tests/fixtures/pr190_initial_manifest.json").read_text("utf-8"))
    diff = Path("tests/fixtures/pr190_initial.diff").read_text("utf-8")
    changed = parse_unified_diff(diff)
    candidates = []
    for item in manifest["expected"]:
        candidates.append(
            Finding(
                scope=item["scope"],
                path=item["path"],
                category=item["category"],
                severity=item["severities"][0],
                confidence=0.96,
                title=item["id"],
                body="Changed logging causes observable failure.",
                condition="new throwable payload is processed",
                impact="credentials leak or logs lost",
                evidence=" -> ".join(item["causal_chain"]),
                changed_symbol=item["symbol"],
                introduced_by_pr=True,
                relation_to_change=item["relation_to_change"],
                causal_evidence=(
                    f"{item['symbol']} now adds exception data causing new output failure"
                ),
                changed_file_anchor={
                    "kind": "ADDED_LINE",
                    "path": item["path"],
                    "line": min(changed[item["path"]].added_lines),
                },
                suggested_fix="Correct the changed logging contract.",
            )
        )
    result = validate_findings_with_diagnostics(candidates, changed, 0.9, False, 10)
    assert {finding.title for finding in result.findings} == {
        item["id"] for item in manifest["expected"]
    }
    assert not result.rejection_counts
