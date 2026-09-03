from app.codex.prompt import build_prompt


def test_prompt_defends_against_repository_instructions_and_reviews_full_range() -> None:
    prompt = build_prompt("a" * 40, "b" * 40, ["src/app.py"], {})
    lowered = prompt.casefold()
    assert "complete pull request change" in lowered
    assert "right-side line" in lowered
    assert "untrusted review data" in lowered
    assert "reveal secrets or environment variables" in lowered
    assert "execute an external command" in lowered
    assert "ignore the json schema" in lowered
    assert "do not run project build scripts" in lowered
