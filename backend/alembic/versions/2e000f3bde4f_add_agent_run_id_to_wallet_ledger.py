"""add agent run id to wallet ledger

Revision ID: 2e000f3bde4f
Revises: c54f1039ddd6
Create Date: 2026-08-14 02:56:57.806768

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e000f3bde4f'
down_revision: Union[str, Sequence[str], None] = 'c54f1039ddd6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('wallet_ledger', sa.Column('agent_run_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_wallet_ledger_agent_run_id'), 'wallet_ledger', ['agent_run_id'], unique=False)
    op.create_foreign_key(
        'fk_wallet_ledger_agent_run_id', 'wallet_ledger', 'agent_runs',
        ['agent_run_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_wallet_ledger_agent_run_id', 'wallet_ledger', type_='foreignkey')
    op.drop_index(op.f('ix_wallet_ledger_agent_run_id'), table_name='wallet_ledger')
    op.drop_column('wallet_ledger', 'agent_run_id')
