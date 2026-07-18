import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.recorder.session as recorder_session
from app.models.workflow import Workflow, WorkflowStatus
from app.recorder.session import RecordingSession


@pytest_asyncio.fixture(autouse=True)
async def _finalize_uses_test_db(engine, monkeypatch):
    # RecordingSession._finalize opens its own DB session via the module-level
    # `async_session` imported from app.db, which is bound at import time to
    # settings.database_url (the dev DB, e.g. "apibuilder") — not the
    # `apibuilder_test` DB the `db`/`engine` fixtures use. Every other test in
    # this suite passes `db` into the code under test explicitly, so this
    # mismatch has never mattered before. Point _finalize's internal session
    # at the same test engine so it sees the workflow this test seeds.
    monkeypatch.setattr(recorder_session, "async_session", async_sessionmaker(engine, expire_on_commit=False))


async def _seed(db, make_user, extraction=None):
    owner = await make_user()
    workflow = Workflow(
        user_id=owner.id,
        name="Live scraper",
        start_url="https://example.com",
        status=WorkflowStatus.RECORDING,  # rerecord endpoint already flipped it
        steps=[{"i": 0, "type": "goto", "url": "https://example.com"}],
        parameters=[],
        extraction=extraction if extraction is not None else {"main": {"mode": "single", "fields": []}},
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return owner, workflow


async def _noop_publish(event: dict) -> None:
    # _finalize publishes a "closed" status event over self.redis, which
    # defaults to the module-level redis_client singleton bound to the dev
    # Redis DB and reused across pytest-asyncio's per-test event loops —
    # exactly the pattern test_recorder_mark_param.py avoids by stubbing
    # _publish rather than opening a real Redis connection. Follow that
    # precedent here too.
    pass


async def test_cancelled_rerecord_restores_ready_not_archived(db, make_user):
    owner, workflow = await _seed(db, make_user)
    workflow_id, owner_id = workflow.id, owner.id  # read before expire_all below

    session = RecordingSession(str(workflow_id), str(owner_id), rerecord=True)
    session._cancelled = True
    session._publish = _noop_publish
    await session._finalize()

    # _finalize commits through a different session/connection object (see
    # the _finalize_uses_test_db fixture above); with expire_on_commit=False
    # on the `db` fixture's sessionmaker, db.get() would otherwise return the
    # identity-mapped object cached from _seed's own commit instead of
    # re-reading the row.
    db.expire_all()
    refreshed = await db.get(Workflow, workflow_id)
    assert refreshed.status == WorkflowStatus.READY


async def test_cancelled_rerecord_with_empty_extraction_goes_draft_not_ready(db, make_user):
    # A second re-record cancelled/timed out on a workflow whose persisted
    # extraction is empty (e.g. an earlier re-record never set it) must not
    # be force-set to READY — that would let the owner Sync an extraction-less
    # snapshot over the LIVE API. Status must derive from the real extraction.
    owner, workflow = await _seed(db, make_user, extraction={})
    workflow_id, owner_id = workflow.id, owner.id  # read before expire_all below

    session = RecordingSession(str(workflow_id), str(owner_id), rerecord=True)
    session._cancelled = True
    session._publish = _noop_publish
    await session._finalize()

    db.expire_all()
    refreshed = await db.get(Workflow, workflow_id)
    assert refreshed.status == WorkflowStatus.DRAFT


async def test_cancelled_fresh_recording_still_archives(db, make_user):
    owner, workflow = await _seed(db, make_user)
    workflow_id, owner_id = workflow.id, owner.id  # read before expire_all below

    session = RecordingSession(str(workflow_id), str(owner_id), rerecord=False)
    session._cancelled = True
    session._publish = _noop_publish
    await session._finalize()

    db.expire_all()
    refreshed = await db.get(Workflow, workflow_id)
    assert refreshed.status == WorkflowStatus.ARCHIVED
