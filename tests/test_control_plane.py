import pytest

from app.admin.control_plane import (
    BlockReason,
    PolicySource,
    apply_global_policy_patch,
    apply_repository_policy_patch,
    resolve_repository_policy,
    validate_global_policy_patch,
    validate_repository_policy_patch,
)
from app.config import Settings
from app.jobs.models import GlobalReviewSettings, RepositorySettings


def _settings() -> Settings:
    return Settings(
        environment="test",
        github_app_id="1",
        github_private_key="test-key",
        github_webhook_secret="test-secret",
        github_bot_login="review-bot[bot]",
        admin_session_secret="test-session-secret",
        codex_model_allowlist="approved-model",
    )


def _repository(**values: object) -> RepositorySettings:
    return RepositorySettings(
        installation_id=7,
        repository_owner="tenant",
        repository_name="repo",
        installed=True,
        enabled=False,  # legacy materialized value must not win over NULL
        auto_review=False,
        **values,
    )


def test_inherited_policy_ignores_stale_materialized_flags() -> None:
    global_settings = GlobalReviewSettings(
        id=1,
        enabled=True,
        auto_review_enabled=True,
        command_review_enabled=True,
    )
    policy = resolve_repository_policy(global_settings, _repository(), _settings())
    assert policy.enabled is True
    assert policy.auto_review is True
    assert policy.provenance["review_enabled"] is PolicySource.GLOBAL_DEFAULT
    assert policy.provenance["auto_review_enabled"] is PolicySource.GLOBAL_DEFAULT
    assert BlockReason.REVIEW_DISABLED not in policy.block_reasons


def test_explicit_off_remains_off_and_is_provenanced() -> None:
    repository = _repository(override_enabled=False, override_auto_review_enabled=False)
    policy = resolve_repository_policy(
        GlobalReviewSettings(id=1, enabled=True, auto_review_enabled=True),
        repository,
        _settings(),
    )
    assert policy.enabled is False
    assert policy.auto_review is False
    assert policy.provenance["review_enabled"] is PolicySource.REPOSITORY_OVERRIDE
    assert policy.provenance["auto_review_enabled"] is PolicySource.REPOSITORY_OVERRIDE
    assert BlockReason.REVIEW_DISABLED in policy.block_reasons


def test_paused_policy_is_not_execution_eligible() -> None:
    policy = resolve_repository_policy(
        GlobalReviewSettings(id=1, processing_paused=True), _repository(), _settings()
    )
    assert policy.manual_review_eligible is False
    assert policy.automatic_review_eligible is False
    assert BlockReason.GLOBAL_PROCESSING_PAUSED in policy.block_reasons


def test_policy_patch_preserves_unspecified_fields() -> None:
    repository = _repository(override_enabled=False, override_auto_review_enabled=True)
    repository.override_model = "approved-model"
    changed = apply_repository_policy_patch(
        repository,
        {"override_enabled": None},
        _settings(),
    )
    assert changed == ("override_enabled",)
    assert repository.override_enabled is None
    assert repository.override_auto_review_enabled is True
    assert repository.override_model == "approved-model"


def test_policy_patch_rejects_unknown_and_unsafe_values() -> None:
    with pytest.raises(ValueError, match="unsupported policy fields"):
        validate_repository_policy_patch({"enabled": False}, _settings())
    with pytest.raises(ValueError, match="minimum confidence"):
        validate_repository_policy_patch({"override_minimum_confidence": 0.5}, _settings())


def test_global_profile_patch_records_inherited_provenance() -> None:
    global_settings = GlobalReviewSettings(id=1, profile_defaults_inherited=False)
    changed = apply_global_policy_patch(
        global_settings,
        {"review_profile": "THOROUGH"},
        _settings(),
    )
    assert "profile_defaults_inherited" in changed
    assert global_settings.profile_defaults_inherited is True


def test_global_threshold_patch_marks_custom_provenance() -> None:
    global_settings = GlobalReviewSettings(id=1, profile_defaults_inherited=True)
    apply_global_policy_patch(
        global_settings,
        {"minimum_confidence": 0.85},
        _settings(),
    )
    assert global_settings.profile_defaults_inherited is False


def test_global_patch_rejects_unknown_and_unsafe_values() -> None:
    with pytest.raises(ValueError, match="unsupported policy fields"):
        validate_global_policy_patch({"processing_paused": True}, _settings())
    with pytest.raises(ValueError, match="outside safety limit"):
        validate_global_policy_patch({"codex_timeout_seconds": 1}, _settings())
