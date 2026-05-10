"""Tests für EntryRangeWatcher (Aufgabenpaket 4).

Spec: docs/architecture/signal_to_execution_gap_analysis_20260510.md
Operator-Auftrag (2026-05-10) Aufgabenpaket 4 — Entry-Triggering lösen.

Testkategorien:
    A) Pure entry-condition für jeden entry_type × direction
    B) TickPlausibility-Filter (rolling-window, edge cases)
    C) Stale-data fail-closed
    D) TTL expiry
    E) Watcher state-transitions (HOLD → TRIGGER_ENTRY)
    F) Tick-Korruption-Simulation (1 outlier in 10 ticks)
    G) Operator-Beispiel: BTCUSDT range entry mit time-series
    H) SHORT-Pendant: ETH/USDT inverted
"""

from __future__ import annotations

import pytest

from app.execution.entry_watcher import (
    EntryRangeWatcher,
    EntryWatcherConfig,
    TickPlausibility,
    WatcherDecision,
    evaluate_tick,
    is_entry_condition_met,
)
from app.execution.normalized_signal import (
    NormalizedTradeSignal,
    SignalStatus,
    new_signal,
)


# ── Helper ────────────────────────────────────────────────────────────────────


def _waiting_long_range(**overrides) -> NormalizedTradeSignal:
    """LONG signal in WAITING_FOR_ENTRY status, range entry 65000-65500."""
    base = {
        "correlation_id": "SIG-TGCH-20260510120000-BTCUSDT",
        "source": "telegram_premium_channel",
        "symbol": "BTCUSDT",
        "side": "buy",
        "direction": "long",
        "entry_type": "range",
        "entry_min": 65000.0,
        "entry_max": 65500.0,
        "stop_loss": 64200.0,
        "targets": (66000.0, 67000.0, 68500.0),
        "leverage": 10,
        "risk_allocation_pct": 0.05,
    }
    base.update(overrides)
    s = new_signal(**base)
    s = s.transition_to(SignalStatus.VALIDATED, actor="V", reason="ok")
    s = s.transition_to(SignalStatus.WAITING_FOR_ENTRY, actor="W", reason="awaiting")
    return s


def _waiting_short_limit(**overrides) -> NormalizedTradeSignal:
    """SHORT signal in WAITING_FOR_ENTRY, limit entry 3500."""
    base = {
        "correlation_id": "SIG-TGCH-20260510120000-ETHUSDT",
        "source": "telegram_premium_channel",
        "symbol": "ETHUSDT",
        "side": "sell",
        "direction": "short",
        "entry_type": "limit",
        "entry_value": 3500.0,
        "stop_loss": 3600.0,
        "targets": (3400.0, 3300.0),
        "leverage": 5,
        "risk_allocation_pct": 0.03,
    }
    base.update(overrides)
    s = new_signal(**base)
    s = s.transition_to(SignalStatus.VALIDATED, actor="V", reason="ok")
    s = s.transition_to(SignalStatus.WAITING_FOR_ENTRY, actor="W", reason="awaiting")
    return s


# ─────────────────────────────────────────────────────────────────────────────
# A) Pure entry-condition (8 cases)
# ─────────────────────────────────────────────────────────────────────────────


def test_market_entry_always_hits() -> None:
    s = _waiting_long_range(entry_type="market", entry_min=None, entry_max=None)
    assert is_entry_condition_met(s, current_price=99999.0)
    assert is_entry_condition_met(s, current_price=1.0)


def test_market_entry_zero_price_misses() -> None:
    s = _waiting_long_range(entry_type="market", entry_min=None, entry_max=None)
    assert not is_entry_condition_met(s, current_price=0.0)
    assert not is_entry_condition_met(s, current_price=-1.0)


def test_long_range_entry_inside_range_hits() -> None:
    s = _waiting_long_range()  # 65000-65500
    assert is_entry_condition_met(s, current_price=65000.0)
    assert is_entry_condition_met(s, current_price=65250.0)
    assert is_entry_condition_met(s, current_price=65500.0)


def test_long_range_entry_outside_range_misses() -> None:
    s = _waiting_long_range()
    assert not is_entry_condition_met(s, current_price=64999.99)
    assert not is_entry_condition_met(s, current_price=65500.01)


def test_short_range_entry_inside_range_hits() -> None:
    s = _waiting_long_range(direction="short", side="sell")  # range geometry stays
    assert is_entry_condition_met(s, current_price=65250.0)


def test_long_limit_entry_at_or_below_hits() -> None:
    s = _waiting_long_range(
        entry_type="limit", entry_value=100.0, entry_min=None, entry_max=None
    )
    assert is_entry_condition_met(s, current_price=100.0)
    assert is_entry_condition_met(s, current_price=99.0)
    assert not is_entry_condition_met(s, current_price=100.01)


def test_short_limit_entry_at_or_above_hits() -> None:
    s = _waiting_short_limit()  # entry_value=3500
    assert is_entry_condition_met(s, current_price=3500.0)
    assert is_entry_condition_met(s, current_price=3501.0)
    assert not is_entry_condition_met(s, current_price=3499.99)


def test_long_trigger_at_or_above_hits() -> None:
    s = _waiting_long_range(
        entry_type="trigger",
        entry_value=70000.0,
        entry_min=None,
        entry_max=None,
    )
    assert is_entry_condition_met(s, current_price=70000.0)
    assert is_entry_condition_met(s, current_price=70500.0)
    assert not is_entry_condition_met(s, current_price=69999.99)


def test_short_trigger_at_or_below_hits() -> None:
    s = _waiting_short_limit(
        entry_type="trigger",
        entry_value=3000.0,
    )
    assert is_entry_condition_met(s, current_price=3000.0)
    assert is_entry_condition_met(s, current_price=2999.0)
    assert not is_entry_condition_met(s, current_price=3000.01)


def test_invalid_range_min_zero_misses() -> None:
    s = _waiting_long_range(entry_min=0.0, entry_max=100.0)
    assert not is_entry_condition_met(s, current_price=50.0)


def test_invalid_range_min_greater_max_misses() -> None:
    s = _waiting_long_range(entry_min=200.0, entry_max=100.0)
    assert not is_entry_condition_met(s, current_price=150.0)


# ─────────────────────────────────────────────────────────────────────────────
# B) TickPlausibility (rolling-window)
# ─────────────────────────────────────────────────────────────────────────────


def test_plausibility_bootstrap_always_passes() -> None:
    """First few ticks (window not full) always plausible."""
    p = TickPlausibility(max_deviation_pct=5.0, window_size=5)
    assert p.check(100.0) == (True, None, None)
    p.record(100.0)
    assert p.check(50.0) == (True, None, None)  # 50% jump but bootstrap
    p.record(50.0)


def test_plausibility_filled_window_rejects_outlier() -> None:
    p = TickPlausibility(max_deviation_pct=5.0, window_size=5)
    for price in [100.0, 100.0, 100.0, 100.0, 100.0]:
        p.record(price)
    # Window now full, median=100. Try 150 → 50% deviation, rejected.
    plausible, med, dev = p.check(150.0)
    assert plausible is False
    assert med == 100.0
    assert dev == 50.0


def test_plausibility_filled_window_accepts_within_threshold() -> None:
    p = TickPlausibility(max_deviation_pct=5.0, window_size=5)
    for price in [100.0, 100.0, 100.0, 100.0, 100.0]:
        p.record(price)
    plausible, _med, dev = p.check(104.0)  # 4% deviation < 5%
    assert plausible is True
    assert dev == 4.0


def test_plausibility_zero_or_negative_price_rejected() -> None:
    p = TickPlausibility()
    plausible, _, _ = p.check(0.0)
    assert plausible is False
    plausible, _, _ = p.check(-1.0)
    assert plausible is False


def test_plausibility_record_skips_invalid() -> None:
    p = TickPlausibility(window_size=3)
    p.record(100.0)
    p.record(0.0)  # invalid, should not be recorded
    p.record(-5.0)  # invalid, not recorded
    assert not p.window_filled  # only 1 valid in window


def test_plausibility_invalid_config_raises() -> None:
    with pytest.raises(ValueError, match="window_size"):
        TickPlausibility(window_size=0)
    with pytest.raises(ValueError, match="max_deviation_pct"):
        TickPlausibility(max_deviation_pct=0.0)


# ─────────────────────────────────────────────────────────────────────────────
# C) Stale-data fail-closed
# ─────────────────────────────────────────────────────────────────────────────


def test_stale_data_skip_short_circuit() -> None:
    s = _waiting_long_range()
    p = TickPlausibility()
    cfg = EntryWatcherConfig(market_data_max_staleness_seconds=10.0)
    eval_ = evaluate_tick(
        s,
        current_price=65250.0,
        quote_age_seconds=15.0,  # > 10s threshold
        plausibility=p,
        config=cfg,
    )
    assert eval_.decision == WatcherDecision.SKIP_STALE_DATA
    assert "quote_age_15.0s" in eval_.reason


def test_fresh_data_passes_freshness_gate() -> None:
    s = _waiting_long_range()
    p = TickPlausibility()
    cfg = EntryWatcherConfig(market_data_max_staleness_seconds=30.0)
    eval_ = evaluate_tick(
        s,
        current_price=65250.0,
        quote_age_seconds=2.0,
        plausibility=p,
        config=cfg,
    )
    # First tick → plausible (bootstrap), entry hit (price in range)
    assert eval_.decision == WatcherDecision.TRIGGER_ENTRY


# ─────────────────────────────────────────────────────────────────────────────
# D) TTL expiry
# ─────────────────────────────────────────────────────────────────────────────


def test_ttl_expired_emits_expire_decision() -> None:
    s = _waiting_long_range()
    p = TickPlausibility()
    eval_ = evaluate_tick(
        s,
        current_price=65250.0,
        quote_age_seconds=2.0,
        plausibility=p,
        ttl_expired=True,
    )
    assert eval_.decision == WatcherDecision.EXPIRE_TTL
    assert eval_.reason == "ttl_exceeded"


def test_ttl_expired_takes_precedence_over_stale() -> None:
    """TTL is checked BEFORE staleness — operator priorisiert Expiry."""
    s = _waiting_long_range()
    p = TickPlausibility()
    cfg = EntryWatcherConfig(market_data_max_staleness_seconds=5.0)
    eval_ = evaluate_tick(
        s,
        current_price=65250.0,
        quote_age_seconds=99.0,
        plausibility=p,
        config=cfg,
        ttl_expired=True,
    )
    assert eval_.decision == WatcherDecision.EXPIRE_TTL


# ─────────────────────────────────────────────────────────────────────────────
# E) Signal-Status-Gate (HOLD wenn nicht waiting)
# ─────────────────────────────────────────────────────────────────────────────


def test_non_waiting_signal_holds() -> None:
    s = _waiting_long_range()
    s = s.transition_to(SignalStatus.ENTRY_TRIGGERED, actor="X", reason="x")
    p = TickPlausibility()
    eval_ = evaluate_tick(
        s,
        current_price=65250.0,
        quote_age_seconds=2.0,
        plausibility=p,
    )
    assert eval_.decision == WatcherDecision.HOLD
    assert "signal_not_waiting" in eval_.reason
    assert "ENTRY_TRIGGERED" in eval_.reason


def test_validated_signal_holds_not_yet_waiting() -> None:
    """A signal that hasn't yet been put into WAITING_FOR_ENTRY is HOLD."""
    s = new_signal(
        correlation_id="SIG-X-20260510120000-X",
        source="x",
        symbol="BTCUSDT",
        side="buy",
        direction="long",
        entry_type="limit",
        entry_value=100.0,
        stop_loss=95.0,
        targets=(105.0,),
        leverage=1,
    )
    s = s.transition_to(SignalStatus.VALIDATED, actor="X", reason="x")
    p = TickPlausibility()
    eval_ = evaluate_tick(
        s,
        current_price=99.0,
        quote_age_seconds=2.0,
        plausibility=p,
    )
    assert eval_.decision == WatcherDecision.HOLD


# ─────────────────────────────────────────────────────────────────────────────
# F) Watcher state-transitions
# ─────────────────────────────────────────────────────────────────────────────


def test_watcher_step_triggers_transition_on_hit() -> None:
    s = _waiting_long_range()
    w = EntryRangeWatcher(s)
    eval_, new_s = w.step(current_price=65250.0, quote_age_seconds=2.0)
    assert eval_.decision == WatcherDecision.TRIGGER_ENTRY
    assert new_s.status == SignalStatus.ENTRY_TRIGGERED
    assert new_s.status_history[-1].actor == "EntryRangeWatcher"
    assert "range_hit" in new_s.status_history[-1].reason


def test_watcher_step_hold_keeps_signal_unchanged() -> None:
    s = _waiting_long_range()
    w = EntryRangeWatcher(s)
    eval_, new_s = w.step(current_price=66000.0, quote_age_seconds=2.0)
    assert eval_.decision == WatcherDecision.HOLD
    assert new_s.status == SignalStatus.WAITING_FOR_ENTRY
    assert new_s is s  # idempotent: same instance


def test_watcher_step_ttl_emits_expired() -> None:
    s = _waiting_long_range()
    w = EntryRangeWatcher(s)
    eval_, new_s = w.step(
        current_price=66000.0, quote_age_seconds=2.0, ttl_expired=True
    )
    assert eval_.decision == WatcherDecision.EXPIRE_TTL
    assert new_s.status == SignalStatus.EXPIRED
    assert new_s.is_terminal


def test_watcher_step_after_terminal_holds() -> None:
    """Watcher on a terminal signal is a no-op."""
    s = _waiting_long_range()
    w = EntryRangeWatcher(s)
    # Trigger entry first
    _eval1, _ = w.step(current_price=65250.0, quote_age_seconds=2.0)
    # Subsequent step on triggered signal → HOLD
    eval_, new_s = w.step(current_price=65250.0, quote_age_seconds=2.0)
    assert eval_.decision == WatcherDecision.HOLD
    assert new_s.status == SignalStatus.ENTRY_TRIGGERED


# ─────────────────────────────────────────────────────────────────────────────
# G) Tick-Korruption-Simulation
# ─────────────────────────────────────────────────────────────────────────────


def test_tick_corruption_outlier_in_steady_market_is_rejected() -> None:
    """Stable market at 65300 for 5 ticks. Then a tick at 100000 (corruption)
    must be rejected, NOT trigger entry into the operator range 65000-65500."""
    s = _waiting_long_range()
    w = EntryRangeWatcher(s)
    # 5 ticks well above the entry range (65000-65500) so no entry hits
    for price in [65800.0, 65750.0, 65820.0, 65780.0, 65810.0]:
        eval_, _ = w.step(current_price=price, quote_age_seconds=2.0)
        assert eval_.decision == WatcherDecision.HOLD
    # Window is full now. Outlier comes in: 100000 (factor-of-1.5 jump)
    eval_, new_s = w.step(current_price=100000.0, quote_age_seconds=2.0)
    assert eval_.decision == WatcherDecision.REJECT_TICK_PLAUSIBILITY
    assert new_s.status == SignalStatus.WAITING_FOR_ENTRY  # not poisoned


def test_tick_corruption_does_not_poison_median() -> None:
    """An implausible tick must NOT be recorded into the rolling window."""
    s = _waiting_long_range()
    w = EntryRangeWatcher(s)
    for price in [65800.0, 65800.0, 65800.0, 65800.0, 65800.0]:
        w.step(current_price=price, quote_age_seconds=2.0)
    # Outlier
    w.step(current_price=200000.0, quote_age_seconds=2.0)
    # Next tick at 65250 should still trigger (outlier didn't enter median)
    eval_, new_s = w.step(current_price=65250.0, quote_age_seconds=2.0)
    # 65250 vs median(65800,...,65800)=65800 → deviation ~0.85% ≤ 5% ✓
    assert eval_.decision == WatcherDecision.TRIGGER_ENTRY
    assert new_s.status == SignalStatus.ENTRY_TRIGGERED


# ─────────────────────────────────────────────────────────────────────────────
# H) Operator-Beispiel End-to-End
# ─────────────────────────────────────────────────────────────────────────────


def test_operator_example_btc_long_range_time_series() -> None:
    """Exakter Operator-Auftrag BTCUSDT LONG mit deterministischer Zeitreihe.

    Marktpreis-Sequenz (Sekunden-Ticks):
      66200 (above range)
      66100
      66000  (just above)
      65900
      65800  (window now full, median ~66000)
      65500  (entry_max boundary — HIT!)

    Erwartete Entry-Trigger genau am ersten Tick der in den Range eintritt.
    """
    s = _waiting_long_range()  # range 65000-65500
    w = EntryRangeWatcher(s)
    sequence = [66200.0, 66100.0, 66000.0, 65900.0, 65800.0]
    for price in sequence:
        eval_, _ = w.step(current_price=price, quote_age_seconds=1.0)
        assert eval_.decision == WatcherDecision.HOLD
    # Sechster Tick fällt in Range
    eval_, new_s = w.step(current_price=65500.0, quote_age_seconds=1.0)
    assert eval_.decision == WatcherDecision.TRIGGER_ENTRY
    assert new_s.status == SignalStatus.ENTRY_TRIGGERED
    assert "range_hit" in eval_.reason
    assert "65500" in eval_.reason


def test_operator_example_eth_short_limit_time_series() -> None:
    """SHORT ETH/USDT mit limit entry 3500. Markt steigt von 3450 auf 3505,
    triggert SHORT bei dem ersten Tick >= 3500."""
    s = _waiting_short_limit()  # limit 3500, direction=short
    w = EntryRangeWatcher(s)
    for price in [3450.0, 3460.0, 3470.0, 3480.0, 3490.0]:
        eval_, _ = w.step(current_price=price, quote_age_seconds=1.0)
        assert eval_.decision == WatcherDecision.HOLD
    eval_, new_s = w.step(current_price=3500.0, quote_age_seconds=1.0)
    assert eval_.decision == WatcherDecision.TRIGGER_ENTRY
    assert new_s.status == SignalStatus.ENTRY_TRIGGERED
    assert "limit_hit" in eval_.reason
    assert "dir=short" in eval_.reason


def test_operator_example_with_stale_data_skips_until_fresh() -> None:
    """Stale data blocks trigger even if price would hit. Fresh data triggers."""
    s = _waiting_long_range()  # range 65000-65500
    w = EntryRangeWatcher(s, config=EntryWatcherConfig(
        market_data_max_staleness_seconds=10.0,
    ))
    # Stale tick — even though price hits range, must skip
    eval_, new_s = w.step(current_price=65250.0, quote_age_seconds=99.0)
    assert eval_.decision == WatcherDecision.SKIP_STALE_DATA
    assert new_s.status == SignalStatus.WAITING_FOR_ENTRY  # no transition

    # Fresh tick same price → triggers
    eval_, new_s = w.step(current_price=65250.0, quote_age_seconds=1.0)
    assert eval_.decision == WatcherDecision.TRIGGER_ENTRY
    assert new_s.status == SignalStatus.ENTRY_TRIGGERED
