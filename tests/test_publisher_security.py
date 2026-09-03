from app.admin.auth import AdminAuthAdapter
from app.codex.schemas import Category, Finding, Severity
from app.config import Settings
from app.github.schemas import is_review_command
from app.logging import redact
from app.review.publisher import build_review_payload


def test_command_parser():
    assert is_review_command("  /ReViEw  ")
    assert not is_review_command("please /review now")


def test_command_parser_ignores_markdown_code_and_quotes() -> None:
    assert not is_review_command("```\n/review\n```")
    assert not is_review_command("> /review")
    assert not is_review_command("`/review`")
    assert is_review_command("```\n/review\n```\n\n/review")
    assert not is_review_command("/review full")


def test_review_payload():
    finding = Finding(
        path="a.py",
        line=4,
        category=Category.SECURITY,
        severity=Severity.CRITICAL,
        confidence=0.99,
        title="Injection",
        body="Unsafe query",
    )
    payload = build_review_payload([finding], 3, "a" * 40, False)
    assert payload["event"] == "COMMENT"
    assert payload["comments"][0]["side"] == "RIGHT"
    assert "Critical: 1" in payload["body"]
    assert "High: 0" in payload["body"] and "Medium: 0" in payload["body"]
    assert "Head: `aaaaaaaaaaaa`" in payload["body"]
    assert payload["comments"][0]["body"].startswith("🔴 CRITICAL · Injection")


def test_secret_redaction():
    assert "hunter2" not in redact("Authorization: Bearer hunter2")
    assert "abc" not in redact("token=abc")


def test_production_bypass_never_enabled():
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
