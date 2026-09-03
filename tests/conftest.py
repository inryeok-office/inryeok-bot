import hashlib
import hmac
import json
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.github.webhook import get_github
from app.main import app


class FakeGitHub:
    permission = "write"

    def __init__(self) -> None:
        self.reactions: list[tuple[str, int]] = []

    async def get_collaborator_permission(self, *_: object) -> str:
        return self.permission

    async def get_pull_request(self, *_: object) -> dict[str, object]:
        return {"draft": False, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}

    async def list_installation_repositories(self, *_: object) -> list[dict[str, object]]:
        return [{"full_name": "acme/repo"}]

    async def add_pull_request_eyes_reaction(self, *_: object) -> bool:
        self.reactions.append(("pull_request", int(_[-1])))
        return True

    async def add_comment_eyes_reaction(self, *_: object) -> bool:
        self.reactions.append(("comment", int(_[-1])))
        return True


@pytest.fixture
async def app_client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite://",
        github_webhook_secret="test-secret",
        github_bot_login="reviewbot[bot]",
        allowed_github_accounts="acme",
    )

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    from app.config import get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_github] = lambda: FakeGitHub()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


def signed(
    payload: dict[str, object], event: str = "pull_request", delivery: str = "d-1"
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return body, {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }


@pytest.fixture
def pr_payload() -> dict[str, object]:
    return {
        "action": "opened",
        "installation": {"id": 1, "account": {"login": "Acme"}},
        "repository": {"name": "repo", "owner": {"login": "acme"}},
        "sender": {"login": "alice"},
        "pull_request": {
            "number": 7,
            "draft": False,
            "base": {"sha": "a" * 40},
            "head": {"sha": "b" * 40},
        },
    }
