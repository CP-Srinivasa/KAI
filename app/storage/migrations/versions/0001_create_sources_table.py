"""create sources table

Revision ID: 0001
Revises:
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("source_id", sa.String(36), primary_key=True),
        sa.Column("source_type", sa.String(50), nullable=False, index=True),
        sa.Column("provider", sa.String(100), nullable=True, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="planned", index=True),
        sa.Column("auth_mode", sa.String(50), nullable=False, server_default="none"),
        sa.Column("original_url", sa.Text, nullable=False, unique=True),
        sa.Column("normalized_url", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("sources")
