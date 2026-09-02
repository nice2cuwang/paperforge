"""store prompt and response text in audit_logs

为「对话式工作区展示真实团队工作」功能落库 prompt 原文与响应摘要：
audit_logs 原本只存 SHA-256 哈希（便于审计不泄密），但用户明确希望
在 Chat 界面看到系统发给模型的完整数据，故新增原文列（本地单用户工具）。

Revision ID: d4e5f6a7b8c9
Revises: c9d3e7f1a2b4
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c9d3e7f1a2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("system_prompt_text", sa.Text(), nullable=True))
    op.add_column("audit_logs", sa.Column("user_prompt_text", sa.Text(), nullable=True))
    op.add_column("audit_logs", sa.Column("response_text", sa.Text(), nullable=True))
    op.add_column("audit_logs", sa.Column("purpose", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "purpose")
    op.drop_column("audit_logs", "response_text")
    op.drop_column("audit_logs", "user_prompt_text")
    op.drop_column("audit_logs", "system_prompt_text")
