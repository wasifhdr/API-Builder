import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import select

from app.api import auth as auth_api
from app.api import profile as profile_api
from app.models.api import ApiKey, CustomApi, ApiVisibility
from app.models.user import User
from app.models.workflow import Workflow
from app.schemas.user import (
    DeleteAccountRequest,
    PasswordSetRequest,
    ProfileUpdateRequest,
)
from app.services import accounts as accounts_module
from app.services.accounts import delete_user
from app.services.sessions import create_session, user_sessions_key


def _patch_redis(monkeypatch, redis) -> None:
    # app.api.profile, app.services.accounts, and app.api.auth each hold their
    # own `from app.redis import redis_client` binding — patch all three,
    # following the same convention as test_auth.py. auth matters because the
    # re-registration test calls auth_api.register, and the unpatched global
    # client's pool can be bound to a previous test's closed event loop.
    monkeypatch.setattr(profile_api, "redis_client", redis)
    monkeypatch.setattr(accounts_module, "redis_client", redis)
    monkeypatch.setattr(auth_api, "redis_client", redis)


def _resp() -> Response:
    return Response()


async def _make_password_user(db, *, email=None, password="correct-pass", username=None):
    from app.services.passwords import hash_password

    user = User(
        email=email or f"{uuid.uuid4()}@example.com",
        username=username or f"user_{uuid.uuid4().hex[:12]}",
        name="Test User",
        password_hash=hash_password(password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_google_user(db, *, email=None, username=None):
    user = User(
        google_sub=f"sub-{uuid.uuid4()}",
        email=email or f"{uuid.uuid4()}@example.com",
        username=username or f"user_{uuid.uuid4().hex[:12]}",
        name="Google User",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# --- PATCH /me/profile ---


async def test_update_profile_persists_name_and_phone(db, redis, monkeypatch, make_user):
    _patch_redis(monkeypatch, redis)
    user = await make_user()
    result = await profile_api.update_profile(
        body=ProfileUpdateRequest(name="New Name", phone="+8801000000"), user=user, db=db
    )
    assert result.name == "New Name"
    assert result.phone == "+8801000000"

    await db.refresh(user)
    assert user.name == "New Name"
    assert user.phone == "+8801000000"


async def test_update_profile_partial_update_leaves_other_field(db, redis, monkeypatch, make_user):
    _patch_redis(monkeypatch, redis)
    user = await make_user()
    user.phone = "+8801111111"
    await db.commit()

    result = await profile_api.update_profile(
        body=ProfileUpdateRequest(name="Only Name Changed"), user=user, db=db
    )
    assert result.name == "Only Name Changed"
    assert result.phone == "+8801111111"


def test_profile_update_schema_rejects_username():
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(username="new_username")


def test_profile_update_schema_rejects_email():
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(email="new@example.com")


# --- POST /me/password ---


async def test_set_password_without_current_for_google_only_user(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_google_user(db)
    assert user.password_hash is None

    result = await profile_api.set_password(
        body=PasswordSetRequest(new_password="newpassword1"), user=user, db=db, current_sid=None
    )
    assert result.has_password is True

    await db.refresh(user)
    assert user.password_hash is not None


async def test_change_password_requires_and_verifies_current(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_password_user(db, password="original-pass")

    # Missing current_password -> rejected.
    with pytest.raises(HTTPException) as exc_info:
        await profile_api.set_password(
            body=PasswordSetRequest(new_password="brand-new-pass"), user=user, db=db, current_sid=None
        )
    assert exc_info.value.status_code == 400

    # Wrong current_password -> rejected.
    with pytest.raises(HTTPException) as exc_info:
        await profile_api.set_password(
            body=PasswordSetRequest(current_password="wrong", new_password="brand-new-pass"),
            user=user, db=db, current_sid=None,
        )
    assert exc_info.value.status_code == 400

    # Correct current_password -> succeeds.
    result = await profile_api.set_password(
        body=PasswordSetRequest(current_password="original-pass", new_password="brand-new-pass"),
        user=user, db=db, current_sid=None,
    )
    assert result.has_password is True


async def test_change_password_revokes_other_sessions_keeps_current(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_password_user(db, password="original-pass")

    current_sid = await create_session(redis, user.id, user_agent="ua-1", ip="1.1.1.1")
    other_sid = await create_session(redis, user.id, user_agent="ua-2", ip="2.2.2.2")

    await profile_api.set_password(
        body=PasswordSetRequest(current_password="original-pass", new_password="brand-new-pass"),
        user=user, db=db, current_sid=current_sid,
    )

    assert await redis.exists(f"sess:{current_sid}") == 1
    assert await redis.exists(f"sess:{other_sid}") == 0
    remaining = await redis.smembers(user_sessions_key(user.id))
    assert remaining == {current_sid}


async def test_set_password_rejects_short_password(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_google_user(db)
    with pytest.raises(HTTPException) as exc_info:
        await profile_api.set_password(
            body=PasswordSetRequest(new_password="short"), user=user, db=db, current_sid=None
        )
    assert exc_info.value.status_code == 400


# --- GET /me/sessions, POST /me/sessions/revoke-others ---


async def test_list_sessions_marks_current_and_prunes_stale(db, redis, monkeypatch, make_user):
    _patch_redis(monkeypatch, redis)
    user = await make_user()

    current_sid = await create_session(redis, user.id, user_agent="ua-1", ip="1.1.1.1")
    other_sid = await create_session(redis, user.id, user_agent="ua-2", ip="2.2.2.2")
    # Simulate a stale set entry whose session hash already expired.
    stale_sid = "stale-sid-not-a-real-session"
    await redis.sadd(user_sessions_key(user.id), stale_sid)

    sessions = await profile_api.list_sessions(user=user, current_sid=current_sid)
    sids_seen = {s.sid_prefix for s in sessions}
    assert current_sid[:8] in sids_seen
    assert other_sid[:8] in sids_seen
    assert stale_sid[:8] not in sids_seen

    current_entry = next(s for s in sessions if s.sid_prefix == current_sid[:8])
    assert current_entry.current is True
    other_entry = next(s for s in sessions if s.sid_prefix == other_sid[:8])
    assert other_entry.current is False

    # Stale sid should have been pruned from the set.
    remaining = await redis.smembers(user_sessions_key(user.id))
    assert stale_sid not in remaining


async def test_revoke_others_keeps_current_session(db, redis, monkeypatch, make_user):
    _patch_redis(monkeypatch, redis)
    user = await make_user()

    current_sid = await create_session(redis, user.id, user_agent="ua-1", ip="1.1.1.1")
    other_sid = await create_session(redis, user.id, user_agent="ua-2", ip="2.2.2.2")

    result = await profile_api.revoke_other_sessions(user=user, current_sid=current_sid)
    assert result["revoked"] == 1

    assert await redis.exists(f"sess:{current_sid}") == 1
    assert await redis.exists(f"sess:{other_sid}") == 0


# --- delete_user (services/accounts.py) ---


async def test_delete_user_removes_row_and_cascades(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_password_user(db, username="scratchuser")

    workflow = Workflow(user_id=user.id, name="wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=user.id,
        slug=f"scratch-{workflow.id.hex[:8]}",
        name="Scratch API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        visibility=ApiVisibility.PRIVATE,
    )
    db.add(api)

    key = ApiKey(user_id=user.id, label="default", key_prefix="ab_abcdef", key_hash="hash")
    db.add(key)
    await db.commit()

    # Register a live session so we can assert it's gone afterward.
    sid = await create_session(redis, user.id, user_agent="ua", ip="1.1.1.1")

    # Capture ids up front: delete_user commits via a Core DELETE, which
    # doesn't touch the session's identity map, so touching an attribute on
    # these now-deleted ORM instances afterward would trigger a lazy-refresh
    # against a row that no longer exists.
    user_id, workflow_id, api_id, key_id = user.id, workflow.id, api.id, key.id

    await delete_user(db, user)
    db.expunge_all()

    assert await db.get(User, user_id) is None
    assert await db.get(Workflow, workflow_id) is None
    assert await db.get(CustomApi, api_id) is None
    assert await db.get(ApiKey, key_id) is None

    assert await redis.exists(f"sess:{sid}") == 0
    assert await redis.exists(user_sessions_key(user_id)) == 0


async def test_delete_user_frees_username_for_new_registration(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_password_user(db, username="freeme")

    await delete_user(db, user)

    result = await db.execute(select(User).where(User.username == "freeme"))
    assert result.scalar_one_or_none() is None

    from app.schemas.user import RegisterRequest

    new_user = await auth_api.register(
        body=RegisterRequest(
            name="New Owner", email=f"{uuid.uuid4()}@example.com", username="freeme",
            password="password123",
        ),
        request=SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1")),
        response=_resp(),
        db=db,
    )
    assert new_user.username == "freeme"


# --- DELETE /me ---


async def test_delete_account_confirm_username_mismatch_rejected(db, make_user):
    user = await make_user()
    user.username = "realname"
    await db.commit()

    with pytest.raises(HTTPException) as exc_info:
        await profile_api.delete_account(
            body=DeleteAccountRequest(confirm_username="wrongname"),
            response=_resp(), user=user, db=db,
        )
    assert exc_info.value.status_code == 400


async def test_delete_account_requires_current_password_when_set(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_password_user(db, username="pwuser", password="correct-pass")

    with pytest.raises(HTTPException) as exc_info:
        await profile_api.delete_account(
            body=DeleteAccountRequest(confirm_username="pwuser"),
            response=_resp(), user=user, db=db,
        )
    assert exc_info.value.status_code == 400


async def test_delete_account_wrong_password_rejected(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_password_user(db, username="pwuser2", password="correct-pass")

    with pytest.raises(HTTPException) as exc_info:
        await profile_api.delete_account(
            body=DeleteAccountRequest(confirm_username="pwuser2", current_password="wrong"),
            response=_resp(), user=user, db=db,
        )
    assert exc_info.value.status_code == 400


async def test_delete_account_succeeds_and_clears_cookie(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    user = await _make_password_user(db, username="gonesoon", password="correct-pass")

    user_id = user.id
    response = _resp()
    result = await profile_api.delete_account(
        body=DeleteAccountRequest(confirm_username="gonesoon", current_password="correct-pass"),
        response=response, user=user, db=db,
    )
    assert result == {"ok": True}
    db.expunge_all()
    assert await db.get(User, user_id) is None
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "ab_session" in set_cookie_header
