"""add status column to canonical_documents

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "canonical_documents",
        sa.Column("status", sa.String(length=20), nullable=True, server_default="pending"),
    )
    op.create_index("ix_canonical_documents_status", "canonical_documents", ["status"])

    op.execute(
        """
        UPDATE canonical_documents
        SET status = CASE
            WHEN is_duplicate = true THEN 'duplicate'
            WHEN is_analyzed = true THEN 'analyzed'
            ELSE 'persisted'
        END
        """
    )
    with op.batch_alter_table("canonical_documents") as batch_op:
        batch_op.alter_column("status", nullable=False, server_default="pending")


def downgrade() -> None:
    op.drop_index("ix_canonical_documents_status", "canonical_documents")
    op.drop_column("canonical_documents", "status")
