import json
import re
import secrets
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api import CustomApi, SpecStatus
from app.models.workflow import Workflow
from app.redis import redis_client


def _slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "api"


async def _unique_slug(base: str, db: AsyncSession) -> str:
    for _ in range(10):
        candidate = f"{base}-{secrets.token_hex(2)}"
        result = await db.execute(select(CustomApi.id).where(CustomApi.slug == candidate))
        if result.scalar_one_or_none() is None:
            return candidate
    raise RuntimeError("could not generate a unique slug")


async def publish_workflow(workflow: Workflow, db: AsyncSession) -> CustomApi:
    slug = await _unique_slug(_slugify(workflow.name), db)

    workflow_snapshot = {
        "steps": workflow.steps,
        "parameters": workflow.parameters,
        "extraction": workflow.extraction,
        "output_schema": workflow.output_schema,
    }

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=workflow.user_id,
        slug=slug,
        name=workflow.name,
        workflow_snapshot=workflow_snapshot,
        spec_status=SpecStatus.PENDING,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)

    await redis_client.xadd("jobs:llm", {"payload": json.dumps({"api_id": str(api.id)})})

    return api
