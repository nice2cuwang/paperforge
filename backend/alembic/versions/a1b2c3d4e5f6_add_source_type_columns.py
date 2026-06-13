"""add source_type to papers and evidence_cards

Revision ID: a1b2c3d4e5f6
Revises: 607cc55778da
Create Date: 2026-06-11
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "1a6a93bbbcc9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column(
            "source_type",
            sa.String(32),
            nullable=True,
            comment="academic | web | community | llm_knowledge",
        ),
    )
    op.create_index("ix_papers_source_type", "papers", ["source_type"])

    op.add_column(
        "evidence_cards",
        sa.Column(
            "source_type",
            sa.String(32),
            nullable=True,
            comment="academic | web | community | llm_knowledge",
        ),
    )
    op.create_index("ix_evidence_cards_source_type", "evidence_cards", ["source_type"])


def downgrade() -> None:
    op.drop_index("ix_evidence_cards_source_type", table_name="evidence_cards")
    op.drop_column("evidence_cards", "source_type")
    op.drop_index("ix_papers_source_type", table_name="papers")
    op.drop_column("papers", "source_type")
