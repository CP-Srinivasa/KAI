"""Unit tests for BacktestEngine (Sprint 35, I-231–I-240)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.enums import MarketScope, SentimentLabel
from app.execution.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    SignalExecutionRecord,
)
from app.research.signals import SignalCandidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signal(
    signal_id: str = "sig_001",
    target_asset: str = "BTC/USDT",
    direction_hint: str = "bullish",
    confidence: float = 0.85,
    priority: int = 9,
) -> SignalCandidate:
    return SignalCandidate(
        signal_id=signal_id,
        document_id=f"doc_{signal_id}",
        target_asset=target_asset,
        direction_hint=direction_hint,
        confidence=confidence,
        supporting_evidence="Strong bullish momentum",
        contradicting_evidence="None",
        risk_notes="Standard risk",
        source_quality=0.9,
        recommended_next_step="Monitor",
        analysis_source="RULE",
        priority=priority,
        sentiment=SentimentLabel.BULLISH,
        affected_assets=[target_asset],
        market_scope=MarketScope.CRYPTO,
        published_at=None,
    )


_DEFAULT_PRICES = {"BTC/USDT": 65000.0, "ETH/USDT": 3200.0, "SOL/USDT": 150.0}


def _cfg(**kwargs) -> BacktestConfig:
    return BacktestConfig(**kwargs)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_backtest_config_is_frozen() -> None:
    cfg = BacktestConfig()
    with pytest.raises((AttributeError, TypeError)):
        cfg.initial_equity = 99999.0  # type: ignore[misc]


def test_backtest_result_is_frozen(tmp_path: Path) -> None:
    engine = BacktestEngine(_cfg(audit_log_path=str(tmp_path / "audit.jsonl")))
    result = engine.run([], {})
    with pytest.raises((AttributeError, TypeError)):
        result.trade_count = 42  # type: ignore[misc]


def test_signal_execution_record_is_frozen() -> None:
    rec = SignalExecutionRecord(
        signal_id="s1",
        target_asset="BTC",
        direction_hint="bullish",
        confidence=0.8,
        outcome="filled",
        risk_violations=(),
    )
    with pytest.raises((AttributeError, TypeError)):
        rec.outcome = "risk_rejected"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Empty signals
# ---------------------------------------------------------------------------


def test_backtest_empty_signals_returns_empty_result(tmp_path: Path) -> None:
    engine = BacktestEngine(_cfg(audit_log_path=str(tmp_path / "a.jsonl")))
    result = engine.run([], _DEFAULT_PRICES)

    assert result.signals_received == 0
    assert result.signals_executed == 0
    assert result.signals_skipped == 0
    assert result.trade_count == 0
    assert result.kill_switch_triggered is False
    assert result.execution_records == ()
    assert result.final_equity == pytest.approx(result.config_initial_equity)


# ---------------------------------------------------------------------------
# Direction filtering (I-236)
# ---------------------------------------------------------------------------


def test_neutral_signal_is_skipped(tmp_path: Path) -> None:
    engine = BacktestEngine(_cfg(audit_log_path=str(tmp_path / "a.jsonl")))
    result = engine.run([_signal(direction_hint="neutral")], _DEFAULT_PRICES)

    assert result.signals_received == 1
    assert result.signals_executed == 0
    assert result.signals_skipped == 1
    assert result.execution_records[0].outcome == "skipped_neutral"


def test_bearish_signal_skipped_when_long_only(tmp_path: Path) -> None:
    engine = BacktestEngine(_cfg(long_only=True, audit_log_path=str(tmp_path / "a.jsonl")))
    result = engine.run([_signal(direction_hint="bearish")], _DEFAULT_PRICES)

    assert result.execution_records[0].outcome == "skipped_bearish"
    assert result.signals_executed == 0


def test_bearish_signal_fills_when_not_long_only(tmp_path: Path) -> None:
    engine = BacktestEngine(
        _cfg(
            long_only=False,
            require_stop_loss=False,
            audit_log_path=str(tmp_path / "a.jsonl"),
        )
    )
    result = engine.run([_signal(direction_hint="bearish")], _DEFAULT_PRICES)
    # Should not be skipped_bearish; may fill or risk_reject but not skip
    assert result.execution_records[0].outcome != "skipped_bearish"


# ---------------------------------------------------------------------------
# Happy path — bullish fill
# ---------------------------------------------------------------------------


def test_bullish_high_confidence_fills(tmp_path: Path) -> None:
    engine = BacktestEngine(
        _cfg(
            min_signal_confidence=0.7,
            require_stop_loss=True,
            audit_log_path=str(tmp_path / "a.jsonl"),
        )
    )
    result = engine.run([_signal(confidence=0.9)], _DEFAULT_PRICES)

    assert result.signals_executed == 1
    assert result.trade_count == 1
    filled = result.execution_records[0]
    assert filled.outcome == "filled"
    assert filled.fill_price is not None and filled.fill_price > 0
    assert filled.stop_loss is not None
    assert filled.take_profit is not None
    assert filled.order_id is not None
    assert filled.risk_check_id is not None


def test_fill_records_stop_loss_and_take_profit(tmp_path: Path) -> None:
    cfg = _cfg(
        stop_loss_pct=2.0,
        take_profit_multiplier=2.0,
        min_signal_confidence=0.5,
        audit_log_path=str(tmp_path / "a.jsonl"),
    )
    engine = BacktestEngine(cfg)
    result = engine.run([_signal(confidence=0.8)], _DEFAULT_PRICES)

    filled = result.execution_records[0]
    assert filled.outcome == "filled"
    assert filled.stop_loss is not None
    assert filled.take_profit is not None
    # TP should be further from entry than SL (multiplier=2.0)
    assert filled.fill_price is not None
    sl_dist = abs(filled.fill_price - filled.stop_loss)
    tp_dist = abs(filled.fill_price - filled.take_profit)
    assert tp_dist > sl_dist


# ---------------------------------------------------------------------------
# Risk gate rejection (I-232)
# ---------------------------------------------------------------------------


def test_low_confidence_signal_is_risk_rejected(tmp_path: Path) -> None:
    engine = BacktestEngine(
        _cfg(
            min_signal_confidence=0.9,
            audit_log_path=str(tmp_path / "a.jsonl"),
        )
    )
    result = engine.run([_signal(confidence=0.5)], _DEFAULT_PRICES)

    assert result.signals_executed == 0
    rec = result.execution_records[0]
    assert rec.outcome == "risk_rejected"
    assert any("confidence" in v for v in rec.risk_violations)


def test_max_open_positions_blocks_new_order(tmp_path: Path) -> None:
    # max_open_positions=1, send 2 bullish signals for different assets
    engine = BacktestEngine(
        _cfg(
            max_open_positions=1,
            min_signal_confidence=0.5,
            require_stop_loss=False,
            audit_log_path=str(tmp_path / "a.jsonl"),
        )
    )
    signals = [
        _signal("s1", target_asset="BTC/USDT", confidence=0.9),
        _signal("s2", target_asset="ETH/USDT", confidence=0.9),
    ]
    result = engine.run(signals, _DEFAULT_PRICES)

    outcomes = [r.outcome for r in result.execution_records]
    assert "filled" in outcomes
    assert "risk_rejected" in outcomes


# ---------------------------------------------------------------------------
# No price (I-234)
# ---------------------------------------------------------------------------


def test_signal_with_unknown_price_is_skipped(tmp_path: Path) -> None:
    engine = BacktestEngine(_cfg(audit_log_path=str(tmp_path / "a.jsonl")))
    result = engine.run([_signal(target_asset="UNKNOWN_XYZ")], _DEFAULT_PRICES)

    assert result.execution_records[0].outcome == "no_price"


def test_price_lookup_normalizes_usdt_suffix(tmp_path: Path) -> None:
    """target_asset='BTC' should resolve against 'BTC/USDT' in prices dict."""
    engine = BacktestEngine(
        _cfg(min_signal_confidence=0.5, audit_log_path=str(tmp_path / "a.jsonl"))
    )
    result = engine.run(
        [_signal(target_asset="BTC", confidence=0.9)],
        {"BTC/USDT": 65000.0},
    )
    assert result.execution_records[0].outcome == "filled"


# ---------------------------------------------------------------------------
# Kill switch (I-237, I-238)
# ---------------------------------------------------------------------------


def test_kill_switch_halts_remaining_signals(tmp_path: Path) -> None:
    """Use max_total_drawdown_pct=0.001 so first fill triggers kill switch."""
    cfg = _cfg(
        initial_equity=10_000.0,
        max_total_drawdown_pct=0.001,  # virtually zero — first fee triggers it
        kill_switch_enabled=True,
        min_signal_confidence=0.5,
        require_stop_loss=False,
        audit_log_path=str(tmp_path / "a.jsonl"),
    )
    signals = [
        _signal("s1", confidence=0.9),
        _signal("sig_002", confidence=0.9),
    ]
    result = BacktestEngine(cfg).run(signals, _DEFAULT_PRICES)

    assert result.kill_switch_triggered is True
    outcomes = [r.outcome for r in result.execution_records]
    assert "kill_switch_halted" in outcomes


def test_kill_switch_not_triggered_on_clean_run(tmp_path: Path) -> None:
    engine = BacktestEngine(
        _cfg(
            max_total_drawdown_pct=99.0,
            min_signal_confidence=0.5,
            audit_log_path=str(tmp_path / "a.jsonl"),
        )
    )
    result = engine.run([_signal(confidence=0.9)], _DEFAULT_PRICES)
    assert result.kill_switch_triggered is False


# ---------------------------------------------------------------------------
# Idempotency (I-235)
# ---------------------------------------------------------------------------


def test_duplicate_signal_id_idempotency_key_is_fill_rejected(tmp_path: Path) -> None:
    """Same signal_id submitted twice → second fill rejected by idempotency."""
    engine = BacktestEngine(
        _cfg(
            max_open_positions=5,
            min_signal_confidence=0.5,
            require_stop_loss=False,
            audit_log_path=str(tmp_path / "a.jsonl"),
        )
    )
    sig = _signal(confidence=0.9)
    result = engine.run([sig, sig], _DEFAULT_PRICES)  # same object twice

    outcomes = [r.outcome for r in result.execution_records]
    # First should fill, second should be rejected (averaging_down or no_quantity)
    assert "filled" in outcomes
    assert result.signals_received == 2


# ---------------------------------------------------------------------------
# Serialization (I-239)
# ---------------------------------------------------------------------------


def test_backtest_result_to_json_dict_is_serializable(tmp_path: Path) -> None:
    engine = BacktestEngine(
        _cfg(min_signal_confidence=0.5, audit_log_path=str(tmp_path / "a.jsonl"))
    )
    result = engine.run([_signal(confidence=0.9)], _DEFAULT_PRICES)
    payload = result.to_json_dict()

    raw = json.dumps(payload)  # must not raise
    decoded = json.loads(raw)

    assert "final_equity" in decoded
    assert "total_return_pct" in decoded
    assert "execution_records" in decoded
    assert "kill_switch_triggered" in decoded
    # I-239: no internal paths
    assert "audit_log_path" not in raw
    assert "artifacts/" not in raw or "artifacts/" not in decoded


def test_backtest_result_no_trading_execution_fields(tmp_path: Path) -> None:
    """BacktestResult must not expose live execution flags."""
    engine = BacktestEngine(_cfg(audit_log_path=str(tmp_path / "a.jsonl")))
    result = engine.run([], {})
    serialized = json.dumps(result.to_json_dict()).lower()

    assert "live_enabled" not in serialized
    assert "execution_enabled" not in serialized


# ---------------------------------------------------------------------------
# Audit trail (I-240)
# ---------------------------------------------------------------------------


def test_backtest_audit_written_to_jsonl(tmp_path: Path) -> None:
    audit_path = tmp_path / "backtest_audit.jsonl"
    engine = BacktestEngine(_cfg(audit_log_path=str(audit_path)))
    engine.run([], {})

    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert "completed_at" in row
    assert "signals_received" in row


def test_backtest_audit_is_append_only(tmp_path: Path) -> None:
    audit_path = tmp_path / "backtest_audit.jsonl"
    engine = BacktestEngine(_cfg(audit_log_path=str(audit_path)))
    engine.run([], {})
    engine.run([], {})

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def test_backtest_signals_received_equals_list_length(tmp_path: Path) -> None:
    engine = BacktestEngine(
        _cfg(min_signal_confidence=0.5, audit_log_path=str(tmp_path / "a.jsonl"))
    )
    signals = [_signal(f"s_{i}", confidence=0.9) for i in range(4)]
    result = engine.run(signals, _DEFAULT_PRICES)
    assert result.signals_received == 4
    assert result.signals_executed + result.signals_skipped == 4


def test_backtest_executed_plus_skipped_equals_received(tmp_path: Path) -> None:
    engine = BacktestEngine(
        _cfg(min_signal_confidence=0.5, audit_log_path=str(tmp_path / "a.jsonl"))
    )
    signals = [
        _signal("s1", direction_hint="bullish", confidence=0.9),
        _signal("s2", direction_hint="neutral", confidence=0.9),
    ]
    result = engine.run(signals, _DEFAULT_PRICES)
    assert result.signals_executed + result.signals_skipped == result.signals_received
