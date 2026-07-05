import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select

from app.config import settings
from app.core.deps import SESSION_COOKIE, SESSION_TTL_SECONDS
from app.db import async_session
from app.models.user import User, UserRole
from app.redis import redis_client

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

OAUTH_STATE_TTL_SECONDS = 600


@router.get("/login")
async def login() -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    await redis_client.set(f"oauth:state:{state}", "1", ex=OAUTH_STATE_TTL_SECONDS)

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/callback/google")
async def callback_google(code: str, state: str) -> RedirectResponse:
    stored = await redis_client.getdel(f"oauth:state:{state}")
    if not stored:
        raise HTTPException(status_code=400, detail="invalid or expired oauth state")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="google token exchange failed")
    tokens = token_resp.json()

    idinfo = google_id_token.verify_oauth2_token(
        tokens["id_token"], google_requests.Request(), settings.google_client_id,
    )

    google_sub = idinfo["sub"]
    email = idinfo["email"]
    name = idinfo.get("name")
    picture_url = idinfo.get("picture")
    role = UserRole.ADMIN if email.lower() in settings.admin_email_set else UserRole.USER

    async with async_session() as db:
        result = await db.execute(select(User).where(User.google_sub == google_sub))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(google_sub=google_sub, email=email, name=name, picture_url=picture_url, role=role)
            db.add(user)
        else:
            user.email = email
            user.name = name
            user.picture_url = picture_url
            if role == UserRole.ADMIN:
                user.role = role
        await db.commit()
        await db.refresh(user)
        user_id = user.id

    sid = secrets.token_urlsafe(32)
    await redis_client.hset(f"sess:{sid}", mapping={"user_id": str(user_id)})
    await redis_client.expire(f"sess:{sid}", SESSION_TTL_SECONDS)

    response = RedirectResponse(f"{settings.frontend_origin}/dashboard")
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        await redis_client.delete(f"sess:{sid}")

    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
