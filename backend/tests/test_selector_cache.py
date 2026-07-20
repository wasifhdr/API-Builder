import uuid

from app.models.workflow import Workflow
from app.recorder.selector_cache import read_cache, upsert_cache


async def _make_workflow(db, make_user) -> uuid.UUID:
    # extraction_selector_cache.workflow_id FK-references workflows.id, so a real
    # row must exist before it can be used as a cache key (Postgres enforces this
    # even though the brief's illustrative snippet used bare uuid.uuid4()).
    user = await make_user()
    wf = Workflow(user_id=user.id, name="test", start_url="https://example.com")
    db.add(wf)
    await db.commit()
    await db.refresh(wf)
    return wf.id


async def test_upsert_then_read_roundtrips(db, make_user):
    wf = await _make_workflow(db, make_user)
    await upsert_cache(db, wf, "main", "price", [".p1", ".p2"])
    out = await read_cache(db, wf)
    assert out[("main", "price")] == [".p1", ".p2"]


async def test_upsert_overwrites_existing(db, make_user):
    wf = await _make_workflow(db, make_user)
    await upsert_cache(db, wf, "main", "price", [".old"])
    await upsert_cache(db, wf, "main", "price", [".new"])
    out = await read_cache(db, wf)
    assert out[("main", "price")] == [".new"]


async def test_read_empty_for_unknown_workflow(db):
    out = await read_cache(db, uuid.uuid4())
    assert out == {}
