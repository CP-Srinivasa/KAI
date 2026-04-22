"""Tests for D-182 paper-execution priority-tier gate."""

from __future__ import annotations

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


def _loop(tmp_path) -> TradingLoop:
    risk = RiskEngine(
        RiskLimits(
            initial_equity=10000.0,
            max_risk_per_trade_pct=0.25,
            max_daily_loss_pct=1.0,
            max_total_drawdown_pct=5.0,
            max_open_positions=3,
            max_leverage=1.0,
            require_stop_loss=True,
            allow_averaging_down=False,
            allow_martingale=False,
            kill_switch_enabled=True,
            min_signal_confidence=0.75,
            min_signal_confluence_count=2,
        )
    )
    exec_eng = PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "exec_audit.jsonl"),
    )
    return TradingLoop(
        risk_engine=risk,
        execution_engine=exec_eng,
        market_data_adapter=MockMarketDataAdapter(),
        signal_generator=SignalGenerator(
            min_confidence=0.75,
            min_confluence=2,
            stop_loss_pct=2.5,
            take_profit_pct=5.0,
        ),
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _analysis(priority: int | None) -> AnalysisResult:
    return AnalysisResult(
        document_id=f"doc_p{priority}",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.85,
        relevance_score=0.90,
        impact_score=0.80,
        confidence_score=0.85,
        novelty_score=0.70,
        actionable=True,
        affected_assets=["BTC", "BTC/USDT"],
        tags=["etf", "bullish"],
        spam_probability=0.02,
        explanation_short="Strong bullish catalyst.",
        explanation_long="Reasoning.",
        recommended_priority=priority,
    )


@pytest.mark.asyncio
async def test_default_threshold_allows_any_priority(tmp_path) -> None:
    """paper_min_priority=1 (default) must not block low-priority analyses."""
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=3), "BTC/USDT")
    assert cycle.status != CycleStatus.PRIORITY_REJECTED


@pytest.mark.asyncio
async def test_threshold_10_blocks_below_tier(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P<10 must be rejected when threshold is raised to the high-conviction tier."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=8), "BTC/USDT")
    assert cycle.status == CycleStatus.PRIORITY_REJECTED
    assert not cycle.market_data_fetched  # gate runs before market-data fetch
    assert any("priority_gate_reject:8" in n for n in cycle.notes)
    assert any("threshold:10" in n for n in cycle.notes)


@pytest.mark.asyncio
async def test_threshold_10_allows_p10(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P=10 must pass the gate even at threshold=10 (boundary, inclusive)."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=10), "BTC/USDT")
    assert cycle.status != CycleStatus.PRIORITY_REJECTED


@pytest.mark.asyncio
async def test_none_priority_blocked_when_gate_active(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """priority=None is treated as 'unknown' and rejected when threshold>1."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=None), "BTC/USDT")
    assert cycle.status == CycleStatus.PRIORITY_REJECTED
    assert any("priority_gate_reject:None" in n for n in cycle.notes)


@pytest.mark.asyncio
async def test_none_priority_allowed_at_default_threshold(tmp_path) -> None:
    """priority=None must not be penalised while the gate is the no-op default."""
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=None), "BTC/USDT")
    assert cycle.status != CycleStatus.PRIORITY_REJECTED
