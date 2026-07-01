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
