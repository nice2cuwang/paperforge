"""add_strategy_fields_to_llm_config

Revision ID: 607cc55778da
Revises: 20260515_0001
Create Date: 2026-05-18 07:35:44.684202
"""

from alembic import op
import sqlalchemy as sa



revision = '607cc55778da'
down_revision = 'c133a708dc21'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns (strategy_mode, enable_reasoning, preferred_max_tokens) are already
    # included in the create_table migration (c133a708dc21). This migration is
    # kept as a no-op so that existing revision chains remain intact.
    pass


def downgrade() -> None:
    # No-op — columns are owned by the create_table migration.
    pass
