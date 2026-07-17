import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api import apis as apis_api
from app.models.api import ApiAccessGrant, ApiVisibility, CustomApi, GrantSource
from app.models.user import UserRole
from app.models.workflow import Workflow

PARAMS = [
    {"name": "city", "type": "string", "required": True, "example": "Dhaka",
     "description": "City name", "source_step": 2},
    {"name": "page", "type": "integer", "required": False},  # missing optional fields
]


async def _make_api(db, owner, *, visibility=ApiVisibility.PRIVATE, parameters=None):
    workflow = Workflow(user_id=owner.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"test-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": parameters or [], "extraction": {}},
        visibility=visibility,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def test_owner_gets_parameter_list(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, parameters=PARAMS)
    result = await apis_api.get_api_parameters(api_id=api.id, user=owner, db=db)
    assert [p.name for p in result] == ["city", "page"]


async def test_missing_optional_fields_get_defaults(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, parameters=PARAMS)
    page = next(p for p in await apis_api.get_api_parameters(api_id=api.id, user=owner, db=db)
                if p.name == "page")
    assert page.type == "integer"
    assert page.required is False
    assert page.example is None
    assert page.description is None
    assert page.source_step is None


async def test_api_with_no_parameters_returns_empty(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, parameters=[])
    assert await apis_api.get_api_parameters(api_id=api.id, user=owner, db=db) == []


async def test_grantee_can_read_parameters(db, make_user):
    owner = await make_user()
    owner.role = UserRole.SUPER_ADMIN  # super-admin owner always allows sharing
    db.add(owner)
    await db.commit()
    grantee = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED, parameters=PARAMS)
    db.add(ApiAccessGrant(api_id=api.id, user_id=grantee.id, granted_via=GrantSource.INVITE))
    await db.commit()
    result = await apis_api.get_api_parameters(api_id=api.id, user=grantee, db=db)
    assert [p.name for p in result] == ["city", "page"]


async def test_unrelated_user_gets_404(db, make_user):
    owner = await make_user()
    other = await make_user()
    api = await _make_api(db, owner, parameters=PARAMS)
    with pytest.raises(HTTPException) as exc_info:
        await apis_api.get_api_parameters(api_id=api.id, user=other, db=db)
    assert exc_info.value.status_code == 404


async def test_missing_api_returns_404(db, make_user):
    owner = await make_user()
    with pytest.raises(HTTPException) as exc_info:
        await apis_api.get_api_parameters(api_id=uuid.uuid4(), user=owner, db=db)
    assert exc_info.value.status_code == 404


async def test_malformed_parameter_entry_is_skipped(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, parameters=[
        {"name": "good", "type": "string"},
        {"type": "string"},  # missing required "name" — must be skipped, not 500
    ])
    result = await apis_api.get_api_parameters(api_id=api.id, user=owner, db=db)
    assert [p.name for p in result] == ["good"]
