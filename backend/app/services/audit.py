import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AdminAuditLog
from app.models.user import User


def log_admin_action(
    db: AsyncSession,
    actor: User,
    action: str,
    target_type: str,
    target_id: str | uuid.UUID,
    detail: dict,
) -> None:
    """Stage an audit-log row on `db` without committing.

    Callers add this inside the same transaction as the mutation being
    recorded, then commit once — so an audit row never appears without its
    corresponding change, and a failed mutation never leaves an orphan row.
    """
    db.add(
        AdminAuditLog(
            actor_user_id=actor.id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            detail=detail,
        )
    )
