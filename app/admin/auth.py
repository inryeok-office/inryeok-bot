import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session
from app.jobs.models import AdminSession

_development_csrf_secret = secrets.token_bytes(32)


@dataclass(frozen=True)
class AdminPrincipal:
    session_id: str
    github_user_id: int | None
    github_login: str
    access_token: str | None
    development: bool = False


def _secret(settings: Settings) -> bytes:
    value = settings.admin_session_secret.get_secret_value().encode()
    return value or _development_csrf_secret


def sign_session_id(session_id: str, settings: Settings) -> str:
    signature = hmac.new(_secret(settings), session_id.encode(), hashlib.sha256).hexdigest()
    return f"{session_id}.{signature}"


def verify_session_cookie(value: str | None, settings: Settings) -> str | None:
    if not value or "." not in value:
        return None
    session_id, signature = value.rsplit(".", 1)
    expected = sign_session_id(session_id, settings).rsplit(".", 1)[1]
    return session_id if hmac.compare_digest(signature, expected) else None


def encrypt_token(token: str, settings: Settings) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret(settings)).digest())
    return Fernet(key).encrypt(token.encode()).decode()


def decrypt_token(token: str, settings: Settings) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret(settings)).digest())
    try:
        return Fernet(key).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "administrator session is invalid"
        ) from exc


def csrf_token(principal: AdminPrincipal, settings: Settings) -> str:
    return hmac.new(
        _secret(settings), f"csrf:{principal.session_id}".encode(), hashlib.sha256
    ).hexdigest()


def verify_csrf(value: str, principal: AdminPrincipal, settings: Settings) -> None:
    if not value or not hmac.compare_digest(value, csrf_token(principal, settings)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid CSRF token")


async def require_admin(
    request: Request,
    admin_session: str | None = Cookie(None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AdminPrincipal:
    if settings.admin_bypass_enabled:
        principal = AdminPrincipal("development", None, "development-admin", None, True)
        request.state.admin = principal
        return principal
    if not settings.admin_oauth_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "administrator OAuth is not configured"
        )
    session_id = verify_session_cookie(admin_session, settings)
    record = await session.get(AdminSession, session_id) if session_id else None
    if record is None:
        if request.method == "GET":
            redirect_to = quote(request.url.path, safe="/")
            raise HTTPException(
                status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": f"/auth/github/login?redirect_to={redirect_to}"},
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "administrator authentication required")
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        await session.delete(record)
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "administrator session expired")
    principal = AdminPrincipal(
        record.id,
        record.github_user_id,
        record.github_login,
        decrypt_token(record.encrypted_access_token, settings),
    )
    request.state.admin = principal
    return principal


class AdminAuthAdapter:
    """Compatibility facade used by callers that only need cookie verification."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify_session(self, value: str | None) -> bool:
        return verify_session_cookie(value, self.settings) is not None
