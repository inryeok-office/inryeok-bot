import pytest

from app.admin.control_plane import PolicySource, resolve_repository_policy
from app.codex.executor import ReviewRequest
from app.codex.runner import CodexRunner
from app.config import Settings
from app.jobs.models import GlobalReviewSettings, RepositorySettings
from app.review.model_catalog import (
    CLI_DEFAULT,
    load_catalog,
    normalize_model,
    validate_model_effort,
)


def _settings(catalog: str = "") -> Settings:
    return Settings(environment="test", codex_model_catalog_json=catalog)


def _repository() -> RepositorySettings:
    return RepositorySettings(
        installation_id=1,
        repository_owner="acme",
        repository_name="repo",
        enabled=True,
        installed=True,
        auto_review=True,
    )


def test_allowlist_is_not_a_verified_catalog() -> None:
    settings = Settings(environment="test", codex_model_allowlist="guessed-model")
    assert load_catalog(settings) == ()
    with pytest.raises(ValueError, match="not verified"):
        validate_model_effort(settings, "guessed-model", "medium")


def test_missing_catalog_file_keeps_cli_default_fallback(tmp_path) -> None:
    settings = Settings(
        environment="test",
        codex_model_catalog_file=tmp_path / "not-provisioned.json",
    )
    assert load_catalog(settings) == ()


def test_blank_catalog_file_setting_means_unconfigured() -> None:
    settings = Settings(environment="test", codex_model_catalog_file="")
    assert settings.codex_model_catalog_file is None


def test_cli_default_is_nullable_and_has_provenance() -> None:
    assert normalize_model(CLI_DEFAULT) is None
    policy = resolve_repository_policy(GlobalReviewSettings(id=1), _repository(), _settings())
    assert policy.model is None
    assert policy.provenance["model"] is PolicySource.CLI_DEFAULT


def test_catalog_metadata_and_effort_are_strictly_scoped() -> None:
    settings = _settings(
        '[{"model_id":"verified-a","supported_efforts":["low","high"],'
        '"default_effort":"low","enabled":true,"availability_status":"VERIFIED",'
        '"source":"OPERATOR","verification_id":"v-1"}]'
    )
    catalog = load_catalog(settings)
    assert catalog[0].selectable
    assert catalog[0].verification_id == "v-1"
    validate_model_effort(settings, "verified-a", "high")
    with pytest.raises(ValueError, match="not supported"):
        validate_model_effort(settings, "verified-a", "medium")


def test_executor_request_rejects_option_injection_model() -> None:
    with pytest.raises(ValueError):
        ReviewRequest(
            archive="archive",
            prompt="prompt",
            model="--config=bad",
            reasoning_effort="medium",
            execution_id="safe-execution-id-1234",
        )


@pytest.mark.asyncio
async def test_codex_runner_omits_cli_default_model_and_passes_verified_model(
    monkeypatch, tmp_path
) -> None:
    captured: list[object] = []

    class Process:
        returncode = 0

        async def communicate(self, input=None):
            return b'{"summary":"ok","findings":[]}', b""

        def kill(self):
            return None

    async def create(*args: object, **kwargs: object) -> Process:
        captured.extend(args)
        return Process()

    monkeypatch.setattr("app.codex.runner.asyncio.create_subprocess_exec", create)
    settings = _settings(
        '[{"model_id":"verified-a","supported_efforts":["medium"],'
        '"default_effort":"medium","enabled":true,"availability_status":"VERIFIED",'
        '"source":"OPERATOR"}]'
    )
    runner = CodexRunner(settings, schema_path=tmp_path / "review-schema.json")
    (tmp_path / "review-schema.json").write_text(
        '{"type":"object","properties":{"summary":{"type":"string"},'
        '"findings":{"type":"array","items":{"type":"object",'
        '"properties":{},"required":[],"additionalProperties":false}}},'
        '"required":["summary","findings"],"additionalProperties":false}',
        encoding="utf-8",
    )
    await runner.run(tmp_path, "review", reasoning_effort="medium")
    assert "--model" not in captured

    captured.clear()
    await runner.run(tmp_path, "review", model="verified-a", reasoning_effort="medium")
    assert "--model" in captured
    assert captured[captured.index("--model") + 1] == "verified-a"
