import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.codex.runner import CodexRunner, classify_codex_failure
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


@respx.mock
@pytest.mark.asyncio
async def test_github_client_refreshes_installation_token_once_on_401() -> None:
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
    tokens = respx.post("https://api.github.test/app/installations/9/access_tokens").mock(
        side_effect=[
            httpx.Response(201, json={"token": "one"}),
            httpx.Response(201, json={"token": "two"}),
        ]
    )
    request = respx.get("https://api.github.test/repos/acme/repo/pulls/1").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"number": 1})]
    )
    async with httpx.AsyncClient() as http:
        client = GitHubClient(settings, http)
        result = await client.get_pull_request(9, "acme", "repo", 1)
    assert result["number"] == 1
    assert tokens.call_count == 2
    assert request.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_github_client_retries_rate_limit_with_retry_after() -> None:
    settings = Settings(environment="test", github_api_url="https://api.github.test")
    request = respx.get("https://api.github.test/repos/acme/repo/pulls/1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"number": 1}),
        ]
    )
    async with httpx.AsyncClient() as http:
        client = GitHubClient(settings, http)

        class Tokens:
            async def get(self, installation_id: int) -> str:
                return "token"

            def invalidate(self, installation_id: int) -> None:
                pass

        client.tokens = Tokens()  # type: ignore[assignment]
        assert (await client.get_pull_request(1, "acme", "repo", 1))["number"] == 1
    assert request.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_review_listing_paginates_for_marker_lookup() -> None:
    settings = Settings(environment="test", github_api_url="https://api.github.test")
    first = [{"id": index, "body": "other"} for index in range(100)]
    first_route = respx.get(
        "https://api.github.test/repos/acme/repo/pulls/1/reviews",
        params={"per_page": "100", "page": "1"},
    ).mock(return_value=httpx.Response(200, json=first))
    second_route = respx.get(
        "https://api.github.test/repos/acme/repo/pulls/1/reviews",
        params={"per_page": "100", "page": "2"},
    ).mock(return_value=httpx.Response(200, json=[{"id": 101, "body": "marker"}]))
    async with httpx.AsyncClient() as http:
        client = GitHubClient(settings, http)

        class Tokens:
            async def get(self, installation_id: int) -> str:
                return "token"

            def invalidate(self, installation_id: int) -> None:
                pass

        client.tokens = Tokens()  # type: ignore[assignment]
        reviews = await client.list_reviews(1, "acme", "repo", 1)
    assert len(reviews) == 101
    assert first_route.call_count == 1 and second_route.call_count == 1


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
async def test_codex_runner_uses_managed_read_only_profile(monkeypatch, tmp_path) -> None:
    captured: tuple[object, ...] = ()
    captured_env: dict[str, str] = {}
    captured_cwd = None

    async def create(*args: object, **kwargs: object) -> FakeProcess:
        nonlocal captured, captured_cwd, captured_env
        captured = args
        captured_env = kwargs["env"]  # type: ignore[assignment]
        captured_cwd = kwargs["cwd"]
        return FakeProcess()

    monkeypatch.setattr("app.codex.runner.asyncio.create_subprocess_exec", create)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "must-not-leak")
    runner = CodexRunner(
        Settings(environment="test", codex_command="codex", review_timeout_seconds=30)
    )
    result = await runner.run(tmp_path, "review this")
    assert result.summary == "ok"
    assert captured[:2] == ("codex", "exec")
    assert "--output-schema" in captured and "--ignore-rules" in captured
    assert "--ignore-user-config" in captured
    assert "--ask-for-approval" in captured and "never" in captured
    assert 'default_permissions="inryeok_review_read_only"' in captured
    assert captured_cwd == tmp_path
    assert "GITHUB_WEBHOOK_SECRET" not in captured_env


@respx.mock
@pytest.mark.asyncio
async def test_eyes_reaction_is_added_once_per_bot() -> None:
    settings = Settings(
        environment="test",
        github_api_url="https://api.github.test",
        github_bot_login="reviewbot[bot]",
    )
    async with httpx.AsyncClient() as http:
        github = GitHubClient(settings, http)

        class Tokens:
            async def get(self, installation_id: int) -> str:
                return "token"

        github.tokens = Tokens()  # type: ignore[assignment]
        listed = respx.get("https://api.github.test/repos/acme/repo/issues/7/reactions").mock(
            return_value=httpx.Response(
                200, json=[{"content": "eyes", "user": {"login": "reviewbot[bot]"}}]
            )
        )
        created = respx.post("https://api.github.test/repos/acme/repo/issues/8/reactions").mock(
            return_value=httpx.Response(201, json={"id": 55})
        )
        respx.get("https://api.github.test/repos/acme/repo/issues/8/reactions").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert not await github.add_pull_request_eyes_reaction(1, "acme", "repo", 7)
        assert await github.add_pull_request_eyes_reaction(1, "acme", "repo", 8)

    assert listed.call_count == 1
    assert json.loads(created.calls[0].request.content) == {"content": "eyes"}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("usage quota exceeded", "CODEX_QUOTA"),
        ("HTTP 429 too many requests", "CODEX_RATE_LIMIT"),
        ("login required", "CODEX_AUTH"),
        ("service unavailable", "CODEX_SERVICE_UNAVAILABLE"),
        ("unexpected failure", "CODEX_FAILED"),
    ],
)
def test_codex_failure_classification(message: str, expected: str) -> None:
    error = classify_codex_failure(1, b"", message.encode())
    assert error.code == expected
    assert message not in str(error)


def test_codex_quota_retry_time_is_safely_extracted() -> None:
    error = classify_codex_failure(1, b"", b"usage quota exceeded; resets at 2030-01-02T03:04:05Z")
    assert error.code == "CODEX_QUOTA"
    assert error.retry_at is not None
    assert error.retry_at.isoformat() == "2030-01-02T03:04:05+00:00"
