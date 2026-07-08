"""add wallets, wallet_ledger, and plan_settings monetization columns

Revision ID: e1f90599e8ca
Revises: 5b9817751bb1
Create Date: 2026-07-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f90599e8ca'
down_revision: Union[str, Sequence[str], None] = '5b9817751bb1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


plan_settings_table = sa.table(
    'plan_settings',
    sa.column('tier', sa.String),
    sa.column('monthly_call_quota', sa.Integer),
    sa.column('platform_cut_pct', sa.Numeric(5, 2)),
    sa.column('can_cashout', sa.Boolean),
    sa.column('max_invitees_per_api', sa.Integer),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('wallets',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('balance_bdt', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('earnings_bdt', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )

    # user_id is nullable — platform_cut rows (Phase W3) belong to the
    # platform, not a user, and carry user_id = NULL.
    op.create_table('wallet_ledger',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('bucket', sa.String(length=10), nullable=False),
    sa.Column('amount_bdt', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('reason', sa.String(length=20), nullable=False),
    sa.Column('balance_after_bdt', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('execution_id', sa.UUID(), nullable=True),
    sa.Column('api_id', sa.UUID(), nullable=True),
    sa.Column('transaction_id', sa.UUID(), nullable=True),
    sa.Column('counterparty_user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['api_id'], ['custom_apis.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['counterparty_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['execution_id'], ['api_executions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['transaction_id'], ['payment_transactions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_wallet_ledger_user_id'), 'wallet_ledger', ['user_id'], unique=False)
    op.create_index(op.f('ix_wallet_ledger_created_at'), 'wallet_ledger', ['created_at'], unique=False)
    op.create_index('ix_wallet_ledger_user_created', 'wallet_ledger', ['user_id', 'created_at'], unique=False)

    # server_default on the two NOT NULL columns backfills plan_settings'
    # existing free/pro/max rows at ALTER TABLE time; the immediate UPDATEs
    # below then set the real per-tier values. The defaults are left in place
    # afterward as a harmless safety net (e.g. app/api/admin.py's
    # unseeded-tier fallback row construction never sets these explicitly).
    op.add_column('plan_settings', sa.Column('monthly_call_quota', sa.Integer(), nullable=True))
    op.add_column('plan_settings', sa.Column(
        'platform_cut_pct', sa.Numeric(precision=5, scale=2), nullable=False, server_default='0'))
    op.add_column('plan_settings', sa.Column(
        'can_cashout', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('plan_settings', sa.Column('max_invitees_per_api', sa.Integer(), nullable=True))

    op.execute(
        plan_settings_table.update().where(plan_settings_table.c.tier == 'free').values(
            monthly_call_quota=100, platform_cut_pct=0, can_cashout=False, max_invitees_per_api=1)
    )
    op.execute(
        plan_settings_table.update().where(plan_settings_table.c.tier == 'pro').values(
            monthly_call_quota=5000, platform_cut_pct=25, can_cashout=False, max_invitees_per_api=25)
    )
    op.execute(
        plan_settings_table.update().where(plan_settings_table.c.tier == 'max').values(
            monthly_call_quota=50000, platform_cut_pct=10, can_cashout=True, max_invitees_per_api=None)
    )

    op.execute(
        "INSERT INTO wallets (user_id, balance_bdt, earnings_bdt, updated_at) "
        "SELECT id, 0, 0, now() FROM users"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('plan_settings', 'max_invitees_per_api')
    op.drop_column('plan_settings', 'can_cashout')
    op.drop_column('plan_settings', 'platform_cut_pct')
    op.drop_column('plan_settings', 'monthly_call_quota')

    op.drop_index('ix_wallet_ledger_user_created', table_name='wallet_ledger')
    op.drop_index(op.f('ix_wallet_ledger_created_at'), table_name='wallet_ledger')
    op.drop_index(op.f('ix_wallet_ledger_user_id'), table_name='wallet_ledger')
    op.drop_table('wallet_ledger')

    op.drop_table('wallets')
