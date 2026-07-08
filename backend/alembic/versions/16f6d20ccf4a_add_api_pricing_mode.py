"""add api pricing mode

Revision ID: 16f6d20ccf4a
Revises: e1f90599e8ca
Create Date: 2026-07-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '16f6d20ccf4a'
down_revision: Union[str, Sequence[str], None] = 'e1f90599e8ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


custom_apis_table = sa.table(
    'custom_apis',
    sa.column('pricing_mode', sa.String),
    sa.column('price_bdt', sa.Numeric(10, 2)),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('custom_apis', sa.Column(
        'pricing_mode',
        sa.Enum('free', 'one_time', 'per_call', 'subscription', name='apipricingmode', native_enum=False, length=32),
        nullable=False, server_default='free',
    ))
    op.add_column('custom_apis', sa.Column('included_call_quota', sa.Integer(), nullable=True))

    op.execute(
        custom_apis_table.update()
        .where(custom_apis_table.c.price_bdt.is_not(None), custom_apis_table.c.price_bdt > 0)
        .values(pricing_mode='one_time')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('custom_apis', 'included_call_quota')
    op.drop_column('custom_apis', 'pricing_mode')
