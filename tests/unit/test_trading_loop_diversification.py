"""Integration tests for the trading-loop diversification stamp/gate.

Verifies the default-off contract (no behaviour change), the shadow-mode audit
stamp, and the enforce-mode block (CycleStatus.DIVERSIFICATION_REJECTED).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.execution.models import PaperPosition
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import TradingLoop
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator


def _limits() -> RiskLimits:
    return RiskLimits(
        initial_equity=10000.0,
        max_risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_total_drawdown_pct=5.0,
        max_open_positions=5,
        max_leverage=1.0,
        require_stop_loss=True,
        allow_averaging_down=False,
        allow_martingale=False,
        kill_switch_enabled=True,
        min_signal_confidence=0.75,
        min_signal_confluence_count=2,
    )


def _loop(tmp_path) -> TradingLoop:
    exec_eng = PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "exec_audit.jsonl"),
    )
    return TradingLoop(
        risk_engine=RiskEngine(_limits()),
        execution_engine=exec_eng,
        market_data_adapter=MockMarketDataAdapter(),
        signal_generator=SignalGenerator(
            min_confidence=0.75, min_confluence=2, stop_loss_pct=2.5, take_profit_pct=5.0
        ),
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _seed_btc(loop: TradingLoop) -> None:
    """Make the paper book 100% BTC so any BTC/ETH add breaches the short-term cap."""
    loop._exec.portfolio.positions["BTC/USDT"] = PaperPosition(
        symbol="BTC/USDT",
        quantity=0.1,
        avg_entry_price=60000.0,
        stop_loss=59000.0,
        take_profit=63000.0,
        opened_at=datetime.now(UTC).isoformat(),
    )


def _strong_eth_analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_eth_div",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.85,
        relevance_score=0.90,
        impact_score=0.80,
        confidence_score=0.85,
        novelty_score=0.70,
        actionable=True,
        affected_assets=["ETH", "ETH/USDT"],
        tags=["etf", "bullish", "adoption"],
        spam_probability=0.02,
        explanation_short="ETH strong bullish catalyst.",
        explanation_long="Detailed reasoning.",
    )


@pytest.mark.asyncio
async def test_disabled_by_default_no_diversification_notes(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("APP_DIVERSIFICATION_ENABLED", raising=False)
    loop = _loop(tmp_path)
    _seed_btc(loop)
    cycle = await loop.run_cycle(_strong_eth_analysis(), "ETH/USDT")
    # Default-off: no diversification stamp anywhere in the notes.
    assert not any("diversification" in n for n in cycle.notes)
    assert cycle.status != CycleStatus.DIVERSIFICATION_REJECTED


@pytest.mark.asyncio
async def test_shadow_mode_stamps_but_does_not_block(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DIVERSIFICATION_ENABLED", "true")
    monkeypatch.setenv("APP_DIVERSIFICATION_SHADOW_ONLY", "true")
    loop = _loop(tmp_path)
    _seed_btc(loop)
    cycle = await loop.run_cycle(_strong_eth_analysis(), "ETH/USDT")
    # Shadow: the audit is stamped (recommendation present) but never blocks.
    assert any(n.startswith("diversification:") for n in cycle.notes)
    assert cycle.status != CycleStatus.DIVERSIFICATION_REJECTED


@pytest.mark.asyncio
async def test_enforce_mode_blocks_btc_eth_overconcentration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DIVERSIFICATION_ENABLED", "true")
    monkeypatch.setenv("APP_DIVERSIFICATION_SHADOW_ONLY", "false")
    loop = _loop(tmp_path)
    _seed_btc(loop)
    cycle = await loop.run_cycle(_strong_eth_analysis(), "ETH/USDT")
    # Enforce: BTC+ETH short-term cap breach blocks the cycle.
    assert cycle.status == CycleStatus.DIVERSIFICATION_REJECTED
    assert cycle.order_created is False
    assert any("diversification_reason" in n for n in cycle.notes)
