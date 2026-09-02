"""add is_image_gen to llm_configs

生图模型角色位：找不到合适论文图表时，工作流用该配置调
OpenAI 兼容 /images/generations 生成主题配图（SiliconFlow、
OpenAI、智谱、火山方舟等均兼容该协议）。

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_configs", sa.Column("is_image_gen", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("llm_configs", "is_image_gen")
