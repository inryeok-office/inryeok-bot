import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import (
    AdminPrincipal,
    encrypt_token,
    require_admin,
    sign_session_id,
    verify_csrf,
    verify_session_cookie,
)
from app.config import Settings, get_settings
from app.db.session import get_session
from app.jobs.models import AdminSession

router = APIRouter(prefix="/auth/github")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def safe_admin_redirect(value: str | None) -> str:
    if (
        not value
        or (value != "/admin" and not value.startswith("/admin/"))
        or value.startswith("//")
        or "\\" in value
    ):
        return "/admin"
    return value


def encode_oauth_state(payload: dict[str, object], settings: Settings) -> str:
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(
        settings.admin_session_secret.get_secret_value().encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def decode_oauth_state(value: str, settings: Settings) -> dict[str, object]:
    try:
        encoded, signature = value.rsplit(".", 1)
        payload = json.loads(_b64decode(encoded))
        expected = encode_oauth_state(payload, settings).rsplit(".", 1)[1]
        if not hmac.compare_digest(signature, expected) or float(payload["exp"]) < time.time():
            raise ValueError
        return dict(payload)
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid OAuth state") from exc


def _secure_cookie(settings: Settings) -> bool:
    return settings.environment == "production" or settings.public_base_url.startswith("https://")


@router.get("/login")
async def login(
    redirect_to: str | None = Query(None), settings: Settings = Depends(get_settings)
) -> RedirectResponse:
    if not settings.admin_oauth_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "administrator OAuth is not configured"
        )
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64encode(hashlib.sha256(verifier.encode()).digest())
    state_cookie = encode_oauth_state(
        {
            "state": state,
            "verifier": verifier,
            "redirect": safe_admin_redirect(redirect_to),
            "exp": int(time.time()) + 600,
        },
        settings,
    )
    query = urlencode(
        {
            "client_id": settings.admin_github_client_id,
            "redirect_uri": settings.admin_callback_url,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "allow_signup": "false",
        }
    )
    response = RedirectResponse(f"https://github.com/login/oauth/authorize?{query}", 302)
    response.set_cookie(
        "oauth_state",
        state_cookie,
        max_age=600,
        httponly=True,
        secure=_secure_cookie(settings),
        samesite="lax",
    )
    return response


@router.get("/callback")
async def callback(
    code: str,
    state: str,
    oauth_state: str | None = Cookie(None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if not oauth_state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing OAuth state")
    stored = decode_oauth_state(oauth_state, settings)
    if not hmac.compare_digest(str(stored["state"]), state):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid OAuth state")
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.admin_github_client_id,
                "client_secret": settings.admin_github_client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": settings.admin_callback_url,
                "code_verifier": str(stored["verifier"]),
            },
        )
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        if token_response.status_code >= 400 or not access_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "GitHub authorization failed")
        user_response = await client.get(
            f"{settings.github_api_url}/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if user_response.status_code >= 400:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "GitHub user lookup failed")
        user = user_response.json()
    session_id = secrets.token_urlsafe(32)
    lifetime = min(int(token_data.get("expires_in", 28800)), 28800)
    session.add(
        AdminSession(
            id=session_id,
            github_user_id=int(user["id"]),
            github_login=str(user["login"]),
            encrypted_access_token=encrypt_token(str(access_token), settings),
            expires_at=datetime.now(UTC) + timedelta(seconds=lifetime),
        )
    )
    await session.commit()
    response = RedirectResponse(safe_admin_redirect(str(stored["redirect"])), 303)
    response.set_cookie(
        "admin_session",
        sign_session_id(session_id, settings),
        max_age=lifetime,
        httponly=True,
        secure=_secure_cookie(settings),
        samesite="lax",
    )
    response.delete_cookie("oauth_state")
    return response


@router.post("/logout")
async def logout(
    csrf: str = Form(..., alias="_csrf"),
    admin_session: str | None = Cookie(None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: AdminPrincipal = Depends(require_admin),
) -> RedirectResponse:
    verify_csrf(csrf, principal, settings)
    session_id = verify_session_cookie(admin_session, settings)
    record = await session.get(AdminSession, session_id) if session_id else None
    if record:
        await session.delete(record)
        await session.commit()
    response = RedirectResponse("/auth/github/login", 303)
    response.delete_cookie("admin_session")
    return response
