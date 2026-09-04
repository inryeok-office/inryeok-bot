import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt

from app.config import Settings


class GitHubAuthError(RuntimeError):
    pass


def load_private_key(settings: Settings) -> str:
    """Inline PEM wins over GITHUB_PRIVATE_KEY_PATH."""
    inline = settings.github_private_key.get_secret_value().replace("\\n", "\n").strip()
    if inline:
        return inline
    path: Path | None = settings.github_private_key_path
    if path:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GitHubAuthError("GitHub private key file could not be read") from exc
    raise GitHubAuthError("GitHub App private key is not configured")


def create_app_jwt(settings: Settings, now: int | None = None) -> str:
    timestamp = now or int(time.time())
    try:
        return jwt.encode(
            {"iat": timestamp - 60, "exp": timestamp + 540, "iss": settings.github_app_id},
            load_private_key(settings),
            algorithm="RS256",
        )
    except Exception as exc:
        raise GitHubAuthError("Unable to sign GitHub App JWT") from exc


@dataclass
class CachedToken:
    value: str
    expires_at: float


class InstallationTokenProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self._tokens: dict[int, CachedToken] = {}

    async def get(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached.expires_at > time.time() + 300:
            return cached.value
        response = await self.client.post(
            f"{self.settings.github_api_url}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {create_app_jwt(self.settings)}",
                "Accept": "application/vnd.github+json",
            },
        )
        if response.status_code >= 400:
            raise GitHubAuthError(
                f"GitHub installation token request failed ({response.status_code})"
            )
        data = response.json()
        token = str(data["token"])
        # GitHub tokens normally last one hour; parsing ISO is unnecessary for safe early refresh.
        self._tokens[installation_id] = CachedToken(token, time.time() + 3300)
        return token

    def invalidate(self, installation_id: int) -> None:
        self._tokens.pop(installation_id, None)
