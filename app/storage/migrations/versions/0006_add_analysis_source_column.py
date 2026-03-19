"""add analysis_source column to canonical_documents

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "canonical_documents",
        sa.Column("analysis_source", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_canonical_documents_analysis_source",
        "canonical_documents",
        ["analysis_source"],
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_documents_analysis_source", "canonical_documents")
    op.drop_column("canonical_documents", "analysis_source")
