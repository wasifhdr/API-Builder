import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentRunStatus


@pytest.mark.asyncio
async def test_agent_run_defaults(db: AsyncSession, make_user):
    user = await make_user()
    run = AgentRun(user_id=user.id, prompt="search walton for fridges")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    assert run.status == AgentRunStatus.PLANNING
    assert run.attempt == 0
    assert run.plan == {}
    assert run.transcript == []
    assert run.workflow_id is None
    assert run.token_usage == 0


@pytest.mark.asyncio
async def test_agent_run_status_persists_as_value(db: AsyncSession, make_user):
    from sqlalchemy import text

    user = await make_user()
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.VERIFYING)
    db.add(run)
    await db.commit()

    raw = await db.execute(text("SELECT status FROM agent_runs WHERE id = :i"), {"i": run.id})
    assert raw.scalar_one() == "verifying"
