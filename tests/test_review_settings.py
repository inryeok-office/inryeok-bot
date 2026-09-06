import pytest

from app.config import Settings
from app.jobs.models import GlobalReviewSettings, RepositorySettings
from app.review.settings import path_is_ignored, resolve


def _settings() -> Settings:
    return Settings(
        environment="development",
        github_app_id="1",
        github_private_key="test-key",
        github_webhook_secret="test-secret",
        github_bot_login="review-bot[bot]",
        admin_session_secret="test-session-secret",
        allowed_github_accounts="example",
        codex_model_allowlist="approved-model",
    )


def _repository() -> RepositorySettings:
    return RepositorySettings(
        installation_id=1,
        repository_owner="example",
        repository_name="repo",
        enabled=True,
        installed=True,
        auto_review=True,
    )


def test_repository_null_overrides_inherit_global_defaults() -> None:
    global_settings = GlobalReviewSettings(
        id=1,
        language="en",
        review_profile="THOROUGH",
        model="approved-model",
        max_findings=8,
        minimum_confidence=0.9,
        ignored_paths="build/**\n*.lock",
    )
    effective = resolve(global_settings, _repository(), _settings())
    assert effective.language == "en"
    assert effective.review_profile == "THOROUGH"
    assert effective.model == "approved-model"
    assert effective.max_findings == 8
    assert path_is_ignored("build/output.kt", effective.ignored_paths)
    assert effective.review_on_synchronize is False
    assert effective.synchronize_debounce_seconds == 60
    assert effective.command_cooldown_seconds == 60


def test_repository_override_and_safety_bounds_apply() -> None:
    repository = _repository()
    repository.override_language = "ko"
    repository.override_max_findings = 500
    repository.override_minimum_confidence = 0.1
    repository.override_timeout_seconds = 1
    effective = resolve(GlobalReviewSettings(id=1), repository, _settings())
    assert effective.language == "ko"
    assert effective.max_findings == 50
    assert effective.minimum_confidence == 0.8
    assert effective.codex_timeout_seconds == 30


def test_disabled_legacy_repository_remains_disabled() -> None:
    repository = _repository()
    repository.enabled = False
    assert not resolve(GlobalReviewSettings(id=1), repository, _settings()).enabled


def test_invalid_model_and_outside_ignore_path_are_rejected() -> None:
    repository = _repository()
    repository.override_model = "not-allowed"
    with pytest.raises(ValueError, match="model"):
        resolve(GlobalReviewSettings(id=1), repository, _settings())
    repository.override_model = None
    repository.override_ignored_paths = "../outside/**"
    with pytest.raises(ValueError, match="ignore path"):
        resolve(GlobalReviewSettings(id=1), repository, _settings())
