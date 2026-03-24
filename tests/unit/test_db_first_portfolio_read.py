"""Unit tests for DB-primary portfolio snapshot path (V-4 Phase 2 → Phase 3 migration).

Phase 3 replaces the TradingCycleRecord-based path with PortfolioStateRecord-based
reconstruction. This file retains the integration-level contract tests to ensure
the public build_portfolio_snapshot() API behaves consistently.

See test_portfolio_snapshot_db_primary.py for detailed Phase 3 unit tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.execution.portfolio_read import PortfolioSnapshot, build_portfolio_snapshot
from app.storage.models.trading import PortfolioStateRecord

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_state_record_mock(*, cycle_id: str = "test-cycle-001") -> MagicMock:
    record = MagicMock(spec=PortfolioStateRecord)
    record.cycle_id = cycle_id
    record.symbol = "BTC/USDT"
    record.equity_usd = 10000.0
    record.position_count = 0
    record.gross_exposure_usd = 0.0
    record.positions_json = {"cash": 10000.0, "positions": {}}
    record.snapshot_mode = "paper"
    return record


def _make_session_factory(state_record: MagicMock | None) -> MagicMock:
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


# ── build_portfolio_snapshot DB-primary path ────────────────────────────────────


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_db_primary_when_record_present(
    tmp_path: Path,
) -> None:
    """session_factory + PortfolioStateRecord → source=db_portfolio_state."""
    factory = _make_session_factory(_make_state_record_mock())

    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "missing.jsonl",
        session_factory=factory,
    )

    assert isinstance(snapshot, PortfolioSnapshot)
    assert snapshot.source == "db_portfolio_state"
    assert snapshot.available is True
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_jsonl_fallback_when_db_empty(
    tmp_path: Path,
) -> None:
    """session_factory + no PortfolioStateRecord → JSONL fallback."""
    factory = _make_session_factory(None)

    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "missing.jsonl",
        session_factory=factory,
    )

    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.position_count == 0


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_jsonl_fallback_when_no_factory(
    tmp_path: Path,
) -> None:
    """No session_factory → JSONL path, no DB query attempted."""
    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "missing.jsonl",
        session_factory=None,
    )

    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.available is True


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_jsonl_fallback_when_db_raises(
    tmp_path: Path,
) -> None:
    """DB query exception → graceful JSONL fallback."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("timeout"))

    factory = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = cm

    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "missing.jsonl",
        session_factory=factory,
    )

    assert snapshot.source == "paper_execution_audit_replay"
    assert snapshot.available is True


# ── Snapshot structure invariants ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_no_factory_returns_correct_structure(
    tmp_path: Path,
) -> None:
    """Without session_factory, existing snapshot fields remain intact."""
    snapshot = await build_portfolio_snapshot(
        audit_path=tmp_path / "no_file.jsonl",
        session_factory=None,
    )

    assert snapshot.generated_at_utc is not None
    assert isinstance(snapshot.cash_usd, float)
    assert isinstance(snapshot.position_count, int)
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False
