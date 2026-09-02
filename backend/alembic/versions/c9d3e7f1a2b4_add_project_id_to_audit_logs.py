"""add project_id to audit_logs

Revision ID: c9d3e7f1a2b4
Revises: b8f2a1c3d4e5
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa


revision = "c9d3e7f1a2b4"
down_revision = "b8f2a1c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("project_id", sa.String(length=36), nullable=True))
    op.create_index("ix_audit_logs_project_id", "audit_logs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_project_id", table_name="audit_logs")
    op.drop_column("audit_logs", "project_id")
