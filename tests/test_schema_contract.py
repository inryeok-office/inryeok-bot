import json
from pathlib import Path


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
