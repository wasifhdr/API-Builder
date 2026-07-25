"""Out-of-session parameter suggestion and marking.

Once a recording ends there's no worker session to answer `suggest_authoring`
or `mark_param` over the WS, so both move to REST + the `jobs:llm` queue. These
cover the HTTP layer and the queue dispatch; the suggestion itself is the same
app.llm.authoring code the recorder already used.
"""

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from fastapi import HTTPException

from app.api import workflows as workflows_api
from app.models.workflow import Workflow, WorkflowStatus
from app.schemas.workflow import MarkParameterIn
from app.services import authoring
from app.workers import handlers


@pytest_asyncio.fixture
async def worker_db(engine, monkeypatch):
    """The worker handler opens its own session from the module-level
    `async_session`, which is bound at import time to the dev DB rather than the
    test one (same reason test_recorder_rerecord needs this). Point it at the
    test engine so the handler sees the workflow these tests seed."""
    monkeypatch.setattr(handlers, "async_session", async_sessionmaker(engine, expire_on_commit=False))

STEPS = [
    {"i": 0, "type": "goto", "url": "https://example.com"},
    {"i": 1, "type": "fill", "selectors": ["#q"], "value": {"literal": "Obsession"}},
    {"i": 2, "type": "press", "selectors": ["#q"], "key": "Enter"},
]


async def _make_workflow(db, owner, *, status=WorkflowStatus.DRAFT, steps=None):
    workflow = Workflow(
        user_id=owner.id,
        name="IMDb search",
        start_url="https://imdb.com",
        status=status,
        steps=STEPS if steps is None else steps,
        parameters=[],
        extraction={},
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return workflow


async def test_mark_parameter_swaps_literal_and_records_the_parameter(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)

    out = await workflows_api.mark_parameter(
        workflow.id,
        MarkParameterIn(step_i=1, name="query", type="string", description="Search term"),
        user=owner,
        db=db,
    )

    assert out.steps[1]["value"] == {"param": "query"}
    assert out.parameters == [{
        "name": "query",
        "type": "string",
        "required": True,
        "example": "Obsession",  # the recorded literal survives as the example
        "description": "Search term",
        "source_step": 1,
    }]


async def test_mark_parameter_reusing_a_name_replaces_the_earlier_one(db, make_user):
    """Matches RecordingSession._handle_mark_param: last marking of a name wins,
    rather than accumulating duplicate parameters the API can't disambiguate."""
    owner = await make_user()
    steps = [*STEPS, {"i": 3, "type": "fill", "selectors": ["#q2"], "value": {"literal": "later"}}]
    workflow = await _make_workflow(db, owner, steps=steps)

    await workflows_api.mark_parameter(
        workflow.id, MarkParameterIn(step_i=1, name="query"), user=owner, db=db)
    out = await workflows_api.mark_parameter(
        workflow.id, MarkParameterIn(step_i=3, name="query"), user=owner, db=db)

    assert len(out.parameters) == 1
    assert out.parameters[0]["source_step"] == 3


async def test_mark_parameter_rejects_a_step_with_no_value(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)

    with pytest.raises(HTTPException) as exc:
        await workflows_api.mark_parameter(
            workflow.id, MarkParameterIn(step_i=0, name="query"), user=owner, db=db)
    assert exc.value.status_code == 400


async def test_mark_parameter_rejects_an_already_parameterized_step(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)

    await workflows_api.mark_parameter(
        workflow.id, MarkParameterIn(step_i=1, name="query"), user=owner, db=db)
    with pytest.raises(HTTPException) as exc:
        await workflows_api.mark_parameter(
            workflow.id, MarkParameterIn(step_i=1, name="other"), user=owner, db=db)
    assert exc.value.status_code == 400


async def test_mark_parameter_defers_to_the_recorder_while_recording(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner, status=WorkflowStatus.RECORDING)

    with pytest.raises(HTTPException) as exc:
        await workflows_api.mark_parameter(
            workflow.id, MarkParameterIn(step_i=1, name="query"), user=owner, db=db)
    assert exc.value.status_code == 409


async def test_mark_parameter_404s_for_another_users_workflow(db, make_user):
    owner = await make_user()
    intruder = await make_user()
    workflow = await _make_workflow(db, owner)

    with pytest.raises(HTTPException) as exc:
        await workflows_api.mark_parameter(
            workflow.id, MarkParameterIn(step_i=1, name="query"), user=intruder, db=db)
    assert exc.value.status_code == 404


async def test_suggestion_request_marks_pending_before_enqueuing(db, make_user, redis, monkeypatch):
    """The pending marker must be written first: a poll landing in the gap
    between enqueue and the worker starting would otherwise read "idle" and the
    page would give up on a job that's about to run."""
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    monkeypatch.setattr(workflows_api, "redis_client", redis)

    out = await workflows_api.request_parameter_suggestions(workflow.id, user=owner, db=db)

    assert out == {"state": "pending"}
    assert json.loads(await redis.get(authoring.suggestions_key(workflow.id))) == {"state": "pending"}
    entries = await redis.xrange("jobs:llm")
    assert len(entries) == 1
    payload = json.loads(entries[0][1]["payload"])
    assert payload == {"kind": authoring.JOB_KIND, "workflow_id": str(workflow.id)}


async def test_suggestion_get_reports_idle_when_nothing_was_requested(db, make_user, redis, monkeypatch):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    monkeypatch.setattr(workflows_api, "redis_client", redis)

    assert await workflows_api.get_parameter_suggestions(workflow.id, user=owner, db=db) == {"state": "idle"}


async def test_suggestion_get_returns_the_workers_result(db, make_user, redis, monkeypatch):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    monkeypatch.setattr(workflows_api, "redis_client", redis)
    ready = {"state": "ready", "parameters": [{"step_i": 1, "name": "query"}]}
    await redis.set(authoring.suggestions_key(workflow.id), json.dumps(ready))

    assert await workflows_api.get_parameter_suggestions(workflow.id, user=owner, db=db) == ready


async def test_suggestion_request_rejected_while_the_recorder_is_live(db, make_user):
    owner = await make_user()
    workflow = await _make_workflow(db, owner, status=WorkflowStatus.RECORDING)

    with pytest.raises(HTTPException) as exc:
        await workflows_api.request_parameter_suggestions(workflow.id, user=owner, db=db)
    assert exc.value.status_code == 409


async def test_worker_writes_suggestions_to_redis(db, make_user, redis, monkeypatch, worker_db):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    monkeypatch.setattr(handlers, "redis_client", redis)

    async def fake_suggest(steps):
        assert steps == STEPS  # reads the persisted steps, not live session state
        return [{"step_i": 1, "name": "query", "type": "string"}]

    monkeypatch.setattr(handlers, "suggest_parameters", fake_suggest)

    await handlers.suggest_workflow_parameters({"workflow_id": str(workflow.id)})

    stored = json.loads(await redis.get(authoring.suggestions_key(workflow.id)))
    assert stored == {"state": "ready", "parameters": [{"step_i": 1, "name": "query", "type": "string"}]}


async def test_worker_records_a_failed_suggestion_instead_of_hanging_the_poller(
    db, make_user, redis, monkeypatch, worker_db,
):
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    monkeypatch.setattr(handlers, "redis_client", redis)

    async def boom(steps):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(handlers, "suggest_parameters", boom)

    await handlers.suggest_workflow_parameters({"workflow_id": str(workflow.id)})

    stored = json.loads(await redis.get(authoring.suggestions_key(workflow.id)))
    assert stored["state"] == "error"
    assert "model unavailable" in stored["message"]


async def test_worker_reports_a_missing_workflow(redis, monkeypatch, worker_db):
    monkeypatch.setattr(handlers, "redis_client", redis)
    missing = uuid.uuid4()

    await handlers.suggest_workflow_parameters({"workflow_id": str(missing)})

    stored = json.loads(await redis.get(authoring.suggestions_key(missing)))
    assert stored == {"state": "error", "message": "workflow not found"}


async def test_llm_job_routes_by_kind(monkeypatch):
    """Spec generation predates the discriminator, so a payload without `kind`
    must still reach generate_spec — old queued messages included."""
    seen = []

    async def fake_spec(payload):
        seen.append(("spec", payload))

    async def fake_suggest(payload):
        seen.append(("suggest", payload))

    monkeypatch.setattr(handlers, "generate_spec", fake_spec)
    monkeypatch.setattr(handlers, "suggest_workflow_parameters", fake_suggest)

    await handlers.llm_job({"api_id": "abc"})
    await handlers.llm_job({"kind": authoring.JOB_KIND, "workflow_id": "def"})

    assert seen == [("spec", {"api_id": "abc"}), ("suggest", {"kind": authoring.JOB_KIND, "workflow_id": "def"})]
