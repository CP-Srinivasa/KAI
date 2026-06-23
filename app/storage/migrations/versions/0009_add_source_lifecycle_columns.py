"""add source-lifecycle columns to sources

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-23

Phase 3 foundation (PR5a). Additive + reversible: four nullable columns on
``sources`` that the discovery/graduation engine (later Phase-3 PR) will write.
No backfill, no NOT NULL, no data migration — a forward deploy and a rollback are
both safe at any time.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources", sa.Column("probation_start_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sources", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("sources", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sources", sa.Column("lifecycle_tier", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "lifecycle_tier")
    op.drop_column("sources", "pinned_at")
    op.drop_column("sources", "last_activity_at")
    op.drop_column("sources", "probation_start_at")
