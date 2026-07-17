# Published API Lifecycle Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let API owners re-record, edit, sync, and delete their APIs *after* publishing (not only as drafts), and give super admins a read-only workflow view alongside their existing delete.

**Architecture:** A published API is a frozen `custom_apis.workflow_snapshot`; the `Workflow` row lives on behind it. Owners edit the *workflow* (re-record replaces steps; params/extraction stay editable), and an explicit **Sync** copies workflow → snapshot in place (preserving slug/keys/stats/grants) and re-triggers spec generation. Delete removes the whole workflow, whose `ON DELETE CASCADE` FK takes the API and its executions/grants/invites. Super admins act only through the existing (audited) admin panel, plus a new read-only workflow-detail endpoint.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (asyncpg), Pydantic v2, Redis Streams, React + Vite + Tailwind v4, pytest (`uv run pytest`).

**Design doc:** [docs/superpowers/specs/2026-07-18-published-api-lifecycle-management-design.md](../specs/2026-07-18-published-api-lifecycle-management-design.md)

## Global Constraints

- Async everywhere: SQLAlchemy async + asyncpg, `redis.asyncio`. Never move Playwright into Docker or FastAPI — it stays in the worker.
- Enums `native_enum=False` storing `.value`; JSONB columns replaced, never mutated in place; timestamps UTC tz-aware; money `Numeric(10,2)` BDT.
- Owner-only guards reuse `apis._get_owned_api` / `workflows._get_owned_workflow` (both 404 for non-owners incl. shared-access users). Super-admin endpoints use `core.deps.require_super_admin`.
- Lint clean: `cd backend; uv run ruff check app`. Tests: `cd backend; uv run pytest`.
- **No new DB migration** — every column already exists; new response fields are derived at read time.
- Frontend reuses `components/ui` primitives and the Warm Editorial system ([docs/DESIGN.md](../../DESIGN.md)) — no new colors, fonts, or spacing scales.
- Commit per task with a `feat:`/`refactor:`/`test:` message.

---

## File Structure

**Backend**
- `backend/app/services/publish.py` — MODIFY: extract `build_snapshot`, add `sync_workflow_to_api`.
- `backend/app/api/apis.py` — MODIFY: add `POST /apis/{id}/sync`, owner `DELETE /apis/{id}`.
- `backend/app/api/workflows.py` — MODIFY: add `POST /workflows/{id}/rerecord`, serialize published fields.
- `backend/app/schemas/workflow.py` — MODIFY: `WorkflowOut` gains `published_api_id`, `published_api_slug`.
- `backend/app/recorder/session.py` — MODIFY: re-record cancel/timeout safety.
- `backend/app/workers/handlers.py` — MODIFY: pass `rerecord` flag into `RecordingSession`.
- `backend/app/api/admin.py` — MODIFY: add `GET /api/admin/workflows/{id}`.
- `backend/app/schemas/admin.py` — MODIFY: add `AdminWorkflowDetailOut`.
- `backend/tests/test_api_lifecycle.py` — CREATE: sync + owner-delete + rerecord + serialize tests.
- `backend/tests/test_recorder_rerecord.py` — CREATE: session cancel/timeout safety.
- `backend/tests/test_moderation.py` — MODIFY: add admin workflow-detail tests.

**Frontend**
- `frontend/src/lib/types.ts` — MODIFY: `Workflow`/`WorkflowDetail` published fields.
- `frontend/src/pages/ApiDetail.tsx` — MODIFY: Manage group (Edit recording, Delete API).
- `frontend/src/pages/WorkflowEditor.tsx` — MODIFY: published-aware (banner, Sync, Re-record).
- `frontend/src/pages/AdminApis.tsx` (or the admin APIs tab) + a small `WorkflowViewer` — MODIFY/CREATE: read-only viewer.

---

## Phase P1 — Backend delete + sync

### Task 1: Refactor snapshot building + add `sync_workflow_to_api`

**Files:**
- Modify: `backend/app/services/publish.py`
- Test: `backend/tests/test_api_lifecycle.py`

**Interfaces:**
- Produces: `build_snapshot(workflow: Workflow) -> dict`; `async sync_workflow_to_api(api: CustomApi, workflow: Workflow, db: AsyncSession) -> None`.
- Consumes: existing `CustomApi`, `Workflow`, `SpecStatus`, `redis_client`.

- [ ] **Step 1: Write the failing test**

Add to a new file `backend/tests/test_api_lifecycle.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -v`
Expected: FAIL — `AttributeError: module 'app.services.publish' has no attribute 'build_snapshot'`.

- [ ] **Step 3: Refactor `publish.py`**

Replace the body of `publish_workflow` so the snapshot dict comes from a shared helper, and add `sync_workflow_to_api`:

```python
def build_snapshot(workflow: Workflow) -> dict:
    return {
        "steps": workflow.steps,
        "parameters": workflow.parameters,
        "extraction": workflow.extraction,
        "output_schema": workflow.output_schema,
        "browser_settings": workflow.browser_settings,
    }


async def sync_workflow_to_api(api: CustomApi, workflow: Workflow, db: AsyncSession) -> None:
    api.workflow_snapshot = build_snapshot(workflow)
    api.spec_status = SpecStatus.PENDING
    await db.commit()
    await db.refresh(api)
    await redis_client.xadd("jobs:llm", {"payload": json.dumps({"api_id": str(api.id)})})
```

And in `publish_workflow`, replace the inline `workflow_snapshot = {...}` literal with `workflow_snapshot = build_snapshot(workflow)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Guard against regressions + lint**

Run: `cd backend; uv run pytest tests/ -k "publish or replay" -v; uv run ruff check app`
Expected: PASS, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/publish.py backend/tests/test_api_lifecycle.py
git commit -m "refactor: extract build_snapshot and add sync_workflow_to_api"
```

---

### Task 2: `POST /apis/{api_id}/sync` endpoint

**Files:**
- Modify: `backend/app/api/apis.py`
- Test: `backend/tests/test_api_lifecycle.py`

**Interfaces:**
- Consumes: `publish.sync_workflow_to_api`, `_get_owned_api`, `Workflow`, `WorkflowStatus`.
- Produces: `async sync_api(api_id, user, db) -> CustomApi` at `POST /apis/{api_id}/sync`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api_lifecycle.py`:

```python
async def test_sync_endpoint_owner_updates_live_snapshot(db, make_user, redis, monkeypatch):
    monkeypatch.setattr(publish_module, "redis_client", redis)
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    api = await _make_api(db, owner, workflow, snapshot={"steps": [], "parameters": [], "extraction": {}})

    result = await apis_api.sync_api(api_id=api.id, user=owner, db=db)

    assert result.workflow_snapshot["parameters"] == workflow.parameters
    assert result.spec_status == SpecStatus.PENDING


async def test_sync_endpoint_requires_ready_workflow(db, make_user, redis, monkeypatch):
    monkeypatch.setattr(publish_module, "redis_client", redis)
    owner = await make_user()
    workflow = await _make_workflow(db, owner, status=WorkflowStatus.DRAFT)
    api = await _make_api(db, owner, workflow)

    with pytest.raises(HTTPException) as exc_info:
        await apis_api.sync_api(api_id=api.id, user=owner, db=db)
    assert exc_info.value.status_code == 400


async def test_sync_endpoint_non_owner_gets_404(db, make_user, redis, monkeypatch):
    monkeypatch.setattr(publish_module, "redis_client", redis)
    owner = await make_user()
    other = await make_user()
    workflow = await _make_workflow(db, owner)
    api = await _make_api(db, owner, workflow)

    with pytest.raises(HTTPException) as exc_info:
        await apis_api.sync_api(api_id=api.id, user=other, db=db)
    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -k sync_endpoint -v`
Expected: FAIL — `AttributeError: module 'app.api.apis' has no attribute 'sync_api'`.

- [ ] **Step 3: Implement the endpoint**

Add to `backend/app/api/apis.py` (import `WorkflowStatus` from `app.models.workflow`, `Workflow` too, and `from app.services.publish import sync_workflow_to_api` — place near the existing imports):

```python
@router.post("/{api_id}/sync", response_model=CustomApiOut)
async def sync_api(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    api = await _get_owned_api(api_id, user, db)
    workflow = await db.get(Workflow, api.workflow_id)
    if workflow is None or workflow.status != WorkflowStatus.READY:
        raise HTTPException(
            status_code=400,
            detail="the recording must be ready (needs extraction) before syncing to the live API",
        )
    await sync_workflow_to_api(api, workflow, db)
    return api
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -k sync -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd backend; uv run ruff check app
git add backend/app/api/apis.py backend/tests/test_api_lifecycle.py
git commit -m "feat: add owner sync-to-live endpoint for published APIs"
```

---

### Task 3: Owner `DELETE /apis/{api_id}` (total delete)

**Files:**
- Modify: `backend/app/api/apis.py`
- Test: `backend/tests/test_api_lifecycle.py`

**Interfaces:**
- Consumes: `_get_owned_api`, `Workflow`, `sqlalchemy.delete`.
- Produces: `async delete_api(api_id, user, db) -> None` at `DELETE /apis/{api_id}` (204).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api_lifecycle.py`:

```python
from app.models.execution import ApiExecution, ExecutionStatus


async def test_delete_api_owner_removes_api_and_workflow_and_executions(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    api = await _make_api(db, owner, workflow)
    execution = ApiExecution(api_id=api.id, status=ExecutionStatus.SUCCEEDED)
    db.add(execution)
    await db.commit()
    await db.refresh(execution)
    api_id, wf_id, exec_id = api.id, workflow.id, execution.id

    await apis_api.delete_api(api_id=api_id, user=owner, db=db)
    db.expunge_all()

    assert await db.get(CustomApi, api_id) is None
    assert await db.get(Workflow, wf_id) is None
    assert await db.get(ApiExecution, exec_id) is None


async def test_delete_api_non_owner_gets_404(db, make_user):
    owner = await make_user()
    other = await make_user()
    workflow = await _make_workflow(db, owner)
    api = await _make_api(db, owner, workflow)

    with pytest.raises(HTTPException) as exc_info:
        await apis_api.delete_api(api_id=api.id, user=other, db=db)
    assert exc_info.value.status_code == 404
    assert await db.get(CustomApi, api.id) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -k delete_api -v`
Expected: FAIL — `AttributeError: module 'app.api.apis' has no attribute 'delete_api'`.

- [ ] **Step 3: Implement the endpoint**

Add to `backend/app/api/apis.py` (ensure `from sqlalchemy import delete` is imported — the module currently imports `case, func, select`; add `delete`):

```python
@router.delete("/{api_id}", status_code=204)
async def delete_api(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    api = await _get_owned_api(api_id, user, db)
    # Deleting the workflow cascades to this API (custom_apis.workflow_id is
    # ON DELETE CASCADE) and, transitively, its executions/grants/invites —
    # the same mechanism as admin.delete_admin_workflow. "Delete" means gone.
    workflow_id = api.workflow_id
    if workflow_id is not None:
        await db.execute(delete(Workflow).where(Workflow.id == workflow_id))
    else:
        await db.execute(delete(CustomApi).where(CustomApi.id == api_id))
    await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -k delete_api -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd backend; uv run ruff check app
git add backend/app/api/apis.py backend/tests/test_api_lifecycle.py
git commit -m "feat: owner delete of a published API removes it and its workflow"
```

---

## Phase P2 — Re-record

### Task 4: `WorkflowOut` published fields + serialization

**Files:**
- Modify: `backend/app/schemas/workflow.py`, `backend/app/api/workflows.py`
- Test: `backend/tests/test_api_lifecycle.py`

**Interfaces:**
- Produces: `WorkflowOut.published_api_id: uuid.UUID | None`, `WorkflowOut.published_api_slug: str | None`; helper `async _serialize_workflow(workflow, db) -> WorkflowOut` in `workflows.py`, used by `get_workflow` and `update_workflow`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api_lifecycle.py`:

```python
async def test_get_workflow_reports_published_api(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    api = await _make_api(db, owner, workflow)

    out = await workflows_api.get_workflow(workflow_id=workflow.id, user=owner, db=db)
    assert out.published_api_id == api.id
    assert out.published_api_slug == api.slug


async def test_get_workflow_unpublished_has_null_published_fields(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)

    out = await workflows_api.get_workflow(workflow_id=workflow.id, user=owner, db=db)
    assert out.published_api_id is None
    assert out.published_api_slug is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -k published_api -v`
Expected: FAIL — `AttributeError: 'WorkflowOut' object has no attribute 'published_api_id'`.

- [ ] **Step 3: Add schema fields**

In `backend/app/schemas/workflow.py`, add to `WorkflowOut` (after `updated_at`):

```python
    published_api_id: uuid.UUID | None = None
    published_api_slug: str | None = None
```

- [ ] **Step 4: Add serialization helper and use it in the routes**

In `backend/app/api/workflows.py`, add a helper and route it through `get_workflow` and `update_workflow`:

```python
async def _serialize_workflow(workflow: Workflow, db: AsyncSession) -> WorkflowOut:
    row = (
        await db.execute(
            select(CustomApi.id, CustomApi.slug).where(CustomApi.workflow_id == workflow.id)
        )
    ).first()
    base = WorkflowOut.model_validate(workflow)
    return base.model_copy(
        update={
            "published_api_id": row.id if row else None,
            "published_api_slug": row.slug if row else None,
        }
    )
```

Change `get_workflow` to `return await _serialize_workflow(await _get_owned_workflow(workflow_id, user, db), db)`, and change the end of `update_workflow` from `return workflow` to `return await _serialize_workflow(workflow, db)`. Both routes keep `response_model=WorkflowOut`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -k published_api -v`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
cd backend; uv run ruff check app
git add backend/app/schemas/workflow.py backend/app/api/workflows.py backend/tests/test_api_lifecycle.py
git commit -m "feat: expose published_api_id/slug on WorkflowOut"
```

---

### Task 5: `POST /workflows/{id}/rerecord` endpoint

**Files:**
- Modify: `backend/app/api/workflows.py`
- Test: `backend/tests/test_api_lifecycle.py`

**Interfaces:**
- Consumes: `_get_owned_workflow`, `WorkflowStatus`, `redis_client` (add `from app.redis import redis_client` and `import json` to `workflows.py`).
- Produces: `async rerecord(workflow_id, user, db) -> dict` at `POST /workflows/{id}/rerecord`; enqueues `jobs:rec` with `{"workflow_id", "user_id", "rerecord": True}`. **No quota consumed.**

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api_lifecycle.py`:

```python
import json as _json


async def test_rerecord_flips_status_and_enqueues_job(db, make_user, redis, monkeypatch):
    monkeypatch.setattr(workflows_api, "redis_client", redis)
    owner = await make_user()
    workflow = await _make_workflow(db, owner, status=WorkflowStatus.READY)
    api = await _make_api(db, owner, workflow)

    await workflows_api.rerecord(workflow_id=workflow.id, user=owner, db=db)

    refreshed = await db.get(Workflow, workflow.id)
    assert refreshed.status == WorkflowStatus.RECORDING

    jobs = await redis.xrange("jobs:rec")
    assert len(jobs) == 1
    payload = _json.loads(jobs[0][1]["payload"])
    assert payload["workflow_id"] == str(workflow.id)
    assert payload["user_id"] == str(owner.id)
    assert payload["rerecord"] is True
    assert api  # keep the published API alive during re-record


async def test_rerecord_blocks_when_already_recording(db, make_user, redis, monkeypatch):
    monkeypatch.setattr(workflows_api, "redis_client", redis)
    owner = await make_user()
    workflow = await _make_workflow(db, owner, status=WorkflowStatus.RECORDING)

    with pytest.raises(HTTPException) as exc_info:
        await workflows_api.rerecord(workflow_id=workflow.id, user=owner, db=db)
    assert exc_info.value.status_code == 409


async def test_rerecord_non_owner_gets_404(db, make_user, redis, monkeypatch):
    monkeypatch.setattr(workflows_api, "redis_client", redis)
    owner = await make_user()
    other = await make_user()
    workflow = await _make_workflow(db, owner)

    with pytest.raises(HTTPException) as exc_info:
        await workflows_api.rerecord(workflow_id=workflow.id, user=other, db=db)
    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -k rerecord -v`
Expected: FAIL — `AttributeError: module 'app.api.workflows' has no attribute 'rerecord'`.

- [ ] **Step 3: Implement the endpoint**

Add these imports at the top of `backend/app/api/workflows.py`: `import json` and `from app.redis import redis_client`. Then add:

```python
@router.post("/{workflow_id}/rerecord", status_code=202)
async def rerecord(
    workflow_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    workflow = await _get_owned_workflow(workflow_id, user, db)
    if workflow.status == WorkflowStatus.RECORDING:
        raise HTTPException(status_code=409, detail="this recording is already in progress")
    # Re-recording an existing API is not a new creation — no quota is consumed.
    # The live API keeps serving its snapshot until the owner syncs afterward.
    workflow.status = WorkflowStatus.RECORDING
    await db.commit()
    await redis_client.xadd(
        "jobs:rec",
        {"payload": json.dumps({
            "workflow_id": str(workflow.id),
            "user_id": str(user.id),
            "rerecord": True,
        })},
    )
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_api_lifecycle.py -k rerecord -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd backend; uv run ruff check app
git add backend/app/api/workflows.py backend/tests/test_api_lifecycle.py
git commit -m "feat: re-record an existing workflow without consuming quota"
```

---

### Task 6: Re-record cancel/timeout safety in the recorder session

**Files:**
- Modify: `backend/app/workers/handlers.py`, `backend/app/recorder/session.py`
- Test: `backend/tests/test_recorder_rerecord.py`

**Interfaces:**
- Consumes: `RecordingSession(workflow_id, user_id, rerecord=False)`.
- Produces: on cancel/timeout of a re-record, the workflow keeps its prior non-recording status (`READY`) instead of `ARCHIVED`.

Background: `RecordingSession._finalize` today sets `status = ARCHIVED` when `self._cancelled`, and the watchdog sets `_stop` on timeout (which then finalizes normally, writing status via the save path). For a re-record of a live API, a cancel/timeout must NOT archive or downgrade the workflow. We add a `rerecord` flag and, in the cancel branch, restore `READY` instead of archiving.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_recorder_rerecord.py`:

```python
import uuid

from app.models.user import User
from app.models.workflow import Workflow, WorkflowStatus
from app.recorder.session import RecordingSession


async def _seed(db, make_user):
    owner = await make_user()
    workflow = Workflow(
        user_id=owner.id,
        name="Live scraper",
        start_url="https://example.com",
        status=WorkflowStatus.RECORDING,  # rerecord endpoint already flipped it
        steps=[{"i": 0, "type": "goto", "url": "https://example.com"}],
        parameters=[],
        extraction={"main": {"mode": "single", "fields": []}},
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return owner, workflow


async def test_cancelled_rerecord_restores_ready_not_archived(db, make_user):
    owner, workflow = await _seed(db, make_user)

    session = RecordingSession(str(workflow.id), str(owner.id), rerecord=True)
    session._cancelled = True
    await session._finalize()

    refreshed = await db.get(Workflow, workflow.id)
    assert refreshed.status == WorkflowStatus.READY


async def test_cancelled_fresh_recording_still_archives(db, make_user):
    owner, workflow = await _seed(db, make_user)

    session = RecordingSession(str(workflow.id), str(owner.id), rerecord=False)
    session._cancelled = True
    await session._finalize()

    refreshed = await db.get(Workflow, workflow.id)
    assert refreshed.status == WorkflowStatus.ARCHIVED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_recorder_rerecord.py -v`
Expected: FAIL — `TypeError: RecordingSession.__init__() got an unexpected keyword argument 'rerecord'`.

- [ ] **Step 3: Thread the flag through the worker + session constructor**

In `backend/app/workers/handlers.py`, change `record_session`:

```python
async def record_session(payload: dict) -> None:
    await RecordingSession(
        payload["workflow_id"], payload["user_id"], rerecord=payload.get("rerecord", False)
    ).run()
```

In `backend/app/recorder/session.py`, add the parameter to `__init__` (near the top of the constructor, alongside the other flags):

```python
    def __init__(self, workflow_id: str, user_id: str, rerecord: bool = False):
        self.workflow_id = uuid.UUID(workflow_id)
        self.user_id = uuid.UUID(user_id)
        self.rerecord = rerecord
```

(Keep every other existing assignment in `__init__` unchanged.)

- [ ] **Step 4: Make `_finalize` re-record-aware**

In `backend/app/recorder/session.py`, change the `_cancelled` branch of `_finalize`. Current code:

```python
            if self._cancelled:
                workflow.status = WorkflowStatus.ARCHIVED
```

Replace with:

```python
            if self._cancelled:
                # A cancelled/timed-out re-record must not archive or downgrade
                # a workflow that already backs a live API — leave it READY so
                # the API page and editor stay reachable. A cancelled *fresh*
                # recording is a throwaway draft, so it still archives.
                workflow.status = WorkflowStatus.READY if self.rerecord else WorkflowStatus.ARCHIVED
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_recorder_rerecord.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Regression + lint + commit**

Run: `cd backend; uv run pytest tests/ -k "recorder or session" -v; uv run ruff check app`
Expected: PASS, no lint errors.

```bash
git add backend/app/workers/handlers.py backend/app/recorder/session.py backend/tests/test_recorder_rerecord.py
git commit -m "feat: keep a re-recorded workflow READY when its session is cancelled"
```

---

## Phase P3 — Owner frontend

> No JS unit-test harness exists in this repo; verify these tasks in the browser preview per the design doc. Start the dev server with the Browser pane (`preview_start {name: "dev"}` or the project's launch config), then drive the flow.

### Task 7: ApiDetail "Manage" group — Edit recording + Delete API

**Files:**
- Modify: `frontend/src/pages/ApiDetail.tsx`
- Verify: browser preview

- [ ] **Step 1: Add delete state + handler**

In `ApiDetail.tsx`, add state near the other `useState` calls:

```tsx
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
```

Add a handler alongside the other async functions:

```tsx
  async function handleDeleteApi() {
    setDeleting(true)
    setError(null)
    try {
      await api.delete(`/apis/${apiId}`)
      window.location.assign('/dashboard')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to delete API')
      setDeleting(false)
      setConfirmingDelete(false)
    }
  }
```

- [ ] **Step 2: Render the Manage group (owner-only)**

Immediately below the existing `View docs` / `Regenerate docs` row (the `div` around [ApiDetail.tsx:277](../../frontend/src/pages/ApiDetail.tsx#L277)), add:

```tsx
      {isOwner && (
        <div className="mb-8 flex flex-wrap items-center gap-3">
          <Link to={`/workflows/${customApi.workflow_id}/edit`} className={buttonClasses('default', 'sm')}>
            Edit recording
          </Link>
          {confirmingDelete ? (
            <>
              <span className="text-sm text-ink/70">Delete this API and its recording? This can&apos;t be undone.</span>
              <Button variant="danger-ghost" size="sm" onClick={handleDeleteApi} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Confirm delete'}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirmingDelete(false)} disabled={deleting}>
                Cancel
              </Button>
            </>
          ) : (
            <Button variant="danger-ghost" size="sm" onClick={() => setConfirmingDelete(true)}>
              Delete API
            </Button>
          )}
        </div>
      )}
```

- [ ] **Step 3: Verify in the browser**

Log in as an API owner, open a published API. Confirm: "Edit recording" navigates to `/workflows/<id>/edit`; "Delete API" → confirm → the API is gone and you land on the dashboard (the recording is gone too — not in Drafts). As a non-owner viewing a shared API, the Manage group is absent.

Run (console/network check): `read_console_messages` (no errors), `read_network_requests` (the `DELETE /api/apis/<id>` returns 204).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ApiDetail.tsx
git commit -m "feat(ui): add Edit recording and Delete API controls to ApiDetail"
```

---

### Task 8: WorkflowEditor published-aware — banner, Sync, Re-record

**Files:**
- Modify: `frontend/src/pages/WorkflowEditor.tsx`
- Verify: browser preview

- [ ] **Step 1: Extend the local `WorkflowDetail` type + state**

In `WorkflowEditor.tsx`, add to the `WorkflowDetail` interface:

```tsx
  published_api_id: string | null
  published_api_slug: string | null
```

Add state and derive publish status near the other `useState` calls:

```tsx
  const [syncing, setSyncing] = useState(false)
  const [rerecording, setRerecording] = useState(false)
  const isPublished = !!workflow?.published_api_id
```

- [ ] **Step 2: Add Sync + Re-record handlers**

```tsx
  async function handleSync() {
    if (!workflow?.published_api_id) return
    setSyncing(true)
    setError(null)
    setSaveMessage(null)
    try {
      await api.post(`/apis/${workflow.published_api_id}/sync`)
      setSaveMessage('Live API updated.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to sync')
    } finally {
      setSyncing(false)
    }
  }

  async function handleRerecord() {
    setRerecording(true)
    setError(null)
    try {
      await api.post(`/workflows/${workflowId}/rerecord`)
      navigate(`/recorder/${workflowId}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start re-record')
      setRerecording(false)
    }
  }
```

- [ ] **Step 3: Render the published banner**

Directly under the `<PageHeader ... />` in the return, add:

```tsx
      {isPublished && (
        <div className={`${cardClasses({ variant: 'callout', accent: 'blue' })} mb-6 flex items-center justify-between gap-3`}>
          <span className="text-sm text-ink/80">
            Live as <InlineCode>/v1/run/{workflow.published_api_slug}</InlineCode>. Edits here don&apos;t affect the
            live API until you sync.
          </span>
          <Link to={`/apis/${workflow.published_api_id}`} className={buttonClasses('ghost', 'sm')}>
            Open API &rarr;
          </Link>
        </div>
      )}
```

Add `CapsLabel`/`InlineCode` to the existing import from `'../components/ui'` if not already present (`InlineCode` is needed here).

- [ ] **Step 4: Swap the action buttons based on publish state**

Replace the existing action row (the `<div className="flex items-center gap-3">` block containing "Save changes" / conditional "Publish as API" / delete) so that:
- **Save changes** stays for both states.
- When `isPublished`: show **Re-record** and **Sync changes to live API**; hide the draft delete button (deletion happens on the API page).
- When not published: keep the existing `status === 'ready'` "Publish as API" button and the "Delete workflow" flow unchanged.

```tsx
      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save changes'}
        </Button>

        {isPublished ? (
          <>
            <Button variant="default" onClick={handleRerecord} disabled={rerecording}>
              {rerecording ? 'Starting…' : 'Re-record'}
            </Button>
            <Button variant="ink" onClick={handleSync} disabled={syncing}>
              {syncing ? 'Syncing…' : 'Sync changes to live API'}
            </Button>
          </>
        ) : (
          <>
            {workflow.status === 'ready' && (
              <Button variant="ink" onClick={handlePublish} disabled={publishing}>
                {publishing ? 'Publishing…' : 'Publish as API'}
              </Button>
            )}
            {confirmingDelete ? (
              <div className="ml-auto flex items-center gap-2">
                <span className="text-sm text-ink/70">Delete this workflow? This can&apos;t be undone.</span>
                <Button variant="danger-ghost" onClick={handleDelete} disabled={deleting}>
                  {deleting ? 'Deleting…' : 'Confirm delete'}
                </Button>
                <Button variant="ghost" onClick={() => setConfirmingDelete(false)} disabled={deleting}>
                  Cancel
                </Button>
              </div>
            ) : (
              <Button variant="danger-ghost" className="ml-auto" onClick={() => setConfirmingDelete(true)}>
                Delete workflow
              </Button>
            )}
          </>
        )}
      </div>
```

- [ ] **Step 5: Verify in the browser**

- Open a published API → **Edit recording** → the editor shows the blue "Live as /v1/run/…" banner and the Save / Re-record / Sync buttons (no draft delete).
- Change a parameter description, **Save changes**, then **Sync changes to live API** → success message; reload the API page and confirm `spec_status` shows `pending`/`generating` then `ready`; the live snapshot reflects the change.
- Click **Re-record** → lands on `/recorder/<id>` with a live recording session.
- Open an unpublished draft → the editor behaves exactly as before (Publish + Delete workflow), no banner.

`read_console_messages` shows no errors; `read_network_requests` shows `POST /api/apis/<id>/sync` → 200 and `POST /api/workflows/<id>/rerecord` → 202.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/WorkflowEditor.tsx frontend/src/lib/types.ts
git commit -m "feat(ui): make WorkflowEditor published-aware with Sync and Re-record"
```

---

## Phase P4 — Admin read-only workflow viewer

### Task 9: `GET /api/admin/workflows/{id}` detail endpoint

**Files:**
- Modify: `backend/app/api/admin.py`, `backend/app/schemas/admin.py`
- Test: `backend/tests/test_moderation.py`

**Interfaces:**
- Produces: `AdminWorkflowDetailOut { id, name, status, steps: list, parameters: list, extraction: dict }`; `async get_admin_workflow(workflow_id, admin, db) -> Workflow` at `GET /api/admin/workflows/{id}` (`require_super_admin`).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_moderation.py`:

```python
async def test_admin_get_workflow_detail_returns_steps(db, make_user):
    admin = await _make_super_admin(db)
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    workflow.steps = [{"i": 0, "type": "goto", "url": "https://example.com"}]
    workflow.parameters = [{"name": "q", "type": "string"}]
    workflow.extraction = {"main": {"mode": "single", "fields": []}}
    await db.commit()

    out = await admin_api.get_admin_workflow(workflow_id=workflow.id, admin=admin, db=db)
    assert out.id == workflow.id
    assert out.steps[0]["type"] == "goto"
    assert out.parameters[0]["name"] == "q"
    assert out.extraction["main"]["mode"] == "single"


async def test_admin_get_workflow_detail_missing_404(db):
    admin = await _make_super_admin(db)
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.get_admin_workflow(workflow_id=uuid.uuid4(), admin=admin, db=db)
    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_moderation.py -k admin_get_workflow -v`
Expected: FAIL — `AttributeError: module 'app.api.admin' has no attribute 'get_admin_workflow'`.

- [ ] **Step 3: Add the schema**

In `backend/app/schemas/admin.py`, add (mirroring the existing `from_attributes` schemas in that file):

```python
class AdminWorkflowDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: WorkflowStatus
    steps: list
    parameters: list
    extraction: dict
```

Ensure `uuid`, `BaseModel`, `ConfigDict`, and `WorkflowStatus` are imported in that file (add `from app.models.workflow import WorkflowStatus` if absent).

- [ ] **Step 4: Add the endpoint**

In `backend/app/api/admin.py`, near `delete_admin_workflow`, add (import `AdminWorkflowDetailOut` from `app.schemas.admin`):

```python
@router.get("/workflows/{workflow_id}", response_model=AdminWorkflowDetailOut)
async def get_admin_workflow(
    workflow_id: uuid.UUID,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> Workflow:
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workflow
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_moderation.py -k admin_get_workflow -v`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
cd backend; uv run ruff check app
git add backend/app/api/admin.py backend/app/schemas/admin.py backend/tests/test_moderation.py
git commit -m "feat: admin read-only workflow detail endpoint"
```

---

### Task 10: Admin read-only workflow viewer UI

**Files:**
- Modify: the admin APIs tab page (`frontend/src/pages/AdminApis.tsx`, or the T5 user-detail workflows list — whichever renders the admin workflow/API rows) + `frontend/src/lib/types.ts`
- Verify: browser preview

> Locate the exact file first: `grep -rl "admin/apis\|admin/users/.*/workflows" frontend/src`. Add the viewer to the component that lists a user's workflows (from the T5/T6 work). The steps below are the shape; wire them into that component's existing table.

- [ ] **Step 1: Add the type**

In `frontend/src/lib/types.ts`, add:

```ts
export interface AdminWorkflowDetail {
  id: string
  name: string
  status: string
  steps: Step[]
  parameters: Parameter[]
  extraction: { main?: ExtractionConfig }
}
```

(Reuse the existing `Step`, `Parameter`, `ExtractionConfig` exports.)

- [ ] **Step 2: Add a "View" action that loads the detail**

In the admin component that lists workflows/APIs, add state and a loader:

```tsx
  const [viewing, setViewing] = useState<AdminWorkflowDetail | null>(null)

  async function viewWorkflow(workflowId: string) {
    const detail = await api.get<AdminWorkflowDetail>(`/admin/workflows/${workflowId}`)
    setViewing(detail)
  }
```

Add a **View** button next to the existing Delete on each workflow row: `<Button variant="ghost" size="sm" onClick={() => viewWorkflow(row.id)}>View</Button>`.

- [ ] **Step 3: Render a read-only panel**

Below the table, render when `viewing` is set (reuse `describeStep` from `../lib/steps`, and `Table`/`Th`/`Tr`/`Td` from `../components/ui`):

```tsx
      {viewing && (
        <section className={`${cardClasses({ variant: 'quiet' })} mt-6 space-y-4`}>
          <div className="flex items-center justify-between">
            <h3 className="text-h2">{viewing.name} — steps (read-only)</h3>
            <Button variant="ghost" size="sm" onClick={() => setViewing(null)}>Close</Button>
          </div>
          <TableWrapper>
            <Table>
              <thead><tr><Th className="w-14">#</Th><Th>Step</Th></tr></thead>
              <tbody>
                {viewing.steps.map((s) => (
                  <Tr key={s.i}><Td mono>{s.i}</Td><Td>{describeStep(s)}</Td></Tr>
                ))}
              </tbody>
            </Table>
          </TableWrapper>
          <div>
            <CapsLabel tone="muted" className="mb-1">Parameters</CapsLabel>
            <p className="text-sm text-ink/70">{viewing.parameters.map((p) => p.name).join(', ') || 'None'}</p>
          </div>
          <div>
            <CapsLabel tone="muted" className="mb-1">Extraction</CapsLabel>
            <pre className="overflow-x-auto rounded-dot bg-cream p-3 text-xs">{JSON.stringify(viewing.extraction, null, 2)}</pre>
          </div>
        </section>
      )}
```

- [ ] **Step 4: Verify in the browser**

As a super admin, open the admin APIs/users area, click **View** on a user's workflow → the read-only steps/params/extraction panel renders; **Close** dismisses it. As a regular user, hitting `/api/admin/workflows/<id>` returns 403 (the admin nav isn't even reachable). Delete still works from the same table.

`read_network_requests`: `GET /api/admin/workflows/<id>` → 200.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ frontend/src/lib/types.ts
git commit -m "feat(ui): admin read-only workflow viewer"
```

---

## Self-Review

**Spec coverage:**
- Explicit Sync (decision 2) → Tasks 1, 2, 8. ✅
- Re-record only (decision 1) → Tasks 5, 6, 8 (no per-step editing anywhere). ✅
- Total delete (decision 3) → Task 3 (owner) + existing admin delete. ✅
- Super admin View + Delete via admin panel only (decision 4) → Tasks 9, 10; no owner-style edit/re-record for others. ✅
- Regular users excluded (decision 5) → owner-only guards in Tasks 2/3/5, `require_super_admin` in Task 9; asserted in the non-owner 404 tests. ✅
- Reachability of published workflow → Task 7 (Edit recording link) + Task 4 (published fields) + Task 8 (banner). ✅
- Re-record cancel/timeout must not archive a live workflow → Task 6. ✅
- No DB migration → confirmed; only derived fields and existing columns. ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code; every test step shows real assertions and exact `pytest`/`git` commands. ✅

**Type consistency:** `build_snapshot`/`sync_workflow_to_api` (Task 1) used verbatim in Task 2. `sync_api`, `delete_api`, `rerecord`, `get_workflow`, `get_admin_workflow` names match between endpoint definitions and the tests that call them directly. `published_api_id`/`published_api_slug` names identical across schema (Task 4), backend serialization (Task 4), and frontend (Tasks 7, 8). Redis stream keys `jobs:rec`/`jobs:llm` and payload key `rerecord` consistent across Tasks 5, 6. ✅
