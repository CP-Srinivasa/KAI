"""ORM models for trading cycle and portfolio state persistence.

V-4 (DB-based aggregation): These models provide the DB-backed persistence
layer for TradingLoop cycles and portfolio snapshots. The JSONL audit trail
remains the primary source of truth during the transition period.

Transition path:
1. (current) JSONL-only: trading_loop_audit.jsonl + paper_execution_audit.jsonl
2. (next) Dual-write: run_cycle() writes to both JSONL and DB
3. (future) DB-primary: build_portfolio_snapshot() reads from DB, JSONL as fallback

Both models use append-only semantics — records are never updated, only inserted.
Corrections are expressed as new records with higher created_at timestamps.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.db.session import Base


class TradingCycleRecord(Base):
    """Append-only DB record for one TradingLoop run-once cycle.

    Each row corresponds to one call to TradingLoop.run_cycle() / run_trading_loop_once().
    No foreign key to portfolio state — cycles are independent audit records.

    JSONL equivalent: artifacts/trading_loop_audit.jsonl
    """

    __tablename__ = "trading_cycles"
    __table_args__ = (
        Index("ix_trading_cycles_symbol_created", "symbol", "created_at"),
        Index("ix_trading_cycles_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # Core cycle fields (mirrors LoopCycle domain model)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="paper")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, server_default="coingecko")
    analysis_profile: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="conservative"
    )
    # Explicit index declared in __table_args__ to keep naming deterministic.
    status: Mapped[str] = mapped_column(String(40), nullable=False)

    # Step flags
    market_data_fetched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    signal_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    risk_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    order_created: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    fill_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Optional linked IDs
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_check_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Timing
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Free-form audit notes as JSON array
    notes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )


class PortfolioStateRecord(Base):
    """Append-only DB record for one paper portfolio state snapshot.

    Snapshots are taken after each fill_simulated=True cycle.
    No position update in-place — new row for every snapshot.

    JSONL equivalent: artifacts/paper_execution_audit.jsonl
    """

    __tablename__ = "portfolio_states"
    __table_args__ = (Index("ix_portfolio_states_symbol_created", "symbol", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Reference to source cycle
    cycle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Portfolio state at snapshot time
    equity_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    gross_exposure_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Full position snapshot as JSON (mirrors paper_execution_audit row)
    positions_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    # Metadata
    snapshot_mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="paper")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
