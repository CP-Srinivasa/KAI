"""Tests for regime threshold classifier + hysteresis."""

from __future__ import annotations

from app.regime.classifier import (
    ATR_Z_BREAKOUT_THRESHOLD,
    ClassifierInputs,
    apply_hysteresis,
    classify_raw,
    classify_with_hysteresis,
)
from app.regime.models import RegimeClass, RegimeSnapshot


def _make_inputs(
    adx: float | None = 35.0,
    plus_di: float | None = 25.0,
    minus_di: float | None = 5.0,
    rv_24h: float | None = 0.02,
    atr_zscore: float | None = 0.5,
    vol_class: str = "vol_normal",
) -> ClassifierInputs:
    return ClassifierInputs(
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
        rv_24h=rv_24h,
        atr_zscore=atr_zscore,
        vol_class=vol_class,  # type: ignore[arg-type]
    )


def _snapshot(
    regime: RegimeClass,
    pending: RegimeClass | None = None,
    pending_n: int = 0,
) -> RegimeSnapshot:
    return RegimeSnapshot(
        asset="BTC",
        timestamp="2026-05-09T13:00:00Z",
        regime=regime,
        vol_class="vol_normal",
        confidence=1.0,
        pending_regime=pending,
        pending_consecutive=pending_n,
    )


# ── classify_raw ────────────────────────────────────────────────────────────


def test_raw_missing_adx_returns_unknown() -> None:
    assert classify_raw(_make_inputs(adx=None)) == RegimeClass.UNKNOWN


def test_raw_missing_plus_di_returns_unknown() -> None:
    assert classify_raw(_make_inputs(plus_di=None)) == RegimeClass.UNKNOWN


def test_raw_strong_uptrend_yields_trend_up() -> None:
    out = classify_raw(_make_inputs(adx=35.0, plus_di=30.0, minus_di=5.0))
    assert out == RegimeClass.TREND_UP


def test_raw_strong_downtrend_yields_trend_down() -> None:
    out = classify_raw(_make_inputs(adx=40.0, plus_di=5.0, minus_di=30.0))
    assert out == RegimeClass.TREND_DOWN


def test_raw_breakout_up_requires_high_atr_z() -> None:
    out = classify_raw(
        _make_inputs(
            adx=27.0,
            plus_di=20.0,
            minus_di=5.0,
            atr_zscore=ATR_Z_BREAKOUT_THRESHOLD + 0.5,
        )
    )
    assert out == RegimeClass.BREAKOUT_UP


def test_raw_breakout_zone_without_atr_anomaly_falls_to_chop() -> None:
    out = classify_raw(
        _make_inputs(
            adx=27.0,
            plus_di=20.0,
            minus_di=5.0,
            atr_zscore=0.0,
            vol_class="vol_normal",
        )
    )
    assert out == RegimeClass.CHOP_VOLATILE


def test_raw_low_adx_low_vol_yields_chop_quiet() -> None:
    out = classify_raw(_make_inputs(adx=15.0, plus_di=10.0, minus_di=12.0, vol_class="vol_low"))
    assert out == RegimeClass.CHOP_QUIET


def test_raw_low_adx_high_vol_yields_chop_volatile() -> None:
    out = classify_raw(_make_inputs(adx=15.0, plus_di=10.0, minus_di=12.0, vol_class="vol_high"))
    assert out == RegimeClass.CHOP_VOLATILE


def test_raw_breakout_down_with_minus_dominant() -> None:
    out = classify_raw(_make_inputs(adx=28.0, plus_di=5.0, minus_di=20.0, atr_zscore=1.5))
    assert out == RegimeClass.BREAKOUT_DOWN


# ── apply_hysteresis ────────────────────────────────────────────────────────


def test_hysteresis_no_previous_commits_immediately() -> None:
    committed, pending, n = apply_hysteresis(RegimeClass.TREND_UP, None)
    assert committed == RegimeClass.TREND_UP
    assert pending is None
    assert n == 0


def test_hysteresis_same_as_previous_clears_pending() -> None:
    prev = _snapshot(RegimeClass.TREND_UP, pending=RegimeClass.CHOP_VOLATILE, pending_n=1)
    committed, pending, n = apply_hysteresis(RegimeClass.TREND_UP, prev)
    assert committed == RegimeClass.TREND_UP
    assert pending is None
    assert n == 0


def test_hysteresis_new_candidate_starts_pending_at_one() -> None:
    prev = _snapshot(RegimeClass.TREND_UP)
    committed, pending, n = apply_hysteresis(RegimeClass.CHOP_QUIET, prev)
    assert committed == RegimeClass.TREND_UP  # not yet committed
    assert pending == RegimeClass.CHOP_QUIET
    assert n == 1


def test_hysteresis_two_consecutive_bars_commits_change() -> None:
    prev = _snapshot(RegimeClass.TREND_UP, pending=RegimeClass.CHOP_QUIET, pending_n=1)
    committed, pending, n = apply_hysteresis(RegimeClass.CHOP_QUIET, prev)
    assert committed == RegimeClass.CHOP_QUIET
    assert pending is None
    assert n == 0


def test_hysteresis_different_candidate_restarts_counter() -> None:
    # prev had CHOP_QUIET pending @ 1; now BREAKOUT_UP appears as candidate.
    prev = _snapshot(RegimeClass.TREND_UP, pending=RegimeClass.CHOP_QUIET, pending_n=1)
    committed, pending, n = apply_hysteresis(RegimeClass.BREAKOUT_UP, prev)
    assert committed == RegimeClass.TREND_UP  # still committed prev
    assert pending == RegimeClass.BREAKOUT_UP
    assert n == 1  # restart, not increment


# ── classify_with_hysteresis (end-to-end) ───────────────────────────────────


def test_full_cycle_first_call_no_previous() -> None:
    inputs = _make_inputs(adx=40.0, plus_di=30.0, minus_di=5.0)
    snap = classify_with_hysteresis("BTC", "2026-05-09T13:00:00Z", inputs, None)
    assert snap.asset == "BTC"
    assert snap.regime == RegimeClass.TREND_UP
    assert snap.pending_regime is None
    assert snap.pending_consecutive == 0
    assert snap.confidence == 1.0
    assert snap.adx == 40.0


def test_full_cycle_flicker_does_not_commit_change() -> None:
    # Bar 1: TREND_UP committed.
    snap1 = classify_with_hysteresis(
        "BTC",
        "2026-05-09T13:00:00Z",
        _make_inputs(adx=35.0, plus_di=30.0, minus_di=5.0),
        None,
    )
    assert snap1.regime == RegimeClass.TREND_UP

    # Bar 2: ADX drops to chop zone — pending starts.
    snap2 = classify_with_hysteresis(
        "BTC",
        "2026-05-09T14:00:00Z",
        _make_inputs(adx=18.0, plus_di=10.0, minus_di=12.0, vol_class="vol_normal"),
        snap1,
    )
    assert snap2.regime == RegimeClass.TREND_UP  # hysteresis blocks change
    assert snap2.pending_regime == RegimeClass.CHOP_VOLATILE
    assert snap2.pending_consecutive == 1

    # Bar 3: ADX bounces back to trend — pending cleared, no commit.
    snap3 = classify_with_hysteresis(
        "BTC",
        "2026-05-09T15:00:00Z",
        _make_inputs(adx=35.0, plus_di=30.0, minus_di=5.0),
        snap2,
    )
    assert snap3.regime == RegimeClass.TREND_UP
    assert snap3.pending_regime is None
    assert snap3.pending_consecutive == 0


def test_full_cycle_two_bars_in_new_class_commits() -> None:
    snap1 = classify_with_hysteresis(
        "BTC",
        "2026-05-09T13:00:00Z",
        _make_inputs(adx=35.0, plus_di=30.0, minus_di=5.0),
        None,
    )
    snap2 = classify_with_hysteresis(
        "BTC",
        "2026-05-09T14:00:00Z",
        _make_inputs(adx=15.0, plus_di=10.0, minus_di=12.0, vol_class="vol_low"),
        snap1,
    )
    snap3 = classify_with_hysteresis(
        "BTC",
        "2026-05-09T15:00:00Z",
        _make_inputs(adx=15.0, plus_di=10.0, minus_di=12.0, vol_class="vol_low"),
        snap2,
    )
    assert snap3.regime == RegimeClass.CHOP_QUIET
    assert snap3.pending_regime is None


# ── snapshot to_json_dict ───────────────────────────────────────────────────


def test_snapshot_to_json_dict_omits_none_indicators() -> None:
    snap = RegimeSnapshot(
        asset="BTC",
        timestamp="2026-05-09T13:00:00Z",
        regime=RegimeClass.UNKNOWN,
        vol_class="vol_normal",
        confidence=1.0,
    )
    d = snap.to_json_dict()
    assert d["asset"] == "BTC"
    assert d["regime"] == "unknown"
    assert "adx" not in d
    assert "pending_regime" not in d


def test_snapshot_to_json_dict_includes_present_indicators_and_pending() -> None:
    snap = RegimeSnapshot(
        asset="ETH",
        timestamp="2026-05-09T14:00:00Z",
        regime=RegimeClass.TREND_UP,
        vol_class="vol_high",
        confidence=1.0,
        adx=42.5,
        plus_di=33.0,
        minus_di=4.0,
        rv_24h=0.05,
        atr_zscore=1.8,
        pending_regime=RegimeClass.CHOP_VOLATILE,
        pending_consecutive=1,
    )
    d = snap.to_json_dict()
    assert d["adx"] == 42.5
    assert d["pending_regime"] == "chop_volatile"
    assert d["pending_consecutive"] == 1
