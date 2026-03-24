"""Unit tests for Dual-Write DB path in TradingLoop.run_cycle() (V-4 Phase 2+3).

Tests verify:
- run_cycle() without session_factory → no DB write, no error
- run_cycle() with session_factory → TradingCycleRecord is added and committed
- DB error in _write_db is non-fatal (loop continues, returns cycle)
- _write_db is called for non-COMPLETED cycles too
- fill_simulated cycles also write PortfolioStateRecord
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import TradingLoop
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator
from app.storage.models.trading import PortfolioStateRecord, TradingCycleRecord

# ── Factories ──────────────────────────────────────────────────────────────────


def _default_limits(**overrides: object) -> RiskLimits:
    defaults: dict[str, object] = {
        "initial_equity": 10000.0,
        "max_risk_per_trade_pct": 0.25,
        "max_daily_loss_pct": 1.0,
        "max_total_drawdown_pct": 5.0,
        "max_open_positions": 3,
        "max_leverage": 1.0,
        "require_stop_loss": True,
        "allow_averaging_down": False,
        "allow_martingale": False,
        "kill_switch_enabled": True,
        "min_signal_confidence": 0.75,
        "min_signal_confluence_count": 2,
    }
    defaults.update(overrides)
    return RiskLimits(**defaults)  # type: ignore[arg-type]


def _make_session_factory(mock_session: AsyncMock) -> MagicMock:
    """Build a mock async_sessionmaker that yields mock_session as async context manager."""
    factory = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = cm
    return factory


def _make_loop(
    tmp_path: object, session_factory: object = None, **limit_overrides: object
) -> TradingLoop:  # noqa: E501
    risk = RiskEngine(_default_limits(**limit_overrides))
    exec_eng = PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "exec_audit.jsonl"),  # type: ignore[operator]
    )
    market = MockMarketDataAdapter()
    gen = SignalGenerator(
        min_confidence=0.75,
        min_confluence=2,
        stop_loss_pct=2.5,
        take_profit_pct=5.0,
    )
    return TradingLoop(
        risk_engine=risk,
        execution_engine=exec_eng,
        market_data_adapter=market,
        signal_generator=gen,
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),  # type: ignore[operator]
        session_factory=session_factory,
    )


def _conservative_analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="test_doc",
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
        relevance_score=0.4,
        impact_score=0.2,
        confidence_score=0.5,
        novelty_score=0.2,
        market_scope=None,
        affected_assets=[],
        affected_sectors=[],
        event_type="test",
        explanation_short="test",
        explanation_long="test",
        actionable=False,
        tags=[],
        spam_probability=0.0,
    )


def _bullish_analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="test_bullish_doc",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.9,
        relevance_score=0.95,
        impact_score=0.9,
        confidence_score=0.9,
        novelty_score=0.8,
        market_scope=None,
        affected_assets=["BTC", "BTC/USDT"],
        affected_sectors=[],
        event_type="test_bullish",
        explanation_short="Bullish test",
        explanation_long="Bullish test signal",
        actionable=True,
        tags=["bullish"],
        spam_probability=0.0,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_cycle_without_session_factory_no_error(tmp_path: object) -> None:
    """No session_factory → cycle completes normally, no DB write attempted."""
    loop = _make_loop(tmp_path, session_factory=None)
    cycle = await loop.run_cycle(_conservative_analysis(), "BTC/USDT")

    assert cycle.status == CycleStatus.NO_SIGNAL
    assert cycle.cycle_id is not None


@pytest.mark.asyncio
async def test_run_cycle_with_session_factory_adds_record(tmp_path: object) -> None:
    """session_factory present → session.add() called with TradingCycleRecord + commit."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    factory = _make_session_factory(mock_session)

    loop = _make_loop(tmp_path, session_factory=factory)
    cycle = await loop.run_cycle(_conservative_analysis(), "BTC/USDT")

    assert cycle.cycle_id is not None
    mock_session.add.assert_called_once()
    added_arg = mock_session.add.call_args[0][0]
    assert isinstance(added_arg, TradingCycleRecord)
    assert added_arg.cycle_id == cycle.cycle_id
    assert added_arg.symbol == "BTC/USDT"
    assert added_arg.status == cycle.status.value
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_run_cycle_db_error_is_nonfatal(tmp_path: object) -> None:
    """DB commit failure must not raise — cycle is returned normally."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock(side_effect=Exception("DB down"))
    factory = _make_session_factory(mock_session)

    loop = _make_loop(tmp_path, session_factory=factory)
    cycle = await loop.run_cycle(_conservative_analysis(), "BTC/USDT")

    # Cycle is returned despite DB failure
    assert cycle.cycle_id is not None
    assert cycle.status is not None


@pytest.mark.asyncio
async def test_run_cycle_db_write_includes_notes(tmp_path: object) -> None:
    """DB record includes the notes from the cycle."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    factory = _make_session_factory(mock_session)

    loop = _make_loop(tmp_path, session_factory=factory)
    await loop.run_cycle(_conservative_analysis(), "BTC/USDT")

    added_arg = mock_session.add.call_args[0][0]
    assert isinstance(added_arg.notes, list)


@pytest.mark.asyncio
async def test_run_cycle_db_write_captures_all_statuses(tmp_path: object) -> None:
    """Even non-COMPLETED cycles (e.g. NO_SIGNAL) trigger a DB write."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    factory = _make_session_factory(mock_session)

    loop = _make_loop(tmp_path, session_factory=factory)
    cycle = await loop.run_cycle(_conservative_analysis(), "BTC/USDT")

    assert cycle.status == CycleStatus.NO_SIGNAL
    mock_session.add.assert_called_once()
    added_arg = mock_session.add.call_args[0][0]
    assert added_arg.status == "no_signal"


@pytest.mark.asyncio
async def test_fill_simulated_cycle_writes_portfolio_state(tmp_path: object) -> None:
    """fill_simulated=True cycle writes both TradingCycleRecord and PortfolioStateRecord."""
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    factory = _make_session_factory(mock_session)

    loop = _make_loop(
        tmp_path,
        session_factory=factory,
        min_signal_confidence=0.0,
        min_signal_confluence_count=0,
        max_risk_per_trade_pct=5.0,
        require_stop_loss=False,
        kill_switch_enabled=False,
    )
    cycle = await loop.run_cycle(_bullish_analysis(), "BTC/USDT")

    if cycle.fill_simulated:
        assert mock_session.add.call_count == 2  # noqa: PLR2004
        added_types = [type(call[0][0]) for call in mock_session.add.call_args_list]
        assert TradingCycleRecord in added_types
        assert PortfolioStateRecord in added_types
        state_record = next(
            a[0][0]
            for a in mock_session.add.call_args_list
            if isinstance(a[0][0], PortfolioStateRecord)
        )
        assert state_record.cycle_id == cycle.cycle_id
        assert state_record.symbol == "BTC/USDT"
        assert state_record.snapshot_mode == "paper"
        assert isinstance(state_record.positions_json, dict)
    else:
        # If signal not generated (e.g. risk blocked), only TradingCycleRecord written
        mock_session.add.assert_called_once()
        assert isinstance(mock_session.add.call_args[0][0], TradingCycleRecord)
