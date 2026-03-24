"""add novelty_score, spam_probability, priority_score to canonical_documents

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("canonical_documents", sa.Column("novelty_score", sa.Float, nullable=True))
    op.add_column("canonical_documents", sa.Column("spam_probability", sa.Float, nullable=True))
    op.add_column("canonical_documents", sa.Column("priority_score", sa.Integer, nullable=True))
    op.create_index(
        "ix_canonical_documents_priority_score", "canonical_documents", ["priority_score"]
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_documents_priority_score", "canonical_documents")
    op.drop_column("canonical_documents", "priority_score")
    op.drop_column("canonical_documents", "spam_probability")
    op.drop_column("canonical_documents", "novelty_score")
