"""Integration test: Sprint E churn-killer gate wired into the trading loop.

Verifies the loop-level contract end-to-end:
- a symbol over its per-symbol entries/hour cap is rejected with
  CycleStatus.CHURN_REJECTED (rate/turnover) and no order is created;
- a recent risk-reducing close (take, not only stop) rejects with
  COOLDOWN_REJECTED via the churn cooldown;
- PROBE entry_mode applies the tighter probe entries/hour cap;
- HARD INVARIANT: exits (monitor_positions / close_position) are NEVER touched
  by the churn-killer — even with all churn limits set to block everything, an
  open position still stops out. This is the de-risking guarantee.
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


def _append(loop: TradingLoop, event: dict) -> None:
    with loop._exec.audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _entry_fill(symbol: str, minutes_ago: int) -> dict:
    ts = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "symbol": symbol,
        "side": "buy",
        "position_side": "long",
        "fill_price": 100.0,
        "filled_quantity": 1.0,
        "quantity": 1.0,
    }


def _close(symbol: str, minutes_ago: int, *, reason: str, pnl: float) -> dict:
    ts = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "symbol": symbol,
        "reason": reason,
        "trade_pnl_usd": pnl,
    }


def _strong_eth() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_eth_churn",
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


def _disable_post_stop(monkeypatch) -> None:
    """Isolate the churn-killer from the legacy base cooldown."""
    monkeypatch.setenv("RISK_POST_STOP_COOLDOWN_MIN", "0")


@pytest.mark.asyncio
async def test_rate_limit_blocks_entry_with_churn_rejected(tmp_path, monkeypatch) -> None:
    _disable_post_stop(monkeypatch)
    monkeypatch.setenv("RISK_CHURN_COOLDOWN_MIN", "0")
    monkeypatch.setenv("RISK_CHURN_MAX_TRADES_PER_SYMBOL_PER_HOUR", "2")
    loop = _loop(tmp_path)
    _append(loop, _entry_fill("ETH/USDT", 30))
    _append(loop, _entry_fill("ETH/USDT", 10))
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    assert cycle.status == CycleStatus.CHURN_REJECTED
    assert cycle.order_created is False
    assert any("trades_per_hour" in n for n in cycle.notes)


@pytest.mark.asyncio
async def test_recent_take_close_blocks_via_churn_cooldown(tmp_path, monkeypatch) -> None:
    """A `take` close (not a stop) must now start a cooldown — the legacy base
    gate only reacted to stops."""
    _disable_post_stop(monkeypatch)
    monkeypatch.setenv("RISK_CHURN_COOLDOWN_MIN", "60")
    monkeypatch.setenv("RISK_CHURN_MAX_TRADES_PER_SYMBOL_PER_HOUR", "0")
    monkeypatch.setenv("RISK_CHURN_MAX_NOTIONAL_TURNOVER_PER_HOUR", "0")
    loop = _loop(tmp_path)
    _append(loop, _close("ETH/USDT", 10, reason="take", pnl=8.0))
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    assert cycle.status == CycleStatus.COOLDOWN_REJECTED
    assert any("churn:post_stop_cooldown" in n for n in cycle.notes)


@pytest.mark.asyncio
async def test_probe_mode_applies_tighter_cap(tmp_path, monkeypatch) -> None:
    _disable_post_stop(monkeypatch)
    monkeypatch.setenv("RISK_CHURN_COOLDOWN_MIN", "0")
    monkeypatch.setenv("RISK_CHURN_MAX_TRADES_PER_SYMBOL_PER_HOUR", "5")
    monkeypatch.setenv("RISK_CHURN_PROBE_TRADES_PER_HOUR", "1")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "probe")
    loop = _loop(tmp_path)
    _append(loop, _entry_fill("ETH/USDT", 10))  # 1 entry, normal cap 5 would allow
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    # PROBE cap of 1 is hit -> blocked, even though the normal cap (5) would pass.
    assert cycle.status == CycleStatus.CHURN_REJECTED


@pytest.mark.asyncio
async def test_all_disabled_does_not_churn_block(tmp_path, monkeypatch) -> None:
    _disable_post_stop(monkeypatch)
    monkeypatch.setenv("RISK_CHURN_COOLDOWN_MIN", "0")
    monkeypatch.setenv("RISK_CHURN_MAX_TRADES_PER_SYMBOL_PER_HOUR", "0")
    monkeypatch.setenv("RISK_CHURN_MAX_NOTIONAL_TURNOVER_PER_HOUR", "0")
    loop = _loop(tmp_path)
    _append(loop, _entry_fill("ETH/USDT", 1))
    _append(loop, _entry_fill("ETH/USDT", 2))
    _append(loop, _close("ETH/USDT", 1, reason="stop", pnl=-5.0))
    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")
    assert cycle.status not in (CycleStatus.CHURN_REJECTED, CycleStatus.COOLDOWN_REJECTED)


@pytest.mark.asyncio
async def test_hard_invariant_exits_never_blocked_by_churn(tmp_path, monkeypatch) -> None:
    """HARD INVARIANT (Goal §4): with EVERY churn limit set to block all entries,
    an open position must still stop out. Exits go through monitor_positions /
    close_position, which never call the churn gate."""
    _disable_post_stop(monkeypatch)
    # Maximally aggressive churn config: cooldown huge, caps at 1, turnover tiny.
    monkeypatch.setenv("RISK_CHURN_COOLDOWN_MIN", "10000")
    monkeypatch.setenv("RISK_CHURN_MAX_TRADES_PER_SYMBOL_PER_HOUR", "1")
    monkeypatch.setenv("RISK_CHURN_MAX_NOTIONAL_TURNOVER_PER_HOUR", "0.01")
    loop = _loop(tmp_path)
    engine = loop._exec

    # Open a long position directly via the engine (entry side).
    order = engine.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        idempotency_key="open_eth_inv",
        risk_check_id="test",
        position_side="long",
        stop_loss=90.0,
        take_profit=200.0,
    )
    fill = engine.fill_order(order, current_price=100.0)
    assert fill is not None
    assert "ETH/USDT" in engine.portfolio.positions

    # Price drops below the stop. monitor_positions must close it regardless of
    # any churn limit — the gate lives only in run_cycle (entry path).
    fills = engine.monitor_positions({"ETH/USDT": 80.0})
    assert len(fills) == 1
    assert "ETH/USDT" not in engine.portfolio.positions

    # And a churn-evaluation of the SAME symbol for a NEW entry is indeed blocked
    # — proving the limits are active, so the exit above passed *despite* them.
    verdict = loop._evaluate_churn("ETH/USDT")
    assert verdict.blocked
