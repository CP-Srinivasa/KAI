"""End-to-end composition test for the edge-discovery engine.

Wires the real modules together — OHLCV -> feature matrix -> forward labels ->
hypothesis search — on deterministic synthetic candles. This catches wiring
mistakes (length alignment, None handling across warm-up, decider/feature
contract) that the per-module unit tests cannot see in isolation. It does NOT
assert a particular edge: synthetic data proves the pipeline runs and produces a
well-formed verdict, not that an edge exists.
"""

from __future__ import annotations

import math

from app.analysis.features.feature_matrix import FeatureRow, build_feature_matrix
from app.analysis.features.forward_returns import compute_forward_return_bps
from app.market_data.models import OHLCV
from app.research.evaluate import search_hypotheses


def _candles(n: int) -> list[OHLCV]:
    closes = [100.0 + 10.0 * math.sin(i / 5.0) + i * 0.05 for i in range(n)]
    return [
        OHLCV(
            symbol="BTC/USDT",
            timestamp_utc=f"bar-{i:04d}",
            timeframe="1h",
            open=c,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]


def _long_if_macd_positive(row: FeatureRow) -> int:
    if row.macd is None:
        return 0
    return 1 if row.macd > 0 else -1


def _long_if_oversold(row: FeatureRow) -> int:
    if row.rsi_14 is None:
        return 0
    return 1 if row.rsi_14 < 30 else 0


def test_full_pipeline_runs_and_produces_well_formed_report() -> None:
    candles = _candles(200)
    rows = build_feature_matrix(candles)
    closes = [c.close for c in candles]
    labels = compute_forward_return_bps(closes, horizon=4)

    # Feature matrix, labels, and rows must align for the search to be valid.
    assert len(rows) == len(labels) == len(candles)

    report = search_hypotheses(
        [("macd_trend", _long_if_macd_positive), ("rsi_oversold", _long_if_oversold)],
        rows,
        labels,
        round_trip_cost_bps=20.0,
        min_trades=5,
    )

    assert report.n_hypotheses == 2
    assert len(report.verdicts) == 2
    assert {v.name for v in report.verdicts} == {"macd_trend", "rsi_oversold"}
    # Every verdict carries a coherent summary; survival is a clean bool.
    for v in report.verdicts:
        assert v.result.summary.n >= 0
        assert isinstance(v.survives, bool)
    assert 0 <= report.n_survivors <= 2
