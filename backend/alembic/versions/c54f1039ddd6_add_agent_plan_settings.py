"""add agent plan settings

Revision ID: c54f1039ddd6
Revises: 0c008392353a
Create Date: 2026-08-14 02:52:59.284069

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c54f1039ddd6'
down_revision: Union[str, Sequence[str], None] = '0c008392353a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


plan_settings_table = sa.table(
    'plan_settings',
    sa.column('tier', sa.String),
    sa.column('agent_runs_per_day', sa.Integer),
    sa.column('agent_run_price_bdt', sa.Numeric(10, 2)),
)


def upgrade() -> None:
    """Upgrade schema."""
    # server_default backfills any existing free/pro/max rows at ALTER TABLE
    # time (0 runs/day = feature disabled); the UPDATEs below then set the
    # real per-tier values, matching e1f90599e8ca's pattern for this table.
    op.add_column('plan_settings', sa.Column(
        'agent_runs_per_day', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('plan_settings', sa.Column(
        'agent_run_price_bdt', sa.Numeric(precision=10, scale=2), nullable=False,
        server_default='0.00'))

    op.execute(
        plan_settings_table.update().where(plan_settings_table.c.tier == 'free').values(
            agent_runs_per_day=0, agent_run_price_bdt=0)
    )
    op.execute(
        plan_settings_table.update().where(plan_settings_table.c.tier == 'pro').values(
            agent_runs_per_day=5, agent_run_price_bdt=10)
    )
    op.execute(
        plan_settings_table.update().where(plan_settings_table.c.tier == 'max').values(
            agent_runs_per_day=25, agent_run_price_bdt=10)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('plan_settings', 'agent_run_price_bdt')
    op.drop_column('plan_settings', 'agent_runs_per_day')
