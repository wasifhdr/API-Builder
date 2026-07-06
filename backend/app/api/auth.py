import re
import secrets
import uuid
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import SESSION_COOKIE, current_user, set_session_cookie
from app.db import get_db
from app.models.user import User, UserRole
from app.redis import redis_client
from app.schemas.user import (
    ClaimUsernameRequest,
    MeOut,
    PasswordLoginRequest,
    RegisterRequest,
    UsernameAvailableOut,
)
from app.services.accounts import build_me_out
from app.services.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.services.rate_limit import record_failed_login, reset_failed_logins, too_many_failed_logins
from app.services.sessions import create_session, destroy_session
from app.services.usernames import USERNAME_HINT, is_valid_username, normalize_username

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

OAUTH_STATE_TTL_SECONDS = 600
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _request_meta(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    return user_agent, ip


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
async def callback_google(
    code: str, state: str, request: Request, db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
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
        clock_skew_in_seconds=10,
    )

    google_sub = idinfo["sub"]
    email = idinfo["email"].lower()
    name = idinfo.get("name")
    picture_url = idinfo.get("picture")
    bootstrap_role = UserRole.SUPER_ADMIN if email in settings.admin_email_set else None

    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()

    if user is None:
        # No Google identity match — try linking onto an existing account by
        # email (e.g. a password account signing in with Google for the
        # first time) before creating a brand-new one.
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    if user is None:
        user = User(
            google_sub=google_sub, email=email, name=name, picture_url=picture_url,
            role=bootstrap_role or UserRole.USER,
        )
        db.add(user)
    else:
        user.google_sub = google_sub
        user.picture_url = picture_url
        if user.name is None:
            user.name = name
        if bootstrap_role is not None:
            user.role = bootstrap_role

    await db.commit()
    await db.refresh(user)

    if user.suspended_at is not None:
        raise HTTPException(status_code=403, detail="this account has been suspended")

    user_agent, ip = _request_meta(request)
    sid = await create_session(redis_client, user.id, user_agent=user_agent, ip=ip)

    response = RedirectResponse(f"{settings.frontend_origin}/dashboard")
    set_session_cookie(response, sid)
    return response


@router.post("/register", response_model=MeOut, status_code=201)
async def register(
    body: RegisterRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db),
) -> MeOut:
    email = body.email.strip().lower()
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="invalid email address")

    username = normalize_username(body.username)
    if not is_valid_username(username):
        raise HTTPException(status_code=400, detail=USERNAME_HINT)

    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )

    name = body.name.strip() or None

    existing_email = await db.execute(select(User).where(User.email == email))
    if existing_email.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409, detail="an account with this email already exists — sign in instead"
        )

    existing_username = await db.execute(select(User).where(User.username == username))
    if existing_username.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="that username is already taken")

    user = User(
        email=email,
        username=username,
        name=name,
        password_hash=hash_password(body.password),
        role=UserRole.USER,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="email or username already taken") from exc
    await db.refresh(user)

    user_agent, ip = _request_meta(request)
    sid = await create_session(redis_client, user.id, user_agent=user_agent, ip=ip)
    set_session_cookie(response, sid)

    return await build_me_out(user, db)


@router.post("/login-password", response_model=MeOut)
async def login_password(
    body: PasswordLoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db),
) -> MeOut:
    email = body.email.strip().lower()

    if await too_many_failed_logins(redis_client, email):
        raise HTTPException(status_code=429, detail="too many failed login attempts — try again later")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or user.password_hash is None or not verify_password(body.password, user.password_hash):
        await record_failed_login(redis_client, email)
        raise HTTPException(status_code=401, detail="invalid email or password")

    if user.suspended_at is not None:
        raise HTTPException(status_code=403, detail="this account has been suspended")

    await reset_failed_logins(redis_client, email)

    user_agent, ip = _request_meta(request)
    sid = await create_session(redis_client, user.id, user_agent=user_agent, ip=ip)
    set_session_cookie(response, sid)

    return await build_me_out(user, db)


@router.get("/username-available", response_model=UsernameAvailableOut)
async def username_available(username: str, db: AsyncSession = Depends(get_db)) -> UsernameAvailableOut:
    normalized = normalize_username(username)
    if not is_valid_username(normalized):
        return UsernameAvailableOut(available=False)

    result = await db.execute(select(User).where(User.username == normalized))
    return UsernameAvailableOut(available=result.scalar_one_or_none() is None)


@router.post("/claim-username", response_model=MeOut)
async def claim_username(
    body: ClaimUsernameRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> MeOut:
    if user.username is not None:
        raise HTTPException(status_code=409, detail="username already set")

    username = normalize_username(body.username)
    if not is_valid_username(username):
        raise HTTPException(status_code=400, detail=USERNAME_HINT)

    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="that username is already taken")

    user.username = username
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="that username is already taken") from exc
    await db.refresh(user)
    return await build_me_out(user, db)


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        user_id = await redis_client.hget(f"sess:{sid}", "user_id")
        if user_id:
            await destroy_session(redis_client, sid, uuid.UUID(user_id))
        else:
            await redis_client.delete(f"sess:{sid}")

    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
