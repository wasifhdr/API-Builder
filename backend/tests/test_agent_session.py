import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.recorder.session as recorder_session
from app.models.workflow import Workflow, WorkflowStatus
from app.recorder.session import RecordingSession


@pytest_asyncio.fixture(autouse=True)
async def _session_uses_test_db(engine, redis, monkeypatch):
    # RecordingSession opens its own DB session via the module-level
    # `async_session` imported from app.db, bound at import time to the dev
    # DB rather than the apibuilder_test DB the db/engine fixtures use — same
    # mismatch test_recorder_rerecord.py documents and works around.
    monkeypatch.setattr(recorder_session, "async_session", async_sessionmaker(engine, expire_on_commit=False))
    # RecordingSession.__init__ also reads the module-level `redis_client`
    # (dev Redis DB 0) into self.redis at construction time. Left unpatched,
    # this test passes in isolation but fails when run alongside the rest of
    # the suite: the dev client's connection pool is tied to whichever event
    # loop last used it, and pytest-asyncio hands every test a fresh loop, so
    # by the time enough other tests have touched app.redis.redis_client the
    # connection belongs to an already-closed loop. Must be set BEFORE
    # RecordingSession(...) is constructed — self.redis is captured at init.
    monkeypatch.setattr(recorder_session, "redis_client", redis)


def test_session_defaults_to_headful_and_no_driver():
    session = RecordingSession("00000000-0000-0000-0000-000000000001",
                                "00000000-0000-0000-0000-000000000002")
    assert session.headless is False
    assert session.agent_driver is None


def test_agent_session_accepts_headless_and_a_driver():
    async def _driver(session):
        pass

    session = RecordingSession("00000000-0000-0000-0000-000000000001",
                                "00000000-0000-0000-0000-000000000002",
                                headless=True, agent_driver=_driver)
    assert session.headless is True
    assert session.agent_driver is _driver


async def _make_workflow(db, user, start_url):
    workflow = Workflow(
        user_id=user.id, name="agent test", start_url=start_url,
        status=WorkflowStatus.RECORDING,
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return workflow


@pytest.mark.asyncio
async def test_agent_driver_runs_headless_and_ends_the_session(db, make_user, fixture_site_url, redis):
    """End-to-end: the agent_driver callback gets a live page, drives it with
    plain Playwright calls (no LLM involved here), and the session captures
    the resulting steps exactly as it would for a human — proving the hook
    integrates with the real browser/heartbeat/finalize lifecycle, not just
    that it's callable."""
    user = await make_user()
    user.settings = {"use_saved_logins": True}  # must be force-disabled for agent runs
    await db.commit()
    workflow = await _make_workflow(db, user, f"{fixture_site_url}/search.html")

    driven = {"called": False, "use_saved_logins": None}

    async def _driver(session):
        driven["called"] = True
        driven["use_saved_logins"] = session.use_saved_logins
        await session.page.fill("#q", "television")
        await session.page.wait_for_timeout(700)  # clear the fill debounce
        await session.page.click("button[type=submit]")
        await session.page.wait_for_timeout(500)

    session = RecordingSession(
        str(workflow.id), str(user.id), headless=True, agent_driver=_driver,
    )
    await session.run()

    assert driven["called"] is True
    assert driven["use_saved_logins"] is False  # forced off despite the user's setting

    step_types = [s["type"] for s in session.steps]
    assert "fill" in step_types
    assert "click" in step_types

    # session.run() commits through its own separate db session, so the test's
    # session's identity-mapped copy of `workflow` is stale until refreshed.
    await db.refresh(workflow)
    assert workflow.status == WorkflowStatus.DRAFT  # no extraction was set
    assert workflow.steps == session.steps
