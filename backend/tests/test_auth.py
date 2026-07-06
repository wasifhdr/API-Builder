import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import select

from app.api import auth as auth_api
from app.models.user import User, UserRole
from app.schemas.user import ClaimUsernameRequest, PasswordLoginRequest, RegisterRequest
from app.services import accounts as accounts_module


def _patch_redis(monkeypatch, redis) -> None:
    # Both app.api.auth and app.services.accounts hold their own
    # `from app.redis import redis_client` binding — patching one doesn't
    # affect the other, so both must be redirected to the test instance.
    monkeypatch.setattr(auth_api, "redis_client", redis)
    monkeypatch.setattr(accounts_module, "redis_client", redis)


class _FakeRequest:
    def __init__(self, client_host: str | None = "127.0.0.1"):
        self.headers = {"user-agent": "pytest"}
        self.cookies: dict[str, str] = {}
        self.client = SimpleNamespace(host=client_host) if client_host else None


def _req() -> _FakeRequest:
    return _FakeRequest()


def _resp() -> Response:
    return Response()


def _register_body(**overrides) -> RegisterRequest:
    defaults = dict(
        name="Test User",
        email=f"{uuid.uuid4()}@example.com",
        username=f"user_{uuid.uuid4().hex[:12]}",
        password="password123",
    )
    defaults.update(overrides)
    return RegisterRequest(**defaults)


# --- register / login-password round trip ---


async def test_register_then_login_round_trip(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)

    email = f"{uuid.uuid4()}@example.com"
    body = _register_body(email=email, username="ada_lovelace", password="s3cret-pass")
    registered = await auth_api.register(body=body, request=_req(), response=_resp(), db=db)

    assert registered.email == email
    assert registered.username == "ada_lovelace"
    assert registered.has_password is True
    assert registered.has_google is False
    assert registered.role == UserRole.USER

    login_body = PasswordLoginRequest(email=email, password="s3cret-pass")
    logged_in = await auth_api.login_password(body=login_body, request=_req(), response=_resp(), db=db)
    assert logged_in.email == email
    assert logged_in.id == registered.id


async def test_register_bootstraps_super_admin_email(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    email = "admin-bootstrap@example.com"
    monkeypatch.setattr(auth_api.settings, "admin_emails", email)

    body = _register_body(email=email, username="notsuper")
    registered = await auth_api.register(body=body, request=_req(), response=_resp(), db=db)

    # Locked decision: the admin_emails bootstrap only applies to Google
    # login, never to password registration.
    assert registered.role == UserRole.USER


async def test_register_duplicate_email_rejected(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    email = f"{uuid.uuid4()}@example.com"
    await auth_api.register(body=_register_body(email=email), request=_req(), response=_resp(), db=db)

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.register(
            body=_register_body(email=email), request=_req(), response=_resp(), db=db
        )
    assert exc_info.value.status_code == 409


async def test_register_duplicate_username_rejected_case_insensitive(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    await auth_api.register(
        body=_register_body(username="TakenName"), request=_req(), response=_resp(), db=db
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.register(
            body=_register_body(username="takenname"), request=_req(), response=_resp(), db=db
        )
    assert exc_info.value.status_code == 409


async def test_register_rejects_short_password(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    with pytest.raises(HTTPException) as exc_info:
        await auth_api.register(
            body=_register_body(password="short"), request=_req(), response=_resp(), db=db
        )
    assert exc_info.value.status_code == 400


async def test_register_rejects_invalid_username(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    with pytest.raises(HTTPException) as exc_info:
        await auth_api.register(
            body=_register_body(username="a"), request=_req(), response=_resp(), db=db
        )
    assert exc_info.value.status_code == 400


# --- login-password: constant-shape errors, rate limit, suspension ---


async def test_login_unknown_email_rejected(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    with pytest.raises(HTTPException) as exc_info:
        await auth_api.login_password(
            body=PasswordLoginRequest(email="nobody@example.com", password="whatever1"),
            request=_req(), response=_resp(), db=db,
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid email or password"


async def test_login_google_only_account_rejected_same_message(db, redis, monkeypatch, make_user):
    _patch_redis(monkeypatch, redis)
    user = await make_user(email="google-only@example.com")
    assert user.password_hash is None

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.login_password(
            body=PasswordLoginRequest(email=user.email, password="whatever1"),
            request=_req(), response=_resp(), db=db,
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid email or password"


async def test_login_wrong_password_rejected_same_message(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    email = f"{uuid.uuid4()}@example.com"
    await auth_api.register(
        body=_register_body(email=email, password="correct-pass"), request=_req(), response=_resp(), db=db
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.login_password(
            body=PasswordLoginRequest(email=email, password="wrong-pass"),
            request=_req(), response=_resp(), db=db,
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid email or password"


async def test_login_rate_limited_after_ten_failures(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    email = f"{uuid.uuid4()}@example.com"
    await auth_api.register(
        body=_register_body(email=email, password="correct-pass"), request=_req(), response=_resp(), db=db
    )

    for _ in range(10):
        with pytest.raises(HTTPException) as exc_info:
            await auth_api.login_password(
                body=PasswordLoginRequest(email=email, password="wrong-pass"),
                request=_req(), response=_resp(), db=db,
            )
        assert exc_info.value.status_code == 401

    # 11th attempt is rate-limited even with the correct password.
    with pytest.raises(HTTPException) as exc_info:
        await auth_api.login_password(
            body=PasswordLoginRequest(email=email, password="correct-pass"),
            request=_req(), response=_resp(), db=db,
        )
    assert exc_info.value.status_code == 429


async def test_login_suspended_account_rejected(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    email = f"{uuid.uuid4()}@example.com"
    registered = await auth_api.register(
        body=_register_body(email=email, password="correct-pass"), request=_req(), response=_resp(), db=db
    )

    user = await db.get(User, registered.id)
    user.suspended_at = datetime.now(timezone.utc)
    await db.commit()

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.login_password(
            body=PasswordLoginRequest(email=email, password="correct-pass"),
            request=_req(), response=_resp(), db=db,
        )
    assert exc_info.value.status_code == 403


# --- username-available / claim-username ---


async def test_username_available_reflects_taken_state(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    out = await auth_api.username_available(username="freshname", db=db)
    assert out.available is True

    await auth_api.register(
        body=_register_body(username="freshname"), request=_req(), response=_resp(), db=db
    )
    out = await auth_api.username_available(username="FreshName", db=db)
    assert out.available is False


async def test_username_available_rejects_invalid_format(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    out = await auth_api.username_available(username="a", db=db)
    assert out.available is False


async def test_claim_username_sets_once_then_immutable(db, make_user):
    user = await make_user()
    assert user.username is None

    result = await auth_api.claim_username(
        body=ClaimUsernameRequest(username="claimedname"), user=user, db=db
    )
    assert result.username == "claimedname"

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.claim_username(
            body=ClaimUsernameRequest(username="othername"), user=user, db=db
        )
    assert exc_info.value.status_code == 409

    await db.refresh(user)
    assert user.username == "claimedname"


async def test_claim_username_rejects_taken_name(db, make_user):
    owner = await make_user()
    owner.username = "already_taken"
    await db.commit()

    other = await make_user()
    with pytest.raises(HTTPException) as exc_info:
        await auth_api.claim_username(
            body=ClaimUsernameRequest(username="already_taken"), user=other, db=db
        )
    assert exc_info.value.status_code == 409


# --- Google callback: link-by-email, bootstrap, nullable google_sub ---


class _FakeTokenResponse:
    status_code = 200

    def json(self):
        return {"id_token": "fake-token"}


class _FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, data=None):
        return _FakeTokenResponse()


def _patch_google(monkeypatch, redis, *, sub: str, email: str, name: str | None = "Google User"):
    _patch_redis(monkeypatch, redis)
    monkeypatch.setattr(auth_api.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        auth_api.google_id_token, "verify_oauth2_token",
        lambda *a, **k: {"sub": sub, "email": email, "name": name, "picture": "http://pic"},
    )


async def test_google_callback_links_existing_password_account_by_email(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    email = f"{uuid.uuid4()}@example.com"
    registered = await auth_api.register(
        body=_register_body(email=email, name="Original Name"), request=_req(), response=_resp(), db=db
    )
    assert registered.has_google is False

    await redis.set("oauth:state:tok-1", "1")
    _patch_google(monkeypatch, redis, sub="google-sub-1", email=email, name="Google Name")

    response = await auth_api.callback_google(code="c", state="tok-1", request=_req(), db=db)
    assert response.status_code in (302, 307)

    user = await db.get(User, registered.id)
    assert user.google_sub == "google-sub-1"
    # Existing name is preserved, not overwritten by the Google profile name.
    assert user.name == "Original Name"


async def test_google_callback_bootstraps_super_admin(db, redis, monkeypatch):
    email = "bootstrap-admin@example.com"
    monkeypatch.setattr(auth_api.settings, "admin_emails", email)
    await redis.set("oauth:state:tok-2", "1")
    _patch_google(monkeypatch, redis, sub="google-sub-2", email=email)

    await auth_api.callback_google(code="c", state="tok-2", request=_req(), db=db)

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    assert user.role == UserRole.SUPER_ADMIN


async def test_google_callback_rejects_suspended_account(db, redis, monkeypatch):
    email = f"{uuid.uuid4()}@example.com"
    user = User(google_sub="google-sub-3", email=email, name="Suspended")
    user.suspended_at = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()

    await redis.set("oauth:state:tok-3", "1")
    _patch_google(monkeypatch, redis, sub="google-sub-3", email=email)

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.callback_google(code="c", state="tok-3", request=_req(), db=db)
    assert exc_info.value.status_code == 403
