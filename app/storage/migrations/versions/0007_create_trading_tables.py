"""create trading_cycles and portfolio_states tables

V-4 (DB-based aggregation): Add append-only tables for TradingLoop cycle
records and portfolio state snapshots. These tables are the DB-backed
persistence layer alongside the existing JSONL audit trails.

Transition note:
- JSONL files remain primary source during the dual-write period.
- DB records are supplementary; build_portfolio_snapshot() reads JSONL first.
- Full DB-primary switch is a future sprint (see V-4 in RISK_REGISTER).

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # trading_cycles: one row per run_trading_loop_once() call
    op.create_table(
        "trading_cycles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="paper"),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="coingecko"),
        sa.Column("analysis_profile", sa.String(length=40), nullable=False,
                  server_default="conservative"),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("market_data_fetched", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("signal_generated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("risk_approved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("order_created", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fill_simulated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("decision_id", sa.String(length=64), nullable=True),
        sa.Column("risk_check_id", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.Column("notes", sa.dialects.postgresql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id"),
    )
    op.create_index("ix_trading_cycles_symbol_created", "trading_cycles", ["symbol", "created_at"])
    op.create_index("ix_trading_cycles_status", "trading_cycles", ["status"])
    op.create_index("ix_trading_cycles_cycle_id", "trading_cycles", ["cycle_id"], unique=True)

    # portfolio_states: one row per portfolio snapshot
    op.create_table(
        "portfolio_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("equity_usd", sa.Float(), nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gross_exposure_usd", sa.Float(), nullable=True),
        sa.Column("positions_json", sa.dialects.postgresql.JSON(), nullable=True),
        sa.Column("snapshot_mode", sa.String(length=20), nullable=False, server_default="paper"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_portfolio_states_symbol_created", "portfolio_states", ["symbol", "created_at"]
    )
    op.create_index("ix_portfolio_states_cycle_id", "portfolio_states", ["cycle_id"])


def downgrade() -> None:
    op.drop_index("ix_portfolio_states_cycle_id", table_name="portfolio_states")
    op.drop_index("ix_portfolio_states_symbol_created", table_name="portfolio_states")
    op.drop_table("portfolio_states")

    op.drop_index("ix_trading_cycles_cycle_id", table_name="trading_cycles")
    op.drop_index("ix_trading_cycles_status", table_name="trading_cycles")
    op.drop_index("ix_trading_cycles_symbol_created", table_name="trading_cycles")
    op.drop_table("trading_cycles")
