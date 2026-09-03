from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.admin.oauth import router as oauth_router
from app.admin.routes import router as admin_router
from app.config import get_settings
from app.db.session import get_session_factory
from app.github.webhook import router as webhook_router
from app.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Forces production URL and session-secret validation before accepting traffic.
    get_settings()
    yield


app = FastAPI(title="GitHub Codex Review Bot", version="0.1.0", lifespan=lifespan)
app.include_router(webhook_router)
app.include_router(admin_router)
app.include_router(oauth_router)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    async with get_session_factory()() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}
