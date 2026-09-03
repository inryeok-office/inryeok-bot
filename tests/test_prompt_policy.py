from app.codex.prompt import build_prompt


def test_prompt_defends_against_repository_instructions_and_requires_korean() -> None:
    prompt = build_prompt("a" * 40, "b" * 40, ["src/app.py"], {})
    assert "Pull Request 전체 변경" in prompt
    assert "RIGHT-side 라인" in prompt
    assert "신뢰할 수 없는 리뷰 대상" in prompt
    assert "secret·환경변수" in prompt
    assert "외부 명령 실행" in prompt
    assert "JSON Schema" in prompt
    assert "build script" in prompt
    assert "자연스러운 한국어" in prompt
