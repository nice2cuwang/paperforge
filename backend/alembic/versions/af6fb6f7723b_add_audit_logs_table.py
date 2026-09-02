"""add audit_logs table

Revision ID: af6fb6f7723b
Revises: a1b2c3d4e5f6
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa


revision = "af6fb6f7723b"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("call_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("strategy_mode", sa.String(length=16), nullable=True),
        sa.Column("system_prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("user_prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("response_format", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_call_id", "audit_logs", ["call_id"])
    op.create_index("ix_audit_logs_task_id", "audit_logs", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_task_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_call_id", table_name="audit_logs")
    op.drop_table("audit_logs")
