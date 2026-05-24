"""add directional_confidence column to canonical_documents

Revision ID: 0008
Revises: 74fab3f5b5d5
Create Date: 2026-05-24

F3-V-0 (Sprint 2026-05-24) — Confidence-Recalibration-Voraussetzung:
``directional_confidence`` ist LLM-Output (0.0..1.0) und wird im
Eligibility-Gate als Schwelle verwendet (bullish>=0.8, bearish>=0.95).
Wert wurde bisher nur durch RAM-Pipeline gereicht; Persistence ermoeglicht
spaeter (~3 Wochen Sammlung) eine Outcome-zu-Confidence-Korrelations-Analyse
zur Schwellen-Recalibration. Siehe
``artifacts/operator_memos/f3_confidence_recalibration_blocked_2026-05-24.md``
und ``artifacts/operator_memos/dispatch_filter_root_befund_2026-05-24.md``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "74fab3f5b5d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "canonical_documents",
        sa.Column("directional_confidence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("canonical_documents", "directional_confidence")
