import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_cache import ExtractionSelectorCache


async def read_cache(db: AsyncSession, workflow_id: uuid.UUID) -> dict[tuple[str, str], list[str]]:
    rows = (
        await db.execute(
            select(ExtractionSelectorCache).where(
                ExtractionSelectorCache.workflow_id == workflow_id
            )
        )
    ).scalars()
    return {(r.ref, r.field_name): r.selectors for r in rows}


async def upsert_cache(
    db: AsyncSession, workflow_id: uuid.UUID, ref: str, field_name: str, selectors: list[str]
) -> None:
    stmt = insert(ExtractionSelectorCache).values(
        workflow_id=workflow_id, ref=ref, field_name=field_name, selectors=selectors
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["workflow_id", "ref", "field_name"],
        set_={"selectors": stmt.excluded.selectors, "healed_at": func.now()},
    )
    await db.execute(stmt)
    await db.commit()
