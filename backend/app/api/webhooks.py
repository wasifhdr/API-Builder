from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.services import sms_matcher

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class BkashSmsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    text: str
    received_at: datetime | None = None


@router.post("/bkash-sms", status_code=201)
async def bkash_sms_webhook(
    body: BkashSmsPayload,
    x_webhook_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if x_webhook_token != settings.sms_webhook_token:
        raise HTTPException(status_code=401, detail="invalid webhook token")

    received_at = body.received_at or datetime.now(timezone.utc)
    receipt = await sms_matcher.ingest_sms(body.text, body.from_, received_at, db)
    if receipt is None:
        return {"status": "duplicate"}
    return {"status": "received", "id": str(receipt.id)}
