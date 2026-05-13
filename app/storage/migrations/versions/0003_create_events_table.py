"""create historical_events table

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_events",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("event_date", sa.Date, nullable=False, index=True),
        sa.Column("category", sa.String(50), nullable=False, index=True),
        sa.Column("sentiment_direction", sa.String(20), nullable=False, server_default="neutral"),
        sa.Column("impact_magnitude", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("affected_assets", JSON, nullable=True),
        sa.Column("affected_sectors", JSON, nullable=True),
        sa.Column("tags", JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("historical_events")
