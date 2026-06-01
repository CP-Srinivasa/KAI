"""Integration test: NEO-V2 per-symbol post-stop cooldown gate in the loop.

Verifies the loop-level contract end-to-end through run_cycle:
- a symbol with a recent `position_closed reason=stop` in the engine's audit is
  rejected with CycleStatus.COOLDOWN_REJECTED and a `post_stop_cooldown` note,
  and no order is created;
- disabling the window (post_stop_cooldown_min=0) lets the same symbol through
  the cooldown gate (it may still be rejected by other gates, but NOT by
  cooldown);
- an old stop (outside the window) does not block.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

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


def _seed_stop(loop: TradingLoop, symbol: str, minutes_ago: int) -> None:
    """Append a position_closed reason=stop event to the engine's audit file."""
    ts = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    event = {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "symbol": symbol,
        "reason": "stop",
    }
    path = loop._exec.audit_path
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _strong_eth() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_eth_cooldown",
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
async def test_recent_stop_blocks_reentry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RISK_POST_STOP_COOLDOWN_MIN", "180")
    loop = _loop(tmp_path)
    _seed_stop(loop, "ETH/USDT", minutes_ago=10)
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    assert cycle.status == CycleStatus.COOLDOWN_REJECTED
    assert cycle.order_created is False
    assert "post_stop_cooldown" in cycle.notes


@pytest.mark.asyncio
async def test_disabled_window_does_not_block_on_cooldown(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RISK_POST_STOP_COOLDOWN_MIN", "0")
    loop = _loop(tmp_path)
    _seed_stop(loop, "ETH/USDT", minutes_ago=10)
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    # Cooldown disabled: whatever the outcome, it is NOT a cooldown rejection.
    assert cycle.status != CycleStatus.COOLDOWN_REJECTED
    assert "post_stop_cooldown" not in cycle.notes


@pytest.mark.asyncio
async def test_old_stop_outside_window_does_not_block(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RISK_POST_STOP_COOLDOWN_MIN", "180")
    loop = _loop(tmp_path)
    _seed_stop(loop, "ETH/USDT", minutes_ago=300)  # outside 3h window
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    assert cycle.status != CycleStatus.COOLDOWN_REJECTED
    assert "post_stop_cooldown" not in cycle.notes
