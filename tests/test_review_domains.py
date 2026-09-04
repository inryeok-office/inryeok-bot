import pytest

from app.codex.prompt import build_prompt
from app.review.domains import PROMPT_VERSION, detect_domains, effective_domains


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["src/main/kotlin/App.kt", "build.gradle.kts"], "BACKEND"),
        (["api/routes/users.py"], "BACKEND"),
        (["package.json", "src/App.tsx"], "WEB_FRONTEND"),
        (["pubspec.yaml", "lib/main.dart"], "MOBILE"),
        (["Dockerfile", "infra/main.tf"], "INFRASTRUCTURE"),
        (["migrations/001.sql"], "DATABASE"),
        (["models/training.py"], "DATA_AI"),
        (["src/cli/command.py"], "LIBRARY_SDK_CLI"),
    ],
)
def test_domain_detection_selects_relevant_lens(files: list[str], expected: str) -> None:
    assert "GENERAL" in detect_domains(files).domains
    assert expected in detect_domains(files).domains


def test_monorepo_can_select_multiple_domains_and_manual_keeps_general() -> None:
    detected = detect_domains(["web/src/App.tsx", "api/build.gradle.kts", "infra/main.tf"])
    assert {"GENERAL", "WEB_FRONTEND", "BACKEND", "INFRASTRUCTURE"} <= set(detected.domains)
    assert effective_domains("MANUAL", "DATABASE,WEB_FRONTEND", detected) == (
        "GENERAL",
        "DATABASE",
        "WEB_FRONTEND",
    )


def test_manual_domains_require_a_supported_selection() -> None:
    with pytest.raises(ValueError):
        effective_domains("MANUAL", "", detect_domains(["README.md"]))
    with pytest.raises(ValueError):
        effective_domains("MANUAL", "NOT_A_DOMAIN", detect_domains(["README.md"]))


def test_prompt_includes_only_selected_domain_lenses(tmp_path) -> None:
    template = tmp_path / "review.md"
    template.write_text("Core policy", encoding="utf-8")
    prompt = build_prompt(
        "a" * 40,
        "b" * 40,
        ["src/App.tsx"],
        {
            "language": "ko",
            "review_profile": "BALANCED",
            "review_domains": ["GENERAL", "WEB_FRONTEND"],
        },
        "diff",
        template,
    )
    assert PROMPT_VERSION in prompt
    assert "UI state races" in prompt
    assert "migration compatibility" not in prompt
