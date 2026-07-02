"""Tests for app.research.confound — beta/drift de-confounding controls."""

from __future__ import annotations

from app.analysis.features.feature_matrix import FeatureRow
from app.research.confound import (
    beta_neutral_forward_returns,
    timing_alpha,
    timing_alpha_report,
)


def _row(z: float | None) -> FeatureRow:
    """Minimal FeatureRow carrying only unlock_frac_fwd_z (the timed selector)."""
    return FeatureRow(
        timestamp_utc="t",
        close=100.0,
        log_return=None,
        rsi_14=None,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
        unlock_frac_fwd_z=z,
    )


def _timed(r: FeatureRow) -> bool:
    return r.unlock_frac_fwd_z is not None and r.unlock_frac_fwd_z > 1.0


# --- beta_neutral_forward_returns -------------------------------------------


def test_beta_neutral_de_means_valid_labels() -> None:
    assert beta_neutral_forward_returns([10.0, 20.0, None]) == [-5.0, 5.0, None]


def test_beta_neutral_preserves_none_positions_and_alignment() -> None:
    out = beta_neutral_forward_returns([None, 30.0, None, 10.0])
    assert out[0] is None and out[2] is None
    # mean of [30, 10] = 20 → [10, -10] at the valid positions
    assert out[1] == 10.0 and out[3] == -10.0


def test_beta_neutral_all_none_passthrough() -> None:
    assert beta_neutral_forward_returns([None, None]) == [None, None]


def test_beta_neutral_single_value_becomes_zero() -> None:
    assert beta_neutral_forward_returns([5.0]) == [0.0]


def test_beta_neutral_zero_mean_unchanged() -> None:
    # mean([1000, -1000, 0]) == 0 → identity on the valid entries
    assert beta_neutral_forward_returns([1000.0, -1000.0, 0.0, None]) == [
        1000.0,
        -1000.0,
        0.0,
        None,
    ]


# --- timing_alpha ------------------------------------------------------------


def test_timing_alpha_short_isolates_timing_from_drift() -> None:
    # closes → fwd(h=1) bps = [+1000, -1000, 0, None]; all_fwd mean = 0 (no drift).
    closes = [100.0, 110.0, 99.0, 99.0]
    rows = [_row(None), _row(2.0), _row(None), _row(None)]  # only bar 1 is "timed"
    res = timing_alpha(rows, closes, horizon=1, cost_bps=20.0, timed=_timed, side=-1)
    assert res is not None
    assert res["n_timed"] == 1
    assert res["always_net_bps"] == -20.0  # short net of zero-drift series = -cost
    assert res["timed_net_bps"] == 980.0  # short into the -1000 bar = +1000 - cost
    assert res["timing_alpha_bps"] == 1000.0  # timing beat drift by 1000 bps


def test_timing_alpha_none_when_no_timed_bars() -> None:
    closes = [100.0, 110.0, 99.0, 99.0]
    rows = [_row(None)] * 4
    assert timing_alpha(rows, closes, horizon=1, cost_bps=20.0, timed=_timed, side=-1) is None


def test_timing_alpha_report_skips_symbols_without_timed_bars() -> None:
    closes = [100.0, 110.0, 99.0, 99.0]
    per_symbol = {
        "HOT/USDT": ([_row(None), _row(2.0), _row(None), _row(None)], closes, 0),
        "COLD/USDT": ([_row(None)] * 4, closes, 0),
    }
    out = timing_alpha_report(per_symbol, horizon=1, cost_bps=20.0, timed=_timed, side=-1)
    assert [r["symbol"] for r in out] == ["HOT/USDT"]
    assert out[0]["timing_alpha_bps"] == 1000.0
