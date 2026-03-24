"""Tests for DB-primary portfolio snapshot path (V-4 Phase 3).

Tests verify:
- build_portfolio_snapshot() with session_factory → uses PortfolioStateRecord
- positions are reconstructed correctly from positions_json
- cash_usd and equity are computed correctly
- fallback to JSONL when DB has no PortfolioStateRecord
- DB error → falls back to JSONL (non-fatal)
- session_factory=None → JSONL path (no DB query)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.execution.portfolio_read import PortfolioSnapshot, build_portfolio_snapshot
from app.storage.models.trading import PortfolioStateRecord

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_portfolio_state_record(
    *,
    cycle_id: str = "cycle_abc123",
    symbol: str = "BTC/USDT",
    positions: dict | None = None,
    cash: float = 9500.0,
    equity_usd: float = 10500.0,
    position_count: int = 1,
    gross_exposure_usd: float = 1000.0,
) -> MagicMock:
    """Create a mock PortfolioStateRecord with realistic attribute values."""
    if positions is None:
        positions = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "quantity": 0.01,
                "avg_entry_price": 50000.0,
                "stop_loss": 48000.0,
                "take_profit": 55000.0,
            }
        }
    record = MagicMock(spec=PortfolioStateRecord)
    record.cycle_id = cycle_id
    record.symbol = symbol
    record.equity_usd = equity_usd
    record.position_count = position_count
    record.gross_exposure_usd = gross_exposure_usd
    record.positions_json = {
        "initial_equity": 10000.0,
        "cash": cash,
        "open_positions": len(positions),
        "positions": positions,
    }
    record.snapshot_mode = "paper"
    record.created_at = datetime.now(UTC)
    return record


def _make_session_factory(state_record: PortfolioStateRecord | None) -> MagicMock:
    """Build a mock session_factory that returns state_record from the DB query."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = state_record

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    factory = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = cm
    return factory


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_primary_returns_portfolio_state_when_record_exists() -> None:
    """session_factory with a PortfolioStateRecord → snapshot sourced from DB."""
    record = _make_portfolio_state_record()
    factory = _make_session_factory(record)

    snapshot = await build_portfolio_snapshot(session_factory=factory)

    assert isinstance(snapshot, PortfolioSnapshot)
    assert snapshot.source == "db_portfolio_state"
    assert snapshot.available is True
    assert snapshot.error is None
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False


@pytest.mark.asyncio
async def test_db_primary_reconstructs_positions_from_json() -> None:
    """Position data from positions_json is reconstructed into PositionSummary objects."""
    record = _make_portfolio_state_record(
        positions={
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "quantity": 0.02,
                "avg_entry_price": 45000.0,
                "stop_loss": 43000.0,
                "take_profit": 50000.0,
            }
        },
        cash=8900.0,
        position_count=1,
    )
    factory = _make_session_factory(record)

    snapshot = await build_portfolio_snapshot(session_factory=factory)

    assert snapshot.position_count == 1
    assert len(snapshot.positions) == 1
    pos = snapshot.positions[0]
    assert pos.symbol == "BTC/USDT"
    assert pos.quantity == 0.02  # noqa: PLR2004
    assert pos.avg_entry_price == 45000.0  # noqa: PLR2004
    assert pos.stop_loss == 43000.0  # noqa: PLR2004
    assert pos.take_profit == 50000.0  # noqa: PLR2004
    # No live price from DB snapshot
    assert pos.market_price is None
    assert pos.market_data_available is False


@pytest.mark.asyncio
async def test_db_primary_cash_and_equity_from_positions_json() -> None:
    """cash_usd and total_equity_usd are derived from positions_json."""
    record = _make_portfolio_state_record(
        positions={
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "quantity": 1.0,
                "avg_entry_price": 3000.0,
                "stop_loss": None,
                "take_profit": None,
            }
        },
        cash=7000.0,
    )
    factory = _make_session_factory(record)

    snapshot = await build_portfolio_snapshot(session_factory=factory)

    assert snapshot.cash_usd == 7000.0  # noqa: PLR2004
    # total_market_value = 1.0 * 3000.0
    assert snapshot.total_market_value_usd == 3000.0  # noqa: PLR2004
    assert snapshot.total_equity_usd == 10000.0  # noqa: PLR2004


@pytest.mark.asyncio
async def test_db_primary_fallback_to_jsonl_when_no_record(tmp_path: Path) -> None:
    """No PortfolioStateRecord in DB → falls back to JSONL path."""
    factory = _make_session_factory(None)

    snapshot = await build_portfolio_snapshot(
        audit_path=str(tmp_path / "nonexistent.jsonl"),
        session_factory=factory,
    )

    # JSONL path used (missing file = empty portfolio, available=True by design)
    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.position_count == 0


@pytest.mark.asyncio
async def test_db_primary_fallback_on_db_error(tmp_path: Path) -> None:
    """DB query exception → falls back to JSONL gracefully (non-fatal)."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("connection refused"))

    factory = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = cm

    snapshot = await build_portfolio_snapshot(
        audit_path=str(tmp_path / "nonexistent.jsonl"),
        session_factory=factory,
    )

    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.position_count == 0


@pytest.mark.asyncio
async def test_no_session_factory_uses_jsonl_path(tmp_path: Path) -> None:
    """session_factory=None → JSONL path used, no DB query."""
    snapshot = await build_portfolio_snapshot(
        audit_path=str(tmp_path / "nonexistent.jsonl"),
        session_factory=None,
    )

    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.position_count == 0


@pytest.mark.asyncio
async def test_db_primary_audit_path_references_cycle_id() -> None:
    """audit_path in DB-primary snapshot contains the cycle_id for traceability."""
    record = _make_portfolio_state_record(cycle_id="cycle_xyz999")
    factory = _make_session_factory(record)

    snapshot = await build_portfolio_snapshot(session_factory=factory)

    assert "cycle_xyz999" in snapshot.audit_path


@pytest.mark.asyncio
async def test_db_primary_empty_positions_json() -> None:
    """Handles PortfolioStateRecord with null positions_json gracefully."""
    state = MagicMock(spec=PortfolioStateRecord)
    state.cycle_id = "cycle_empty"
    state.symbol = "BTC/USDT"
    state.equity_usd = 10000.0
    state.position_count = 0
    state.gross_exposure_usd = 0.0
    state.positions_json = None
    state.snapshot_mode = "paper"
    state.created_at = datetime.now(UTC)

    factory = _make_session_factory(state)

    snapshot = await build_portfolio_snapshot(session_factory=factory)

    assert snapshot.source == "db_portfolio_state"
    assert snapshot.position_count == 0
    assert snapshot.positions == ()
    assert snapshot.cash_usd == 0.0
