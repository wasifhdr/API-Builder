import uuid

import pytest
from fastapi import HTTPException

from app.api import apis as apis_api
from app.api import workflows as workflows_api
from app.models.api import CustomApi, SpecStatus
from app.models.user import User, UserRole
from app.models.workflow import Workflow, WorkflowStatus
from app.services import publish as publish_module


async def _make_workflow(db, owner, *, status=WorkflowStatus.READY, steps=None):
    workflow = Workflow(
        user_id=owner.id,
        name="Book scraper",
        start_url="https://example.com",
        status=status,
        steps=steps if steps is not None else [{"i": 0, "type": "goto", "url": "https://example.com"}],
        parameters=[{"name": "q", "type": "string", "required": True}],
        extraction={"main": {"mode": "single", "fields": [{"name": "title", "selector": "h1", "take": "text"}]}},
        output_schema={"type": "object"},
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return workflow


async def _make_api(db, owner, workflow, *, snapshot=None):
    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"api-{workflow.id.hex[:8]}",
        name=workflow.name,
        workflow_snapshot=snapshot if snapshot is not None else {"steps": [], "parameters": [], "extraction": {}},
        spec_status=SpecStatus.READY,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def test_build_snapshot_copies_workflow_fields(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)

    snapshot = publish_module.build_snapshot(workflow)

    assert snapshot["steps"] == workflow.steps
    assert snapshot["parameters"] == workflow.parameters
    assert snapshot["extraction"] == workflow.extraction
    assert snapshot["output_schema"] == workflow.output_schema
    assert "browser_settings" in snapshot


async def test_sync_workflow_to_api_updates_snapshot_and_marks_spec_pending(db, make_user, redis, monkeypatch):
    monkeypatch.setattr(publish_module, "redis_client", redis)
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    api = await _make_api(db, owner, workflow, snapshot={"steps": [], "parameters": [], "extraction": {}})

    await publish_module.sync_workflow_to_api(api, workflow, db)

    refreshed = await db.get(CustomApi, api.id)
    assert refreshed.workflow_snapshot["parameters"] == workflow.parameters
    assert refreshed.workflow_snapshot["extraction"] == workflow.extraction
    assert refreshed.spec_status == SpecStatus.PENDING
    jobs = await redis.xrange("jobs:llm")
    assert len(jobs) == 1
