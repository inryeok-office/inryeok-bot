from app.codex.prompt import build_prompt


def test_prompt_defends_against_instructions_and_requires_korean_markdown() -> None:
    prompt = build_prompt("a" * 40, "b" * 40, ["src/app.py"], {})

    assert "entire Pull Request change range" in prompt
    assert "RIGHT-side line" in prompt
    assert "untrusted review data" in prompt
    assert "secrets or environment variables" in prompt
    assert "external commands" in prompt
    assert "JSON Schema" in prompt
    assert "build scripts" in prompt
    assert "natural Korean" in prompt
    assert "Markdown" in prompt
    assert "fenced code block" in prompt
