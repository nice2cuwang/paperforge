"""add is_vision to llm_configs

Revision ID: b8f2a1c3d4e5
Revises: af6fb6f7723b
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "b8f2a1c3d4e5"
down_revision = "af6fb6f7723b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_configs",
        sa.Column("is_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("llm_configs", "is_vision")
