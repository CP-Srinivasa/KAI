"""Unit tests for the directional-news signal evaluator (cohort scoring + gate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.research.news_signal_eval import evaluate_cohort, evaluate_news, render

_T0 = datetime(2026, 6, 1, tzinfo=UTC)
_H = 3600  # 1h horizon used across these tests


def _outcome(bps: float, *, symbol: str, source: str = "src", i: int = 0) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "source": source,
        "side": "long",
        "entry_ts": _T0 + timedelta(hours=i),
        "fwd": {_H: bps, 14400: bps, 86400: bps, 259200: bps},
    }


def test_cohort_actionable_when_positive_diverse_and_above_cost() -> None:
    # 40 outcomes, +100bps each (>> 20 cost), spread over 4 symbols (25% each).
    syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    outcomes = [_outcome(100.0, symbol=syms[i % 4], i=i) for i in range(40)]
    res = evaluate_cohort(outcomes, cost_bps=20.0)
    h = res["horizons"][_H]
    assert h["n"] == 40
    assert h["mean_bps"] == 100.0
    assert h["p_positive"] is not None and h["p_positive"] > 0.95
    assert h["top_symbol_share"] == 0.25
    assert h["actionable"] is True
    assert res["actionable"] is True
    assert "ACTIONABLE" in res["verdict"]


def test_cohort_not_actionable_when_single_symbol_monoculture() -> None:
    outcomes = [_outcome(100.0, symbol="BTC/USDT", i=i) for i in range(40)]
    res = evaluate_cohort(outcomes, cost_bps=20.0)
    h = res["horizons"][_H]
    assert h["top_symbol_share"] == 1.0
    assert h["actionable"] is False
    assert "SHADOW_ONLY" in res["verdict"]


def test_cohort_not_actionable_when_below_cost() -> None:
    syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    outcomes = [_outcome(5.0, symbol=syms[i % 4], i=i) for i in range(40)]  # 5 < 20 cost
    res = evaluate_cohort(outcomes, cost_bps=20.0)
    assert res["horizons"][_H]["actionable"] is False


def test_cohort_p_none_below_min_sample() -> None:
    outcomes = [_outcome(100.0, symbol="BTC/USDT", i=i) for i in range(3)]  # < MIN_SAMPLE(8)
    res = evaluate_cohort(outcomes, cost_bps=20.0)
    h = res["horizons"][_H]
    assert h["p_positive"] is None
    assert h["actionable"] is False


def test_cohort_missing_horizon_values_counted_as_zero_n() -> None:
    outcomes = [
        {
            "symbol": "BTC/USDT",
            "source": "src",
            "side": "long",
            "entry_ts": _T0,
            "fwd": {_H: None, 14400: None, 86400: None, 259200: None},
        }
    ]
    res = evaluate_cohort(outcomes)
    assert res["horizons"][_H]["n"] == 0
    assert res["horizons"][_H]["actionable"] is False


def test_evaluate_news_splits_by_source() -> None:
    syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    a = [_outcome(100.0, symbol=syms[i % 4], source="alpha", i=i) for i in range(20)]
    b = [_outcome(-50.0, symbol=syms[i % 4], source="beta", i=i) for i in range(20)]
    res = evaluate_news(a + b, cost_bps=20.0)
    assert set(res["per_source"]) == {"alpha", "beta"}
    assert res["per_source"]["alpha"]["horizons"][_H]["mean_bps"] == 100.0
    assert res["per_source"]["beta"]["horizons"][_H]["mean_bps"] == -50.0
    assert res["overall"]["n"] == 40
    assert res["cost_bps"] == 20.0


def test_render_filters_small_sources_and_labels_horizons() -> None:
    syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    big = [_outcome(100.0, symbol=syms[i % 4], source="alpha", i=i) for i in range(25)]
    small = [_outcome(10.0, symbol="BTC/USDT", source="tiny", i=i) for i in range(3)]
    res = evaluate_news(big + small, cost_bps=20.0)
    out = render(res, min_n=20)
    assert "ALL sources" in out
    assert "alpha" in out
    assert "tiny" not in out  # below min_n
    assert "omitted" in out
    assert "1h" in out and "72d" not in out and "3d" in out  # horizon labels


# ── per-symbol cost bar (liquidity tiers) ────────────────────────────────────


def test_cohort_cost_by_symbol_moves_the_gate() -> None:
    syms = ["BTC/USDT", "ETH/USDT"]
    outcomes = [_outcome(30.0, symbol=syms[i % 2], i=i) for i in range(40)]
    # cohort mean per-symbol cost = (10+30)/2 = 20 <= 30 mean -> actionable
    cheap = evaluate_cohort(
        outcomes, cost_bps=20.0, cost_by_symbol={"BTC/USDT": 10.0, "ETH/USDT": 30.0}
    )
    assert cheap["horizons"][_H]["cost_ref_bps"] == 20.0
    assert cheap["horizons"][_H]["actionable"] is True
    # punitive per-symbol costs push the bar above the mean -> not actionable
    dear = evaluate_cohort(
        outcomes, cost_bps=20.0, cost_by_symbol={"BTC/USDT": 40.0, "ETH/USDT": 60.0}
    )
    assert dear["horizons"][_H]["cost_ref_bps"] == 50.0
    assert dear["horizons"][_H]["actionable"] is False


def test_cohort_cost_falls_back_to_flat_for_unmapped_symbols() -> None:
    outcomes = [_outcome(30.0, symbol="DOGE/USDT", i=i) for i in range(10)]
    res = evaluate_cohort(outcomes, cost_bps=25.0, cost_by_symbol={"BTC/USDT": 10.0})
    assert res["horizons"][_H]["cost_ref_bps"] == 25.0


# ── cross-source pooling (IVW fixed-effect) ──────────────────────────────────


def _noisy(bps: float, i: int) -> float:
    return bps + (1.0 if i % 2 == 0 else -1.0)  # variance > 0, mean preserved


def test_pool_sources_combines_consistent_sources() -> None:
    from app.research.news_signal_eval import pool_sources

    by_source = {
        "a": [_outcome(_noisy(10.0, i), symbol="ETH/USDT", source="a", i=i) for i in range(12)],
        "b": [_outcome(_noisy(10.0, i), symbol="SOL/USDT", source="b", i=i) for i in range(12)],
        "tiny": [_outcome(50.0, symbol="XRP/USDT", source="tiny", i=i) for i in range(3)],
    }
    p = pool_sources(by_source, _H)
    assert p is not None
    assert p["k_sources"] == 2  # tiny excluded (< MIN_POOL_N)
    assert p["n_total"] == 24
    assert abs(p["pooled_mean_bps"] - 10.0) < 1.0
    assert p["z"] > 1.96
    assert p["p_positive_normal"] > 0.95
    assert p["i_squared"] < 0.5  # consistent sources -> low heterogeneity


def test_pool_sources_flags_heterogeneity() -> None:
    from app.research.news_signal_eval import pool_sources

    by_source = {
        "up": [_outcome(_noisy(30.0, i), symbol="ETH/USDT", source="up", i=i) for i in range(12)],
        "dn": [_outcome(_noisy(-30.0, i), symbol="SOL/USDT", source="dn", i=i) for i in range(12)],
    }
    p = pool_sources(by_source, _H)
    assert p is not None
    assert p["i_squared"] > 0.9  # opposite-sign sources = massive heterogeneity


def test_pool_sources_none_below_two_qualifying() -> None:
    from app.research.news_signal_eval import pool_sources

    by_source = {
        "only": [
            _outcome(_noisy(10.0, i), symbol="ETH/USDT", source="only", i=i) for i in range(12)
        ]
    }
    assert pool_sources(by_source, _H) is None
    assert pool_sources({}, _H) is None


def test_evaluate_news_includes_pooled_block_and_render_shows_it() -> None:
    outcomes = [
        _outcome(_noisy(10.0, i), symbol="ETH/USDT", source="a", i=i) for i in range(12)
    ] + [_outcome(_noisy(10.0, i), symbol="SOL/USDT", source="b", i=i) for i in range(12)]
    res = evaluate_news(outcomes, cost_bps=20.0)
    assert res["pooled"][_H] is not None
    assert res["pooled"][_H]["k_sources"] == 2
    out = render(res, min_n=10)
    assert "Pooled across sources" in out
