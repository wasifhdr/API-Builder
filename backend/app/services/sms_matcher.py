import hashlib
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api import ApiAccessGrant, GrantSource
from app.models.billing import (
    BkashSmsReceipt,
    PaymentPurpose,
    PaymentStatus,
    PaymentTransaction,
    VerificationMethod,
)
from app.models.wallet import REASON_RECHARGE
from app.services import subscriptions, wallet

AMOUNT_RE = re.compile(r"Tk\s*([\d,]+(?:\.\d{1,2})?)", re.I)
TRX_RE = re.compile(r"TrxID\s*:?\s*([A-Z0-9]{8,12})", re.I)
MSISDN_RE = re.compile(r"from\s+(01\d{9})", re.I)


def dedupe_hash(raw_text: str, received_at: datetime) -> str:
    # SMS forwarders retry deliveries of the exact same message; bucket by
    # minute so retries within the same minute collapse to one receipt.
    minute_bucket = received_at.strftime("%Y%m%d%H%M")
    return hashlib.sha256(f"{raw_text}|{minute_bucket}".encode()).hexdigest()


def parse_sms(raw_text: str) -> dict:
    amount = None
    m = AMOUNT_RE.search(raw_text)
    if m:
        amount = Decimal(m.group(1).replace(",", ""))

    trx_id = None
    m = TRX_RE.search(raw_text)
    if m:
        trx_id = m.group(1).upper()

    msisdn = None
    m = MSISDN_RE.search(raw_text)
    if m:
        msisdn = m.group(1)

    return {"amount": amount, "trx_id": trx_id, "msisdn": msisdn}


async def apply_verified_effects(transaction: PaymentTransaction, db: AsyncSession) -> None:
    """Activates/extends a subscription or creates an API-access grant for a
    transaction that has just been marked verified. Does not commit — the
    caller (matcher or admin verify endpoint) controls the transaction
    boundary since it also needs to persist the verified status itself."""
    if transaction.purpose == PaymentPurpose.SUBSCRIPTION:
        # Legacy path: no new SUBSCRIPTION intents are created since Phase W2
        # (wallet-funded subscribe), but a pre-W2 pending/submitted row can
        # still land here via submit-trx or a late-arriving SMS.
        await subscriptions.activate(
            transaction.user_id, transaction.plan_tier, db, source_transaction_id=transaction.id
        )
    elif transaction.purpose == PaymentPurpose.API_ACCESS:
        result = await db.execute(
            select(ApiAccessGrant).where(
                ApiAccessGrant.api_id == transaction.api_id,
                ApiAccessGrant.user_id == transaction.user_id,
            )
        )
        grant = result.scalar_one_or_none()
        if grant is None:
            db.add(
                ApiAccessGrant(
                    api_id=transaction.api_id,
                    user_id=transaction.user_id,
                    granted_via=GrantSource.PURCHASE,
                    transaction_id=transaction.id,
                )
            )
        else:
            grant.revoked_at = None
            grant.transaction_id = transaction.id
    elif transaction.purpose == PaymentPurpose.RECHARGE:
        await wallet.credit(
            transaction.user_id, transaction.amount_received_bdt, REASON_RECHARGE, db,
            transaction_id=transaction.id,
        )


async def try_match(transaction_id: uuid.UUID, db: AsyncSession) -> bool:
    """Runs the matcher for one submitted transaction, row-locked so a
    concurrent call (submit-trx vs. webhook SMS arrival) can't double-verify
    it. Returns True if it just got verified."""
    result = await db.execute(
        select(PaymentTransaction).where(PaymentTransaction.id == transaction_id).with_for_update()
    )
    transaction = result.scalar_one_or_none()
    if transaction is None or transaction.status != PaymentStatus.SUBMITTED or transaction.bkash_trx_id is None:
        return False

    receipt_result = await db.execute(
        select(BkashSmsReceipt).where(
            BkashSmsReceipt.matched_transaction_id.is_(None),
            func.upper(BkashSmsReceipt.parsed_trx_id) == transaction.bkash_trx_id.upper(),
        )
    )
    receipt = receipt_result.scalars().first()
    if receipt is None:
        return False

    if receipt.parsed_amount_bdt is None or receipt.parsed_amount_bdt < transaction.amount_expected_bdt:
        transaction.note = (
            f"underpaid: expected {transaction.amount_expected_bdt}, "
            f"received {receipt.parsed_amount_bdt} — admin review needed"
        )
        await db.commit()
        return False

    transaction.status = PaymentStatus.VERIFIED
    transaction.amount_received_bdt = receipt.parsed_amount_bdt
    transaction.verification_method = VerificationMethod.AUTO_SMS
    transaction.matched_sms_id = receipt.id
    transaction.verified_at = datetime.now(timezone.utc)
    receipt.matched_transaction_id = transaction.id

    await apply_verified_effects(transaction, db)
    await db.commit()
    return True


async def try_match_by_sms(receipt_id: uuid.UUID, db: AsyncSession) -> bool:
    """Called right after a new SMS receipt is inserted — finds a submitted
    transaction whose TrxID matches, if any, and hands off to try_match for
    the row-locked verify. Order-independent counterpart of try_match, which
    runs right after submit-trx."""
    receipt_result = await db.execute(select(BkashSmsReceipt).where(BkashSmsReceipt.id == receipt_id))
    receipt = receipt_result.scalar_one_or_none()
    if receipt is None or receipt.parsed_trx_id is None or receipt.matched_transaction_id is not None:
        return False

    tx_result = await db.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.status == PaymentStatus.SUBMITTED,
            func.upper(PaymentTransaction.bkash_trx_id) == receipt.parsed_trx_id.upper(),
        )
    )
    transaction = tx_result.scalar_one_or_none()
    if transaction is None:
        return False
    return await try_match(transaction.id, db)


async def ingest_sms(raw_text: str, sender: str | None, received_at: datetime, db: AsyncSession) -> BkashSmsReceipt | None:
    """Stores the raw receipt (always — even if parsing fails) and attempts a
    match. Returns None if this exact delivery was already recorded, since
    forwarders retry the same message."""
    dedupe = dedupe_hash(raw_text, received_at)
    existing = await db.execute(select(BkashSmsReceipt).where(BkashSmsReceipt.dedupe_hash == dedupe))
    if existing.scalar_one_or_none() is not None:
        return None

    parsed = parse_sms(raw_text)
    receipt = BkashSmsReceipt(
        raw_text=raw_text,
        sms_sender=sender,
        dedupe_hash=dedupe,
        parsed_trx_id=parsed["trx_id"],
        parsed_amount_bdt=parsed["amount"],
        parsed_sender_msisdn=parsed["msisdn"],
    )
    db.add(receipt)
    await db.commit()
    await db.refresh(receipt)

    await try_match_by_sms(receipt.id, db)
    return receipt
