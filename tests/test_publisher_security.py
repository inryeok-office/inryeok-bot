from app.admin.auth import AdminAuthAdapter
from app.codex.schemas import Category, Finding, Severity
from app.config import Settings
from app.github.schemas import is_review_command
from app.logging import redact
from app.review.deduplicator import fingerprint
from app.review.publisher import build_review_payload


def _finding(
    *, title: str = "nullable value check", body: str = "The value may be absent."
) -> Finding:
    return Finding(
        path="src/orders.py",
        line=12,
        category=Category.NULL_SAFETY,
        severity=Severity.HIGH,
        confidence=0.99,
        title=title,
        body=body,
    )


def test_command_parser() -> None:
    assert is_review_command("  /ReViEw  ")
    assert not is_review_command("please /review now")


def test_command_parser_ignores_markdown_code_and_quotes() -> None:
    assert not is_review_command("```\n/review\n```")
    assert not is_review_command("> /review")
    assert not is_review_command("`/review`")
    assert is_review_command("```\n/review\n```\n\n/review")
    assert not is_review_command("/review full")


def test_review_payload_renders_korean_markdown_summary() -> None:
    payload = build_review_payload([_finding()], 3, "a" * 40)

    assert payload["body"].startswith("## \ub9ac\ubdf0 \uacb0\uacfc")
    assert "\ubcc0\uacbd\ub41c **3\uac1c \ud30c\uc77c**" in payload["body"]
    assert "| \uc2ec\uac01\ub3c4 | \uac1c\uc218 |" in payload["body"]
    assert "| Critical | 0 |" in payload["body"]
    assert "| High | 1 |" in payload["body"]
    assert "| Medium | 0 |" in payload["body"]
    assert "### \uc8fc\uc694 \ub0b4\uc6a9" in payload["body"]
    assert "**HIGH \u00b7 NULL_SAFETY**" in payload["body"]
    assert payload["body"].endswith("<!-- inryeok-review:v1 -->")


def test_inline_review_keeps_valid_markdown_without_forcing_sections() -> None:
    finding = _finding(
        title="findById result check",
        body="`findById()` may return `null`.\n\n**\uc601\ud5a5**\n\nA 500 response can occur.",
    )
    inline = build_review_payload([finding], 1, "b" * 40)["comments"][0]["body"]

    assert inline.startswith("**\U0001f534 HIGH \u00b7 NULL_SAFETY**\n\n### findById result check")
    assert "`findById()`" in inline
    assert "**\uc601\ud5a5**" in inline
    assert "```" not in inline


def test_no_findings_review_uses_korean_completion_message() -> None:
    payload = build_review_payload([], 3, "a" * 40)

    assert payload["comments"] == []
    assert "### \uc644\ub8cc" in payload["body"]
    assert (
        "\uc218\uc815\uc774 \ud544\uc694\ud55c \ubb38\uc81c\ub97c "
        "\ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4." in payload["body"]
    )
    assert "### \uc8fc\uc694 \ub0b4\uc6a9" not in payload["body"]


def test_publisher_escapes_heading_control_characters_and_keeps_marker() -> None:
    payload = build_review_payload([_finding(title="heading #1")], 1, "a" * 40)

    assert "### heading \\#1" in payload["comments"][0]["body"]
    assert payload["body"].endswith("<!-- inryeok-review:v1 -->")


def test_markdown_presentation_does_not_change_fingerprint() -> None:
    finding = _finding(body="Root cause and impact.")
    before = fingerprint(finding)
    build_review_payload([finding], 1, "a" * 40)

    assert fingerprint(finding) == before


def test_secret_redaction() -> None:
    assert "hunter2" not in redact("Authorization: Bearer hunter2")
    assert "abc" not in redact("token=abc")
    assert "pw" not in redact("postgresql://reviewbot:pw@db/reviewbot")
    assert "client-value" not in redact("client_secret=client-value")
    assert "url-value" not in redact("https://example.test/callback?token=url-value")


def test_production_bypass_never_enabled() -> None:
    settings = Settings(
        environment="production",
        public_base_url="https://review.example.test",
        admin_session_secret="x" * 32,
        github_bot_login="test-bot[bot]",
        allowed_github_accounts="inryeok-office",
        admin_local_bypass=True,
    )
    assert settings.admin_bypass_enabled is False
    assert not AdminAuthAdapter(settings).verify_session(None)
