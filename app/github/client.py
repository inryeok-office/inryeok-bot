import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.github.auth import InstallationTokenProvider


class GitHubAPIError(RuntimeError):
    def __init__(self, status_code: int, category: str = "GITHUB_API_ERROR") -> None:
        super().__init__(f"GitHub API request failed ({status_code})")
        self.status_code = status_code
        self.category = category
        self.retryable = status_code == 429 or status_code >= 500


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "")
    try:
        return min(30.0, max(0.0, float(retry_after)))
    except ValueError:
        return min(30.0, float(2**attempt) + 0.1)


class GitHubClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=30)
        self.tokens = InstallationTokenProvider(settings, self.http)

    async def _request(
        self, installation_id: int, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        refreshed = False
        for attempt in range(3):
            token = await self.tokens.get(installation_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            try:
                response = await self.http.request(
                    method, f"{self.settings.github_api_url}{path}", headers=headers, **kwargs
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= 2:
                    raise GitHubAPIError(599, "GITHUB_NETWORK_ERROR") from exc
                await asyncio.sleep(min(30.0, float(2**attempt) + 0.1))
                continue
            if response.status_code < 400:
                return response
            if response.status_code == 401 and not refreshed:
                self.tokens.invalidate(installation_id)
                refreshed = True
                continue
            rate_limited = response.status_code == 403 and (
                response.headers.get("X-RateLimit-Remaining") == "0"
                or "Retry-After" in response.headers
            )
            retryable = response.status_code == 429 or response.status_code >= 500 or rate_limited
            if retryable and attempt < 2:
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            category = (
                "GITHUB_RATE_LIMIT"
                if rate_limited or response.status_code == 429
                else "GITHUB_API_ERROR"
            )
            error = GitHubAPIError(response.status_code, category)
            error.retryable = retryable
            raise error
        raise GitHubAPIError(599, "GITHUB_RETRY_EXHAUSTED")

    async def get_pull_request(
        self, installation_id: int, owner: str, repo: str, number: int
    ) -> dict[str, Any]:
        return dict(
            (
                await self._request(installation_id, "GET", f"/repos/{owner}/{repo}/pulls/{number}")
            ).json()
        )

    async def _pages(self, installation_id: int, path: str) -> AsyncIterator[list[dict[str, Any]]]:
        page = 1
        while True:
            data = (
                await self._request(
                    installation_id, "GET", path, params={"per_page": 100, "page": page}
                )
            ).json()
            yield list(data)
            if len(data) < 100:
                break
            page += 1

    async def list_pull_files(
        self, installation_id: int, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        async for page in self._pages(
            installation_id, f"/repos/{owner}/{repo}/pulls/{number}/files"
        ):
            result.extend(page)
        return result

    async def list_installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            data = (
                await self._request(
                    installation_id,
                    "GET",
                    "/installation/repositories",
                    params={"per_page": 100, "page": page},
                )
            ).json()
            repositories = list(data.get("repositories", []))
            result.extend(repositories)
            if len(repositories) < 100:
                return result
            page += 1

    async def list_reviews(
        self, installation_id: int, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        async for page in self._pages(
            installation_id, f"/repos/{owner}/{repo}/pulls/{number}/reviews"
        ):
            result.extend(page)
        return result

    async def list_review_comments(
        self, installation_id: int, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        async for page in self._pages(
            installation_id, f"/repos/{owner}/{repo}/pulls/{number}/comments"
        ):
            result.extend(page)
        return result

    async def get_collaborator_permission(
        self, installation_id: int, owner: str, repo: str, username: str
    ) -> str:
        data = (
            await self._request(
                installation_id, "GET", f"/repos/{owner}/{repo}/collaborators/{username}/permission"
            )
        ).json()
        return str(data.get("permission", "none"))

    async def create_review(
        self, installation_id: int, owner: str, repo: str, number: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(
            (
                await self._request(
                    installation_id,
                    "POST",
                    f"/repos/{owner}/{repo}/pulls/{number}/reviews",
                    json=payload,
                )
            ).json()
        )

    async def _add_eyes_reaction(
        self, installation_id: int, owner: str, repo: str, path: str
    ) -> bool:
        reactions = list((await self._request(installation_id, "GET", path)).json())
        bot_login = self.settings.github_bot_login.casefold()
        if any(
            reaction.get("content") == "eyes"
            and str(reaction.get("user", {}).get("login", "")).casefold() == bot_login
            for reaction in reactions
        ):
            return False
        await self._request(installation_id, "POST", path, json={"content": "eyes"})
        return True

    async def add_pull_request_eyes_reaction(
        self, installation_id: int, owner: str, repo: str, number: int
    ) -> bool:
        return await self._add_eyes_reaction(
            installation_id, owner, repo, f"/repos/{owner}/{repo}/issues/{number}/reactions"
        )

    async def add_comment_eyes_reaction(
        self, installation_id: int, owner: str, repo: str, comment_id: int
    ) -> bool:
        return await self._add_eyes_reaction(
            installation_id,
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions",
        )

    async def create_issue_comment(
        self, installation_id: int, owner: str, repo: str, number: int, body: str
    ) -> dict[str, Any]:
        return dict(
            (
                await self._request(
                    installation_id,
                    "POST",
                    f"/repos/{owner}/{repo}/issues/{number}/comments",
                    json={"body": body},
                )
            ).json()
        )
