"""add cashout requests

Revision ID: a2861ad6bed7
Revises: 16f6d20ccf4a
Create Date: 2026-07-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2861ad6bed7'
down_revision: Union[str, Sequence[str], None] = '16f6d20ccf4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('cashout_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('amount_bdt', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('payout_msisdn', sa.String(length=20), nullable=False),
    sa.Column(
        'status',
        sa.Enum('requested', 'paid', 'rejected', name='cashoutstatus', native_enum=False, length=32),
        nullable=False,
    ),
    sa.Column('bkash_trx_id', sa.String(length=40), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('decided_by_user_id', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_cashout_requests_user_id'), 'cashout_requests', ['user_id'], unique=False)

    # cashout_requests must exist before this FK can target it — hence a
    # separate ALTER on the already-existing wallet_ledger table.
    op.add_column('wallet_ledger', sa.Column('cashout_request_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_wallet_ledger_cashout_request_id', 'wallet_ledger', 'cashout_requests',
        ['cashout_request_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_wallet_ledger_cashout_request_id', 'wallet_ledger', type_='foreignkey')
    op.drop_column('wallet_ledger', 'cashout_request_id')

    op.drop_index(op.f('ix_cashout_requests_user_id'), table_name='cashout_requests')
    op.drop_table('cashout_requests')
