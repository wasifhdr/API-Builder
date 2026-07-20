"""add extraction selector cache

Revision ID: f1a2b3c4d5e6
Revises: a2861ad6bed7
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'a2861ad6bed7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extraction_selector_cache',
        sa.Column('workflow_id', sa.UUID(), nullable=False),
        sa.Column('ref', sa.String(length=64), nullable=False),
        sa.Column('field_name', sa.String(length=200), nullable=False),
        sa.Column('selectors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('healed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('workflow_id', 'ref', 'field_name'),
    )


def downgrade() -> None:
    op.drop_table('extraction_selector_cache')
