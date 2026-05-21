"""create_llm_configs_table

Revision ID: c133a708dc21
Revises: 20260515_0001
Create Date: 2026-05-21 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c133a708dc21"
down_revision = "20260515_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, server_default="Default"),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="openai"),
        sa.Column("model", sa.String(length=128), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("api_base", sa.Text(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="4096"),
        sa.Column("timeout", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("proxy_url", sa.Text(), nullable=True),
        sa.Column("use_system_proxy", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("extra_headers", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("extra_body", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("strategy_mode", sa.String(length=16), nullable=False, server_default="balanced"),
        sa.Column("enable_reasoning", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("preferred_max_tokens", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("llm_configs")
