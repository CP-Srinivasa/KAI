"""Tests for the generator edge-measurement instrument (NEO /goal 2026-06-05).

Behaviour, not implementation. The central honesty contract is that
INSUFFICIENT is a distinct state from NO_GO and that no metric is ever
fabricated when its data is missing.
"""

from __future__ import annotations

from app.learning.calibration import OutcomePair
from app.observability.edge_report import ClosedTrade
from app.observability.generator_edge import (
    VERDICT_GO,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_GO,
    EdgeGateConfig,
    build_cohort_profile,
    build_generator_edge_report,
    cohort_cvar_bps,
    compute_ic_by_horizon,
    compute_signal_decay,
    max_drawdown_bps,
    sharpe_ratio,
    sortino_ratio,
)


def _trade(
    symbol: str,
    entry: float,
    exit_: float,
    *,
    ts: str,
    regime: str = "unknown",
    source: str = "gen_a",
    side: str = "long",
) -> ClosedTrade:
    pnl = (exit_ - entry) * (1.0 if side == "long" else -1.0)
    return ClosedTrade(symbol, side, entry, exit_, 1.0, "tp", pnl, 0.0, ts, regime, source)


def _winning_trades(n: int, *, source: str = "gen_a") -> list[ClosedTrade]:
    """n profitable long round-trips, alternating two regimes, distinct hours."""
    trades: list[ClosedTrade] = []
    for i in range(n):
        regime = "bull" if i % 2 == 0 else "bear"
        ts = f"2026-06-0{1 + i % 5}T{i % 24:02d}:00:00+00:00"
        trades.append(
            _trade(source[:1].upper() + "/USDT", 1.0, 1.1, ts=ts, regime=regime, source=source)
        )
    return trades


# ─── self-contained math ──────────────────────────────────────────────────────


def test_sharpe_none_for_short_or_flat_series():
    assert sharpe_ratio([]) is None
    assert sharpe_ratio([5.0]) is None
    assert sharpe_ratio([5.0, 5.0, 5.0]) is None  # zero dispersion
    assert sharpe_ratio([1.0, 2.0, 3.0]) is not None


def test_sortino_none_when_no_downside():
    # all returns above MAR=0 → downside deviation 0 → undefined (not infinite)
    assert sortino_ratio([1.0, 2.0, 3.0]) is None
    mixed = sortino_ratio([3.0, -1.0, 2.0, -2.0])
    assert mixed is not None


def test_max_drawdown_monotonic_up_is_zero():
    assert max_drawdown_bps([10.0, 10.0, 10.0]) == 0.0
    # peak at +30 then drop to +10 → drawdown 20
    assert max_drawdown_bps([30.0, -20.0]) == 20.0
    assert max_drawdown_bps([]) is None


def test_cvar_none_below_min_sample_and_nonnegative():
    assert cohort_cvar_bps([1.0, 2.0]) is None  # n < 5
    cvar = cohort_cvar_bps([10.0, 9.0, 8.0, -50.0, 7.0, 6.0])
    assert cvar is not None and cvar >= 0.0


# ─── IC + decay ───────────────────────────────────────────────────────────────


def test_ic_missing_horizons_are_none_not_zero():
    ic = compute_ic_by_horizon(None)
    assert set(ic.keys()) == {"1m", "5m", "15m", "1h", "4h", "24h"}
    assert all(v is None for v in ic.values())


def test_ic_positive_for_correlated_samples():
    # score perfectly predicts forward return → IC ≈ 1
    pairs = [(float(i), float(i) * 2.0) for i in range(25)]
    ic = compute_ic_by_horizon({"1h": pairs})
    assert ic["1h"] is not None
    assert ic["1h"] > 0.99
    assert ic["4h"] is None  # not supplied → honest None


def test_ic_none_below_min_sample():
    pairs = [(float(i), float(i)) for i in range(5)]
    ic = compute_ic_by_horizon({"1h": pairs}, min_sample=20)
    assert ic["1h"] is None


def test_signal_decay_relative_to_first_positive_baseline():
    ic = {"1m": None, "5m": 0.4, "15m": 0.2, "1h": None, "4h": 0.1, "24h": None}
    decay = compute_signal_decay(ic)
    assert decay["5m"] == 1.0  # baseline
    assert decay["15m"] == 0.5
    assert decay["4h"] == 0.25
    assert decay["1m"] is None


# ─── verdict honesty: INSUFFICIENT ≠ NO_GO ────────────────────────────────────


def test_few_trades_yield_insufficient_even_with_great_edge():
    # 5 huge winners — but below min_resolved → we cannot judge, so INSUFFICIENT
    trades = _winning_trades(5)
    prof = build_cohort_profile(
        "gen_a", "generator", trades, config=EdgeGateConfig(min_resolved=30)
    )
    assert prof.verdict == VERDICT_INSUFFICIENT
    assert prof.resolved_count == 5
    assert any("resolved_count" in r for r in prof.reason_codes)


def test_empty_cohort_fabricates_nothing():
    prof = build_cohort_profile("gen_x", "generator", [])
    assert prof.verdict == VERDICT_INSUFFICIENT
    assert prof.resolved_count == 0
    assert prof.win_rate is None
    assert prof.expected_value_after_costs_bps is None
    assert prof.sharpe is None
    assert prof.cvar_bps is None
    assert all(v is None for v in prof.ic_by_horizon.values())


def test_go_path_when_all_gates_pass():
    trades = _winning_trades(40)
    ic_aligned = {
        "1h": [(float(i), float(i) + 0.5) for i in range(25)],
        "4h": [(float(i), float(i) + 1.0) for i in range(25)],
    }
    # well-calibrated 50/50 pairs → low ECE
    pairs = [
        OutcomePair(decision_id=f"d{i}", predicted_probability=0.5, actual_outcome=i % 2)
        for i in range(40)
    ]
    prof = build_cohort_profile(
        "gen_a",
        "generator",
        trades,
        ic_aligned=ic_aligned,
        outcome_pairs=pairs,
        config=EdgeGateConfig(min_resolved=30),
    )
    assert prof.verdict == VERDICT_GO, prof.reason_codes
    assert prof.resolved_count == 40
    assert prof.expected_value_after_costs_bps is not None
    assert prof.expected_value_after_costs_bps > 0.0
    assert prof.distinct_regimes == 2
    assert prof.brier_score is not None
    assert prof.max_drawdown_bps == 0.0  # all winners, monotonic equity


def test_no_go_when_negative_edge_but_enough_data():
    # 40 losers: enough data to JUDGE, and it fails → NO_GO (not INSUFFICIENT)
    losers = []
    for i in range(40):
        regime = "bull" if i % 2 == 0 else "bear"
        ts = f"2026-06-0{1 + i % 5}T{i % 24:02d}:00:00+00:00"
        losers.append(_trade("L/USDT", 1.0, 0.9, ts=ts, regime=regime))
    prof = build_cohort_profile(
        "gen_a", "generator", losers, config=EdgeGateConfig(min_resolved=30)
    )
    assert prof.verdict == VERDICT_NO_GO
    assert prof.expected_value_after_costs_bps is not None
    assert prof.expected_value_after_costs_bps < 0.0


def test_single_regime_blocks_go():
    # great winners but all one regime → distinct_regimes gate fails → NO_GO
    trades = [
        _trade(
            "S/USDT", 1.0, 1.1, ts=f"2026-06-0{1 + i % 5}T{i % 24:02d}:00:00+00:00", regime="bull"
        )
        for i in range(40)
    ]
    ic_aligned = {
        "1h": [(float(i), float(i)) for i in range(25)],
        "4h": [(float(i), float(i)) for i in range(25)],
    }
    pairs = [
        OutcomePair(decision_id=f"d{i}", predicted_probability=0.5, actual_outcome=i % 2)
        for i in range(40)
    ]
    prof = build_cohort_profile(
        "gen_a",
        "generator",
        trades,
        ic_aligned=ic_aligned,
        outcome_pairs=pairs,
        config=EdgeGateConfig(min_resolved=30),
    )
    assert prof.verdict == VERDICT_NO_GO
    assert any("distinct_regimes" in r for r in prof.reason_codes)


def test_payoff_ratio_none_without_losses():
    trades = _winning_trades(40)
    prof = build_cohort_profile(
        "gen_a", "generator", trades, config=EdgeGateConfig(min_resolved=30)
    )
    # all winners → no avg loss → payoff undefined, not infinite
    assert prof.payoff_ratio is None
    assert prof.win_rate == 1.0


# ─── report-level data sufficiency banner ─────────────────────────────────────


def test_report_flags_insufficient_data_with_note():
    trades = _winning_trades(10, source="gen_a") + _winning_trades(5, source="gen_b")
    report = build_generator_edge_report(trades, config=EdgeGateConfig(min_resolved=30))
    assert report.total_resolved == 15
    assert report.data_sufficiency == "insufficient"
    assert any("instrument" in n for n in report.notes)
    # grouped by signal_source → two cohorts
    keys = {p.cohort_key for p in report.profiles}
    assert keys == {"gen_a", "gen_b"}
    # every cohort verdict is INSUFFICIENT (too few resolved each)
    assert all(p.verdict == VERDICT_INSUFFICIENT for p in report.profiles)


def test_report_groups_by_regime_when_requested():
    trades = _winning_trades(40)
    report = build_generator_edge_report(
        trades, cohort_type="regime", config=EdgeGateConfig(min_resolved=30)
    )
    keys = {p.cohort_key for p in report.profiles}
    assert keys == {"bull", "bear"}


def test_to_dict_is_json_shaped_and_keeps_none():
    prof = build_cohort_profile("gen_x", "generator", [])
    d = prof.to_dict()
    assert d["verdict"] == VERDICT_INSUFFICIENT
    assert d["win_rate"] is None
    assert isinstance(d["ic_by_horizon"], dict)
    assert isinstance(d["reason_codes"], list)
