"""Unit tests for DB-first portfolio snapshot path (V-4 Phase 2).

Tests cover:
- DB session provided + records present → DB path (source="db_trading_cycles")
- DB session provided + empty DB → JSONL fallback (source="paper_execution_audit_replay")
- No DB session → JSONL fallback (source="paper_execution_audit_replay")
- DB query exception → JSONL fallback (graceful degradation)
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.execution.portfolio_read import (
    PortfolioSnapshot,
    _build_snapshot_from_db,
    _query_db_cycles,
    build_portfolio_snapshot,
)
from app.storage.models.trading import TradingCycleRecord

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_cycle_record(
    *,
    cycle_id: str = "test-cycle-001",
    symbol: str = "BTC/USDT",
    status: str = "completed",
    fill_simulated: bool = True,
) -> TradingCycleRecord:
    return TradingCycleRecord(
        cycle_id=cycle_id,
        symbol=symbol,
        mode="paper",
        provider="coingecko",
        analysis_profile="conservative",
        status=status,
        market_data_fetched=True,
        signal_generated=True,
        risk_approved=True,
        order_created=True,
        fill_simulated=fill_simulated,
        decision_id=None,
        risk_check_id=None,
        order_id=None,
        started_at="2026-03-23T10:00:00+00:00",
        completed_at="2026-03-23T10:00:01+00:00",
        notes=[],
        created_at=datetime.now(UTC),
    )


def _make_db_session(records: list[TradingCycleRecord]) -> AsyncMock:
    """Build a mock AsyncSession whose execute() returns the given records."""
    session = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = records
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute = AsyncMock(return_value=result_mock)
    return session


# ── _build_snapshot_from_db ────────────────────────────────────────────────────


def test_build_snapshot_from_db_with_completed_records() -> None:
    records = [
        _make_cycle_record(cycle_id="c1", fill_simulated=True),
        _make_cycle_record(cycle_id="c2", fill_simulated=True),
    ]
    generated_at = datetime.now(UTC).isoformat()
    snapshot = _build_snapshot_from_db(records, generated_at)

    assert isinstance(snapshot, PortfolioSnapshot)
    assert snapshot.source == "db_trading_cycles"
    assert snapshot.available is True
    assert snapshot.error is None
    assert snapshot.position_count == 0
    assert snapshot.positions == ()
    assert "2_completed" in snapshot.audit_path
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False


def test_build_snapshot_from_db_no_completed_records() -> None:
    records = [
        _make_cycle_record(cycle_id="c1", status="no_signal", fill_simulated=False),
    ]
    generated_at = datetime.now(UTC).isoformat()
    snapshot = _build_snapshot_from_db(records, generated_at)

    assert snapshot.source == "db_trading_cycles"
    assert snapshot.available is True
    assert "0_completed" in snapshot.audit_path


def test_build_snapshot_from_db_empty_records() -> None:
    generated_at = datetime.now(UTC).isoformat()
    snapshot = _build_snapshot_from_db([], generated_at)

    assert snapshot.source == "db_trading_cycles"
    assert snapshot.available is True


# ── _query_db_cycles ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_db_cycles_returns_records() -> None:
    records = [_make_cycle_record()]
    session = _make_db_session(records)

    result = await _query_db_cycles(session)

    assert result == records


@pytest.mark.asyncio
async def test_query_db_cycles_returns_empty_on_exception() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=Exception("DB connection failed"))

    result = await _query_db_cycles(session)

    assert result == []


# ── build_portfolio_snapshot DB-first path ─────────────────────────────────────


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_db_first_when_records_present(
    tmp_path: Path,
) -> None:
    """DB session + records → source=db_trading_cycles, no JSONL read."""
    records = [_make_cycle_record()]
    session = _make_db_session(records)

    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "missing.jsonl",
        db_session=session,
    )

    assert snapshot.source == "db_trading_cycles"
    assert snapshot.available is True


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_jsonl_fallback_when_db_empty(
    tmp_path: Path,
) -> None:
    """DB session + empty DB → JSONL fallback (missing file → empty snapshot)."""
    session = _make_db_session([])

    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "missing.jsonl",
        db_session=session,
    )

    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.available is True
    assert snapshot.position_count == 0


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_jsonl_fallback_when_no_session(
    tmp_path: Path,
) -> None:
    """No DB session → JSONL path, no DB query attempted."""
    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "missing.jsonl",
        db_session=None,
    )

    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.available is True


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_jsonl_fallback_when_db_raises(
    tmp_path: Path,
) -> None:
    """DB query exception → graceful JSONL fallback."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=Exception("timeout"))

    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "missing.jsonl",
        db_session=session,
    )

    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.available is True


# ── DB-first does not break existing JSONL snapshot structure ──────────────────


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_no_session_returns_correct_structure(
    tmp_path: Path,
) -> None:
    """Without DB session, existing snapshot fields remain intact."""
    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "no_file.jsonl",
        db_session=None,
    )

    assert snapshot.generated_at_utc is not None
    assert isinstance(snapshot.cash_usd, float)
    assert isinstance(snapshot.position_count, int)
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False
