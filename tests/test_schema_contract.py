import json
from pathlib import Path

from app.codex.schemas import ReviewOutput


def test_wire_schema_is_flat_strict_and_all_properties_required() -> None:
    schema = json.loads(Path("review-schema.json").read_text(encoding="utf-8"))
    forbidden = {"$ref", "$defs", "oneOf", "anyOf", "allOf", "if", "then", "else"}

    def walk(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            found.extend(key for key in value if key in forbidden)
            for child in value.values():
                found.extend(walk(child))
        elif isinstance(value, list):
            for child in value:
                found.extend(walk(child))
        return found

    assert not walk(schema)
    assert schema["additionalProperties"] is False
    finding = schema["properties"]["findings"]["items"]
    assert finding["additionalProperties"] is False
    assert set(finding["required"]) == set(finding["properties"])
    assert finding["properties"]["changed_file_anchor"]["additionalProperties"] is False


def test_wire_schema_accepts_all_scopes_with_null_non_applicable_fields() -> None:
    schema = json.loads(Path("review-schema.json").read_text(encoding="utf-8"))
    finding = schema["properties"]["findings"]["items"]["properties"]
    assert finding["line"]["type"] == ["integer", "null"]
    assert finding["side"]["type"] == ["string", "null"]
    assert finding["path"]["type"] == ["string", "null"]


def test_wire_anchor_shapes_are_parsed_then_checked_by_semantic_validation() -> None:
    """The executor wire model must accept every shape allowed by its JSON schema."""
    base = {
        "scope": "FILE",
        "path": "src/example.py",
        "line": None,
        "side": None,
        "review_type": "SUGGESTION",
        "category": "SIMPLIFICATION",
        "severity": "LOW",
        "confidence": 0.9,
        "title": "title",
        "body": "body",
        "condition": None,
        "impact": None,
        "evidence": None,
        "suggested_fix": None,
        "domain": None,
        "relation_to_change": "DIRECT_CHANGE",
        "introduced_by_pr": True,
        "changed_symbol": None,
        "causal_evidence": None,
        "defect_identity": None,
        "causal_chain": None,
        "why_it_matters": None,
        "suggested_action": None,
        "suggested_patch": None,
        "blocking": False,
        "style_guide_reference": None,
    }
    for anchor in (
        {"kind": "ADDED_LINE", "path": "src/example.py", "line": None},
        {"kind": "CHANGED_FILE", "path": "src/example.py", "line": 3},
    ):
        ReviewOutput.model_validate(
            {"summary": "ok", "findings": [{**base, "changed_file_anchor": anchor}]}
        )
