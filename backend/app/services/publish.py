import json
import re
import secrets
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api import CustomApi, SpecStatus
from app.models.workflow import Workflow
from app.redis import redis_client


# custom_apis.slug is varchar(80) and _unique_slug appends "-" + 4 hex chars.
# Workflow names are varchar(200) — the agent planner routinely writes
# sentence-long ones — so the base has to be clamped or the INSERT overflows
# the column and publishing 500s.
SLUG_MAX_LENGTH = 80
_SLUG_SUFFIX_LENGTH = 5
SLUG_BASE_MAX_LENGTH = SLUG_MAX_LENGTH - _SLUG_SUFFIX_LENGTH


def _slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if len(slug) > SLUG_BASE_MAX_LENGTH:
        slug = slug[:SLUG_BASE_MAX_LENGTH]
        # Prefer cutting at the last word boundary so the slug stays readable,
        # unless that would leave almost nothing.
        boundary = slug.rfind("-")
        if boundary >= SLUG_BASE_MAX_LENGTH // 2:
            slug = slug[:boundary]
        slug = slug.strip("-")
    return slug or "api"


async def _unique_slug(base: str, db: AsyncSession) -> str:
    for _ in range(10):
        candidate = f"{base}-{secrets.token_hex(2)}"
        result = await db.execute(select(CustomApi.id).where(CustomApi.slug == candidate))
        if result.scalar_one_or_none() is None:
            return candidate
    raise RuntimeError("could not generate a unique slug")


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


async def publish_workflow(workflow: Workflow, db: AsyncSession) -> CustomApi:
    slug = await _unique_slug(_slugify(workflow.name), db)

    workflow_snapshot = build_snapshot(workflow)

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
