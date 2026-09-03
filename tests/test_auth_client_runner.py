import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.codex.runner import CodexRunner
from app.config import Settings
from app.github.auth import InstallationTokenProvider
from app.github.client import GitHubClient


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_request_and_cache() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    settings = Settings(
        environment="test",
        github_api_url="https://api.github.test",
        github_app_id="123",
        github_private_key=pem,
    )
    route = respx.post("https://api.github.test/app/installations/9/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "installation-value"})
    )
    async with httpx.AsyncClient() as client:
        provider = InstallationTokenProvider(settings, client)
        assert await provider.get(9) == "installation-value"
        assert await provider.get(9) == "installation-value"
    assert route.call_count == 1
    authorization = route.calls[0].request.headers["Authorization"]
    assert authorization.startswith("Bearer ey") and "installation-value" not in authorization


class FakeProcess:
    returncode = 0

    async def communicate(self, prompt: bytes) -> tuple[bytes, bytes]:
        assert b"review" in prompt
        return json.dumps({"summary": "ok", "findings": []}).encode(), b""

    def kill(self) -> None:
        pass

    def terminate(self) -> None:
        pass

    async def wait(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_codex_runner_uses_read_only_structured_exec(monkeypatch, tmp_path) -> None:
    captured: tuple[object, ...] = ()
    captured_env: dict[str, str] = {}

    async def create(*args: object, **kwargs: object) -> FakeProcess:
        nonlocal captured, captured_env
        captured = args
        captured_env = kwargs["env"]  # type: ignore[assignment]
        return FakeProcess()

    monkeypatch.setattr("app.codex.runner.asyncio.create_subprocess_exec", create)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "must-not-leak")
    runner = CodexRunner(
        Settings(environment="test", codex_command="codex", review_timeout_seconds=30)
    )
    result = await runner.run(tmp_path, "review this")
    assert result.summary == "ok"
    assert captured[:2] == ("codex", "exec")
    assert (
        "read-only" in captured and "--output-schema" in captured and "--ignore-rules" in captured
    )
    assert "GITHUB_WEBHOOK_SECRET" not in captured_env


@respx.mock
@pytest.mark.asyncio
async def test_check_run_is_created_in_progress_and_completed() -> None:
    settings = Settings(environment="test", github_api_url="https://api.github.test")
    async with httpx.AsyncClient() as http:
        github = GitHubClient(settings, http)

        class Tokens:
            async def get(self, installation_id: int) -> str:
                return "token"

        github.tokens = Tokens()  # type: ignore[assignment]
        created = respx.post("https://api.github.test/repos/acme/repo/check-runs").mock(
            return_value=httpx.Response(201, json={"id": 55})
        )
        completed = respx.patch("https://api.github.test/repos/acme/repo/check-runs/55").mock(
            return_value=httpx.Response(200, json={"id": 55})
        )
        await github.create_check_run(1, "acme", "repo", "a" * 40)
        await github.complete_check_run(1, "acme", "repo", 55, "success", "Done", "No findings")

    assert created.calls[0].request.content
    assert json.loads(created.calls[0].request.content)["status"] == "in_progress"
    completed_payload = json.loads(completed.calls[0].request.content)
    assert completed_payload["status"] == "completed"
    assert completed_payload["conclusion"] == "success"
