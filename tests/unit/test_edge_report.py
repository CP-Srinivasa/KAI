"""Sprint C (2026-06-01): cohort- and forward-edge diagnostics.

Behaviour under test (kai-testing-regeln — behaviour, not implementation):

- side-adjusted return math is correct for LONG and SHORT with known numbers.
- net_bps subtracts exactly the CostModel round-trip cost (single source).
- P(mu_net > 0) is high on a clearly-positive sample, low on a clearly-negative
  one, and None below the minimum sample size (honest insufficiency).
- closed realised PnL and open mark-to-market are NEVER combined.
- open/closed fee separation is preserved (the +433-vs--283 bug stays dead).
- forward-return gaps are reported as 0/N with a reason, never fabricated.
- normal / edge / error cases for parsing and aggregation.
"""

from __future__ import annotations

import json

import pytest

from app.execution import fees
from app.execution.cost_model import CostModel
from app.observability.edge_report import (
    ClosedTrade,
    OpenPosition,
    aggregate_cohort,
    bootstrap_p_mean_positive,
    build_edge_report,
    build_forward_coverage,
    build_report_from_audit,
    compute_churn,
    compute_trade_edge,
    extract_entry_times,
    load_forward_samples_from_shadow_resolved,
    mark_to_market_open,
    parse_closed_trades,
    parse_closed_trades_with_exclusions,
    render_report,
    side_adjusted_return_bps,
)
from app.observability.shadow_drift import FeatureVariance, ShadowDriftReport


@pytest.fixture(autouse=True)
def _clear_cache():
    fees.reset_cache()
    yield
    fees.reset_cache()


# --- side-adjusted return math (known numbers) --------------------------------


def test_side_adjusted_long_positive():
    # +1% price move on a long -> +100 bps.
    assert side_adjusted_return_bps(100.0, 101.0, "long") == pytest.approx(100.0)


def test_side_adjusted_long_negative():
    assert side_adjusted_return_bps(100.0, 99.0, "long") == pytest.approx(-100.0)


def test_side_adjusted_short_is_negated():
    # price falls 1% on a short -> profit +100 bps.
    assert side_adjusted_return_bps(100.0, 99.0, "short") == pytest.approx(100.0)
    # price rises 1% on a short -> loss -100 bps.
    assert side_adjusted_return_bps(100.0, 101.0, "short") == pytest.approx(-100.0)


def test_side_adjusted_rejects_nonpositive_price():
    with pytest.raises(ValueError):
        side_adjusted_return_bps(0.0, 100.0, "long")
    with pytest.raises(ValueError):
        side_adjusted_return_bps(100.0, -5.0, "long")


# --- net_bps = gross minus the SAME CostModel cost ----------------------------


def test_net_bps_subtracts_round_trip_cost():
    cm = CostModel()
    # paper venue = 10 bp/side -> 20 bp round-trip, spread/slippage 0.
    trade = ClosedTrade(
        symbol="BTC/USDT",
        position_side="long",
        entry_price=100.0,
        exit_price=101.0,
        quantity=1.0,
        reason="tp",
        trade_pnl_usd=0.8,
        fee_usd=0.2,
        timestamp_utc="2026-06-01T10:00:00+00:00",
    )
    edge = compute_trade_edge(trade, cm, venue="paper")
    assert edge.gross_bps == pytest.approx(100.0)
    assert edge.fee_bps == pytest.approx(20.0)  # 10 entry + 10 exit
    # net = 100 - 20 - 0 - 0 - 0 = 80
    assert edge.net_bps == pytest.approx(80.0)


def test_net_bps_can_be_negative_when_gross_below_cost():
    cm = CostModel()
    trade = ClosedTrade(
        symbol="X/USDT",
        position_side="long",
        entry_price=100.0,
        exit_price=100.1,
        quantity=1.0,  # +10 bps gross
        reason="tp",
        trade_pnl_usd=0.0,
        fee_usd=0.0,
        timestamp_utc="2026-06-01T10:00:00+00:00",
    )
    edge = compute_trade_edge(trade, cm, venue="paper")
    assert edge.gross_bps == pytest.approx(10.0)
    assert edge.net_bps == pytest.approx(-10.0)  # 10 - 20 round-trip cost


# --- P(mu_net > 0): positive, negative, insufficient --------------------------


def test_p_mu_positive_on_clearly_positive_sample():
    vals = [50.0, 60.0, 45.0, 70.0, 55.0, 65.0, 80.0, 40.0, 75.0, 52.0]
    p = bootstrap_p_mean_positive(vals, n_resamples=2000)
    assert p is not None
    assert p > 0.95


def test_p_mu_low_on_clearly_negative_sample():
    vals = [-50.0, -60.0, -45.0, -70.0, -55.0, -65.0, -80.0, -40.0, -75.0, -52.0]
    p = bootstrap_p_mean_positive(vals, n_resamples=2000)
    assert p is not None
    assert p < 0.05


def test_p_mu_none_below_min_sample():
    assert bootstrap_p_mean_positive([10.0, 20.0, 30.0], min_sample=8) is None


def test_p_mu_is_deterministic_with_seed():
    vals = [10.0, -5.0, 20.0, -3.0, 8.0, 1.0, -2.0, 4.0, 9.0]
    a = bootstrap_p_mean_positive(vals, n_resamples=1000, seed=42)
    b = bootstrap_p_mean_positive(vals, n_resamples=1000, seed=42)
    assert a == b


# --- closed vs open MTM are never combined ------------------------------------


def test_open_mtm_kept_separate_from_closed():
    closed = [
        ClosedTrade(
            "BTC/USDT", "long", 100.0, 110.0, 1.0, "tp", 9.8, 0.2, "2026-06-01T10:00:00+00:00"
        ),
    ]
    open_pos = [OpenPosition("ETH/USDT", "long", 50.0, 2.0, "2026-06-01T09:00:00+00:00")]
    report = build_edge_report(
        closed,
        open_pos,
        mark_prices={"ETH/USDT": 60.0},
        min_sample=1,
    )
    # closed realised pnl is the closed sum only.
    assert report.overall.realized_pnl_usd_sum == pytest.approx(9.8)
    # open MTM is its own bucket: (60-50)*2 = 20, NOT folded into realised.
    assert report.open_mtm.unrealized_pnl_usd == pytest.approx(20.0)
    assert report.open_mtm.count == 1
    assert report.closed_trade_count == 1


def test_open_mtm_unrealized_none_when_no_price():
    open_pos = [OpenPosition("ZZZ/USDT", "long", 10.0, 1.0, "2026-06-01T09:00:00+00:00")]
    mtm = mark_to_market_open(open_pos, mark_prices={})
    # no price -> None, NOT 0 (0 would falsely imply flat).
    assert mtm.unrealized_pnl_usd is None
    assert mtm.count == 1
    assert mtm.priced == 0


def test_open_mtm_short_direction():
    open_pos = [OpenPosition("S/USDT", "short", 100.0, 1.0, "2026-06-01T09:00:00+00:00")]
    mtm = mark_to_market_open(open_pos, mark_prices={"S/USDT": 90.0})
    # short profits when price falls: (100-90)*1 = 10.
    assert mtm.unrealized_pnl_usd == pytest.approx(10.0)


# --- cohort aggregation -------------------------------------------------------


def test_cohort_winrate_and_means():
    cm = CostModel()
    trades = [
        ClosedTrade(
            "A/USDT", "long", 100.0, 110.0, 1.0, "tp", 9.0, 1.0, "2026-06-01T10:00:00+00:00"
        ),  # +1000 gross -> +980 net
        ClosedTrade(
            "A/USDT", "long", 100.0, 99.0, 1.0, "sl", -1.2, 0.2, "2026-06-01T11:00:00+00:00"
        ),  # -100 gross -> -120 net (loss)
    ]
    edges = [compute_trade_edge(t, cm) for t in trades]
    cohort = aggregate_cohort("A/USDT", "symbol", edges, min_sample=2, bootstrap_n=500)
    assert cohort.count == 2
    assert cohort.winrate == pytest.approx(0.5)
    assert cohort.avg_win_bps == pytest.approx(980.0)
    assert cohort.avg_loss_bps == pytest.approx(-120.0)


def test_cohort_net_per_notional_weights_by_size():
    cm = CostModel()
    # big loser, small winner: per-notional should lean negative.
    trades = [
        ClosedTrade(
            "B/USDT", "long", 100.0, 99.0, 100.0, "sl", -100.0, 1.0, "2026-06-01T10:00:00+00:00"
        ),  # notional 10000, net -120
        ClosedTrade(
            "B/USDT", "long", 100.0, 110.0, 0.1, "tp", 0.9, 0.1, "2026-06-01T11:00:00+00:00"
        ),  # notional 10, net +980
    ]
    edges = [compute_trade_edge(t, cm) for t in trades]
    cohort = aggregate_cohort("B/USDT", "symbol", edges, min_sample=2, bootstrap_n=200)
    # unweighted mean would be +430; notional-weighted must be near the big -120.
    assert cohort.net_bps_per_notional_mean < 0
    assert cohort.net_bps_mean > cohort.net_bps_per_notional_mean


def test_cohort_robust_stats_expose_mean_artefact():
    """Edge-hardening 2026-06-22: a handful of glitch-scale winners can hijack the
    arithmetic MEAN into a phantom positive while the typical trade loses. The
    MEDIAN (outliers cannot move it) and the winsorized mean must expose this."""
    cm = CostModel()
    trades = [
        # 9 flat round-trips: gross 0 -> net ≈ -cost (a small per-trade loss).
        ClosedTrade(
            "M/USDT",
            "long",
            100.0,
            100.0,
            1.0,
            "tp",
            0.0,
            0.0,
            f"2026-06-01T{i:02d}:00:00+00:00",
        )
        for i in range(9)
    ]
    # One plausible-but-extreme survivor (+39% < 40% guard) that the parse-time
    # implausible filter does NOT drop: ~+3880 net bps.
    trades.append(
        ClosedTrade(
            "M/USDT", "long", 100.0, 139.0, 1.0, "tp", 39.0, 0.0, "2026-06-01T23:00:00+00:00"
        )
    )
    edges = [compute_trade_edge(t, cm) for t in trades]
    cohort = aggregate_cohort("M/USDT", "symbol", edges, min_sample=2, bootstrap_n=200)
    # The mean is dragged strongly positive by the single outlier ...
    assert cohort.net_bps_mean > 100.0
    # ... but the MEDIAN trade still loses (the truth about the typical trade) ...
    assert cohort.net_bps_median < 0.0
    # ... and the winsorized mean (clip ±500bps) no longer lets the glitch dominate.
    assert cohort.net_bps_mean_winsorized < cohort.net_bps_mean
    assert cohort.net_bps_mean_winsorized < 100.0
    # Serialised view carries the robust fields too.
    d = cohort.to_dict()
    assert d["net_bps_median"] < 0.0 and d["net_bps_mean"] > 100.0


# --- churn --------------------------------------------------------------------


def test_churn_reentries_per_day():
    trades = [
        ClosedTrade("C/USDT", "long", 1.0, 1.1, 1.0, "tp", 0.0, 0.0, "2026-06-01T10:00:00+00:00"),
        ClosedTrade("C/USDT", "long", 1.0, 1.1, 1.0, "tp", 0.0, 0.0, "2026-06-01T14:00:00+00:00"),
        ClosedTrade("C/USDT", "long", 1.0, 1.1, 1.0, "tp", 0.0, 0.0, "2026-06-02T10:00:00+00:00"),
    ]
    churn = compute_churn(trades)
    assert len(churn) == 1
    c = churn[0]
    assert c.closes == 3
    assert c.distinct_days == 2
    assert c.reentries_per_day == pytest.approx(1.5)
    # no entry times supplied -> honest None, not a guessed hold.
    assert c.mean_hold_minutes is None


def test_churn_hold_minutes_when_entry_times_present():
    trades = [
        ClosedTrade("D/USDT", "long", 1.0, 1.1, 1.0, "tp", 0.0, 0.0, "2026-06-01T10:30:00+00:00"),
    ]
    churn = compute_churn(trades, entry_times={"D/USDT": ["2026-06-01T10:00:00+00:00"]})
    assert churn[0].mean_hold_minutes == pytest.approx(30.0)


# --- forward coverage: honest gaps, no fiction --------------------------------


def test_forward_coverage_reports_zero_when_no_samples():
    trades = [
        ClosedTrade("E/USDT", "long", 1.0, 1.1, 1.0, "tp", 0.0, 0.0, "2026-06-01T10:00:00+00:00")
    ]
    cov = build_forward_coverage(trades, forward_samples=None)
    assert {c.horizon_minutes for c in cov} == {1, 5, 15, 60}
    for c in cov:
        assert c.covered == 0
        assert c.total == 1
        assert c.net_bps_mean is None  # never fabricated
        assert c.reason == "no_historical_minute_bars"


def test_forward_coverage_cost_adjusts_present_samples():
    trades = [
        ClosedTrade("F/USDT", "long", 1.0, 1.1, 1.0, "tp", 0.0, 0.0, "2026-06-01T10:00:00+00:00")
    ]
    # one sampled 5m gross forward return of +50 bps; cost 20 bp -> net +30.
    cov = build_forward_coverage(trades, forward_samples={5: [50.0]})
    cov_5m = next(c for c in cov if c.horizon_minutes == 5)
    assert cov_5m.covered == 1
    assert cov_5m.net_bps_mean == pytest.approx(30.0)
    assert cov_5m.reason == "sampled"


# --- parsing: only realised closes, phantom/partial ignored -------------------


def test_parse_closed_trades_filters_non_close_and_invalid():
    events = [
        {"event_type": "order_filled", "symbol": "X/USDT"},  # not a close
        {
            "event_type": "position_closed",
            "symbol": "G/USDT",
            "position_side": "long",
            "entry_price": 100.0,
            "exit_price": 105.0,
            "quantity": 2.0,
            "reason": "tp",
            "trade_pnl_usd": 9.8,
            "fee_usd": 0.2,
            "timestamp_utc": "2026-06-01T10:00:00+00:00",
        },
        {
            "event_type": "position_closed",
            "symbol": "BAD/USDT",
            "entry_price": 0.0,
            "exit_price": 5.0,
            "quantity": 1.0,
        },  # invalid price -> dropped
        {
            "event_type": "position_partial_closed",
            "symbol": "H/USDT",
            "entry_price": 1.0,
            "exit_price": 1.1,
            "quantity": 1.0,
        },  # partial -> dropped
    ]
    closed = parse_closed_trades(events)
    assert len(closed) == 1
    assert closed[0].symbol == "G/USDT"
    assert closed[0].trade_pnl_usd == pytest.approx(9.8)


def test_parse_reads_regime_when_present_else_unknown():
    events = [
        {
            "event_type": "position_closed",
            "symbol": "R/USDT",
            "entry_price": 1.0,
            "exit_price": 1.1,
            "quantity": 1.0,
            "timestamp_utc": "2026-06-01T10:00:00+00:00",
            "regime": "bull_trend",
        },
        {
            "event_type": "position_closed",
            "symbol": "U/USDT",
            "entry_price": 1.0,
            "exit_price": 1.1,
            "quantity": 1.0,
            "timestamp_utc": "2026-06-01T10:00:00+00:00",
        },
    ]
    closed = parse_closed_trades(events)
    by_symbol = {c.symbol: c.regime for c in closed}
    assert by_symbol["R/USDT"] == "bull_trend"
    assert by_symbol["U/USDT"] == "unknown"


def test_parse_uses_source_name_and_regime_fallbacks_to_reduce_unknowns():
    events = [
        {
            "event_type": "position_closed",
            "symbol": "FB/USDT",
            "entry_price": 100.0,
            "exit_price": 101.0,
            "quantity": 1.0,
            "timestamp_utc": "2026-06-01T10:00:00+00:00",
            "source_name": "cryptobriefing",
            "market_regime": "trend_up",
        }
    ]

    closed = parse_closed_trades(events)

    assert closed[0].signal_source == "cryptobriefing"
    assert closed[0].regime == "trend_up"


def test_extract_entry_times_only_buy_entries():
    events = [
        {
            "event_type": "order_filled",
            "symbol": "K/USDT",
            "side": "buy",
            "pnl_usd": 0.0,
            "filled_at": "2026-06-01T10:00:00+00:00",
        },
        {
            "event_type": "order_filled",
            "symbol": "K/USDT",
            "side": "sell",
            "pnl_usd": 5.0,
            "filled_at": "2026-06-01T11:00:00+00:00",
        },  # exit
        {
            "event_type": "order_filled",
            "symbol": "K/USDT",
            "side": "buy",
            "pnl_usd": 3.0,
            "filled_at": "2026-06-01T12:00:00+00:00",
        },  # short-cover, not entry
    ]
    et = extract_entry_times(events)
    assert et == {"K/USDT": ["2026-06-01T10:00:00+00:00"]}


# --- full report: regime-unknown note, insufficiency note ---------------------


def test_report_marks_regime_unknown_and_insufficient():
    trades = [
        ClosedTrade(
            "Z/USDT", "long", 100.0, 101.0, 1.0, "tp", 0.8, 0.2, "2026-06-01T10:00:00+00:00"
        ),
    ]
    report = build_edge_report(trades, min_sample=8)
    notes = " ".join(report.notes)
    assert "regime cohort = 'unknown'" in notes
    assert "insufficient" in notes
    assert report.overall.p_mu_net_positive is None


def test_report_surfaces_unknown_attribution_as_diagnostic_blockers():
    trades = [
        ClosedTrade(
            "BTC/USDT",
            "long",
            100.0,
            101.0,
            1.0,
            "tp",
            0.8,
            0.2,
            "2026-06-01T10:00:00+00:00",
            regime="unknown",
            signal_source="unknown",
        ),
        ClosedTrade(
            "ETH/USDT",
            "long",
            100.0,
            99.0,
            1.0,
            "sl",
            -1.2,
            0.2,
            "2026-06-01T11:00:00+00:00",
            regime="trend_up",
            signal_source="telegram_premium_channel_approved",
        ),
    ]

    report = build_edge_report(trades, min_sample=2, bootstrap_n=200)
    blockers = {b["code"]: b for b in report.to_dict()["diagnostic_blockers"]}

    assert blockers["source_unknown"]["affected_count"] == 1
    assert blockers["source_unknown"]["share"] == pytest.approx(0.5)
    assert blockers["source_unknown"]["severity"] == "blocker"
    assert blockers["regime_unknown"]["affected_count"] == 1
    assert blockers["forward_return_coverage_gap"]["affected_count"] == 8
    assert "DIAGNOSTIC BLOCKERS" in render_report(report)


def test_report_blocks_edge_learning_on_degenerate_shadow_drift():
    trades = [
        ClosedTrade(
            "BTC/USDT",
            "long",
            100.0,
            101.0,
            1.0,
            "tp",
            0.8,
            0.2,
            "2026-06-01T10:00:00+00:00",
            regime="trend_up",
            signal_source="autonomous_generator",
        )
    ]
    drift = ShadowDriftReport(
        generated_at="2026-06-08T10:00:00+00:00",
        ledger_path="ledger.jsonl",
        window_hours=24.0,
        min_rows=1,
        total_rows=10,
        rows_in_window=10,
        latest_ts_utc="2026-06-08T09:00:00+00:00",
        status="warn",
        reasons=["feature_degenerate:signal_confidence"],
        feature_variance=[
            FeatureVariance(
                field="signal_confidence",
                sample_count=10,
                variance=0.0,
                distinct_count=1,
                is_degenerate=True,
            )
        ],
    )

    report = build_edge_report(
        trades,
        forward_samples={1: [50.0], 5: [50.0], 15: [50.0], 60: [50.0]},
        shadow_drift_report=drift,
        min_sample=1,
    )
    blockers = {b["code"]: b for b in report.to_dict()["diagnostic_blockers"]}

    assert blockers["shadow_feature_degenerate"]["severity"] == "blocker"
    assert blockers["shadow_feature_degenerate"]["affected_count"] == 1
    assert report.to_dict()["shadow_drift_report"]["status"] == "warn"
    assert "shadow feature variance blocker" in " ".join(report.notes)


def test_shadow_resolved_samples_reduce_forward_coverage_gap(tmp_path):
    path = tmp_path / "shadow_candidate_resolved.jsonl"
    rows = [
        {
            "document_id": "s1",
            "fwd_60s_bps": 30.0,
            "fwd_300s_bps": 40.0,
            "fwd_900s_bps": 50.0,
            "fwd_3600s_bps": 60.0,
        },
        {
            "document_id": "s2",
            "fwd_60s_bps": 20.0,
            "fwd_300s_bps": 30.0,
            "fwd_900s_bps": 40.0,
            "fwd_3600s_bps": 50.0,
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    trades = [
        ClosedTrade(
            "BTC/USDT", "long", 100.0, 101.0, 1.0, "tp", 0.8, 0.2, "2026-06-01T10:00:00+00:00"
        ),
        ClosedTrade(
            "ETH/USDT", "long", 100.0, 101.0, 1.0, "tp", 0.8, 0.2, "2026-06-01T11:00:00+00:00"
        ),
    ]

    samples = load_forward_samples_from_shadow_resolved(path)
    report = build_edge_report(trades, forward_samples=samples, min_sample=2)
    blockers = {b["code"] for b in report.to_dict()["diagnostic_blockers"]}

    assert {cov.covered for cov in report.forward_coverage} == {2}
    assert "forward_return_coverage_gap" not in blockers


def test_report_renders_human_table_without_error():
    trades = [
        ClosedTrade(
            "BTC/USDT", "long", 100.0, 101.0, 1.0, "tp", 0.8, 0.2, "2026-06-01T10:00:00+00:00"
        ),
        ClosedTrade(
            "ETH/USDT", "short", 100.0, 99.0, 1.0, "tp", 0.8, 0.2, "2026-06-01T11:00:00+00:00"
        ),
    ]
    report = build_edge_report(trades, min_sample=2, bootstrap_n=200)
    text = render_report(report)
    assert "EDGE REPORT" in text
    assert "P(mu_net > 0)" in text
    assert "FORWARD-RETURN COVERAGE" in text
    assert "MARK-TO-MARKET" in text
    # JSON round-trips.
    d = report.to_dict()
    assert d["closed_trade_count"] == 2
    assert "by_symbol" in d


def test_empty_report_is_safe():
    report = build_edge_report([], [])
    assert report.closed_trade_count == 0
    assert report.overall.count == 0
    assert report.overall.p_mu_net_positive is None
    # rendering empty must not raise.
    render_report(report)


# --- quarantine exclusion (DS-20260529-V1 MATIC stale-exit runaway) -----------
# The all-time aggregate was poisoned by the +8295 bps MATIC phantom. The edge
# report must reuse the SAME app.learning.bayes_quarantine signature the Bayes
# recalc uses and drop those closes from net_bps / P(mu_net>0) / count, while
# keeping legitimate closes and reporting the dropped count honestly.

_MATIC_STALE_EXIT = 0.408545625  # the exact frozen corrupt exit price


def _matic_phantom_close(ts: str) -> dict:
    """One corrupt MATIC close: huge fake +bps against the frozen stale exit."""
    return {
        "event_type": "position_closed",
        "symbol": "MATIC/USDT",
        "position_side": "long",
        "entry_price": 0.10,  # tiny entry vs frozen 0.4085 exit -> ~+300% fake gross
        "exit_price": _MATIC_STALE_EXIT,
        "quantity": 1000.0,
        "reason": "stale_exit",
        "trade_pnl_usd": 300.0,
        "fee_usd": 0.0,
        "timestamp_utc": ts,
    }


def _clean_loss_close(symbol: str, ts: str) -> dict:
    """A legitimate small net-negative close (gross -100 bps < 20 bp cost)."""
    return {
        "event_type": "position_closed",
        "symbol": symbol,
        "position_side": "long",
        "entry_price": 100.0,
        "exit_price": 99.0,
        "quantity": 1.0,
        "reason": "sl",
        "trade_pnl_usd": -1.2,
        "fee_usd": 0.2,
        "timestamp_utc": ts,
    }


def test_quarantined_matic_close_is_excluded_from_parse():
    events = [
        _matic_phantom_close("2026-05-28T17:42:00+00:00"),
        _clean_loss_close("BTC/USDT", "2026-06-01T10:00:00+00:00"),
    ]
    kept, excl = parse_closed_trades_with_exclusions(events)
    # the MATIC phantom is gone; the legit BTC close survives.
    assert [t.symbol for t in kept] == ["BTC/USDT"]
    assert excl.excluded_count == 1
    assert excl.reasons == {"matic_stale_exit_runaway": 1}
    # the back-compat wrapper drops it too.
    assert [t.symbol for t in parse_closed_trades(events)] == ["BTC/USDT"]


def test_quarantine_excluded_from_net_bps_p_and_count_aggregate(tmp_path):
    """The poisoned aggregate must flip: with the phantom the edge looks great;
    after exclusion it is the honest negative distribution."""
    audit = tmp_path / "audit.jsonl"
    lines = []
    # 18 MATIC phantoms (the runaway) + 10 clean negative closes.
    for i in range(18):
        lines.append(json.dumps(_matic_phantom_close(f"2026-05-28T17:{i % 60:02d}:00+00:00")))
    for i in range(10):
        lines.append(
            json.dumps(_clean_loss_close("BTC/USDT", f"2026-06-01T{10 + i:02d}:00:00+00:00"))
        )
    audit.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = build_report_from_audit(str(audit), venue="paper", min_sample=8)

    # count excludes the 18 phantoms -> only the 10 clean closes remain.
    assert report.closed_trade_count == 10
    assert report.overall.count == 10
    assert report.excluded_quarantined.excluded_count == 18
    assert report.excluded_quarantined.reasons == {"matic_stale_exit_runaway": 18}
    # the surviving aggregate is honestly NEGATIVE (no phantom +bps lift).
    assert report.overall.net_bps_mean < 0
    assert report.overall.p_mu_net_positive is not None
    assert report.overall.p_mu_net_positive < 0.05
    # MATIC must not appear in any cohort.
    assert all(c.cohort_key != "MATIC/USDT" for c in report.by_symbol)
    # the exclusion is reported, not swallowed.
    assert report.to_dict()["excluded_quarantined"]["excluded_count"] == 18
    assert any("EXCLUDED 18" in n for n in report.notes)


def test_non_quarantined_close_stays_in():
    """A MATIC close at a DIFFERENT (legitimate) exit price is NOT quarantined."""
    events = [
        {
            "event_type": "position_closed",
            "symbol": "MATIC/USDT",
            "position_side": "long",
            "entry_price": 0.09,
            "exit_price": 0.0989,  # the legit 2026-05-06 close, not the frozen price
            "quantity": 100.0,
            "reason": "tp",
            "trade_pnl_usd": 0.8,
            "fee_usd": 0.1,
            "timestamp_utc": "2026-05-06T10:00:00+00:00",
        },
    ]
    kept, excl = parse_closed_trades_with_exclusions(events)
    assert len(kept) == 1
    assert kept[0].symbol == "MATIC/USDT"
    assert excl.excluded_count == 0


def test_row_without_exit_price_is_never_quarantined():
    """Error case: a close missing exit_price is dropped as INVALID (no price),
    never counted as quarantined (it has no signature to match)."""
    events = [
        {
            "event_type": "position_closed",
            "symbol": "MATIC/USDT",
            "position_side": "long",
            "entry_price": 0.10,
            "quantity": 1000.0,
            # exit_price missing entirely
            "timestamp_utc": "2026-05-28T18:00:00+00:00",
        },
    ]
    kept, excl = parse_closed_trades_with_exclusions(events)
    assert kept == []  # dropped as invalid
    assert excl.excluded_count == 0  # NOT a quarantine drop
