"""Integration test: Entry-Safety-Mode gate in the autonomous TradingLoop.

Loop-level contract end-to-end (Goal 2026-06-01):
- EXECUTION_ENTRY_MODE=disabled -> run_cycle returns ENTRY_MODE_BLOCKED, opens no
  order, and does NOT even fetch market data (highest-level kill-switch);
- EXECUTION_ENTRY_MODE=paper (default) -> run_cycle is not entry-mode-blocked;
- the promoted-signal path (operator/bridge/premium) is intentionally OUT of
  scope: run_promoted_signal is never entry-mode-blocked, even when disabled.
"""

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
from app.signals.tv_consumer import load_pending_promoted


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
        regime_filter_enabled=False,
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


def _strong_eth() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_eth_entrymode",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.85,
        relevance_score=0.90,
        impact_score=0.80,
        confidence_score=0.85,
        novelty_score=0.70,
        actionable=True,
        affected_assets=["ETH", "ETH/USDT"],
        tags=["bullish"],
        spam_probability=0.02,
        explanation_short="ETH strong bullish catalyst.",
        explanation_long="Detailed reasoning.",
        recommended_priority=10,
    )


@pytest.mark.asyncio
async def test_disabled_blocks_autonomous_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    assert cycle.status == CycleStatus.ENTRY_MODE_BLOCKED
    assert cycle.order_created is False
    # Kill-switch runs before any market-data fetch.
    assert cycle.market_data_fetched is False
    assert any(n.startswith("entry_mode_blocked:disabled") for n in cycle.notes)


@pytest.mark.asyncio
async def test_paper_mode_is_not_entry_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "paper")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    # Whatever later gate it hits, it must NOT be entry-mode-blocked.
    assert cycle.status != CycleStatus.ENTRY_MODE_BLOCKED
    assert not any(n.startswith("entry_mode_blocked") for n in cycle.notes)


@pytest.mark.asyncio
async def test_default_mode_is_not_entry_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("EXECUTION_ENTRY_MODE", raising=False)
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    assert cycle.status != CycleStatus.ENTRY_MODE_BLOCKED


@pytest.mark.asyncio
async def test_promoted_signal_not_gated_by_entry_mode(tmp_path, monkeypatch) -> None:
    # Scope boundary: operator/bridge/premium-promoted signals are a different
    # signal source and must NOT be blocked by the autonomous entry-safety gate.
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    promoted = tmp_path / "promoted.jsonl"
    promoted.write_text(
        '{"decision_id":"dec_em","timestamp_utc":"2026-06-01T10:00:00+00:00",'
        '"symbol":"ETHUSDT","market":"crypto","venue":"paper","mode":"paper",'
        '"direction":"long","entry_price":2000.0,"stop_loss_price":1950.0,'
        '"take_profit_price":2100.0,"confidence_score":0.8,"confluence_count":2,'
        '"thesis":"promoted","source_document_id":"req_em","execution_state":"pending"}\n',
        encoding="utf-8",
    )
    candidates = load_pending_promoted(promoted)
    assert len(candidates) == 1
    loop = _loop(tmp_path)
    cycle = await loop.run_promoted_signal(candidates[0])
    assert cycle.status != CycleStatus.ENTRY_MODE_BLOCKED
