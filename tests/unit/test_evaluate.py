"""Hypothesis-search orchestration tests.

Covers the one survival path and the three distinct non-survival modes:
negative edge, too-few-trades, and bucket-inconsistency (a lucky window).
"""

from __future__ import annotations

from app.analysis.features.feature_matrix import FeatureRow
from app.research.evaluate import search_hypotheses


def _rows(n: int) -> list[FeatureRow]:
    return [
        FeatureRow(
            timestamp_utc=f"t{i:04d}",
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
        )
        for i in range(n)
    ]


def _always_long(_row: FeatureRow) -> int:
    return 1


def _always_short(_row: FeatureRow) -> int:
    return -1


def test_strong_consistent_positive_hypothesis_survives() -> None:
    rows = _rows(40)
    labels: list[float | None] = [100.0] * 40
    report = search_hypotheses(
        [("long", _always_long)], rows, labels, round_trip_cost_bps=20.0, min_trades=30
    )
    assert report.n_survivors == 1
    v = report.verdicts[0]
    assert v.survives
    assert v.result.summary.mean_bps == 80.0  # 100 gross - 20 cost
    assert v.result.n_buckets_positive == v.result.n_buckets


def test_negative_hypothesis_does_not_survive() -> None:
    rows = _rows(40)
    labels: list[float | None] = [100.0] * 40
    report = search_hypotheses(
        [("short", _always_short)], rows, labels, round_trip_cost_bps=20.0, min_trades=30
    )
    assert report.n_survivors == 0
    assert not report.verdicts[0].survives


def test_search_isolates_real_edge_from_bad_one() -> None:
    rows = _rows(40)
    labels: list[float | None] = [100.0] * 40
    report = search_hypotheses(
        [("long", _always_long), ("short", _always_short)],
        rows,
        labels,
        round_trip_cost_bps=20.0,
        min_trades=30,
    )
    assert report.n_survivors == 1
    by_name = {v.name: v.survives for v in report.verdicts}
    assert by_name == {"long": True, "short": False}


def test_too_few_trades_blocks_survival() -> None:
    rows = _rows(10)
    labels: list[float | None] = [100.0] * 10  # constant positive, but thin
    report = search_hypotheses(
        [("long", _always_long)], rows, labels, round_trip_cost_bps=0.0, min_trades=30
    )
    assert report.n_survivors == 0
    assert not report.verdicts[0].survives


def test_lucky_window_blocked_by_bucket_consistency() -> None:
    rows = _rows(40)
    # Positive overall (mean +192) but the edge lives in ONE bucket only.
    labels: list[float | None] = [1000.0] * 8 + [-10.0] * 32
    report = search_hypotheses(
        [("long", _always_long)],
        rows,
        labels,
        round_trip_cost_bps=0.0,
        min_trades=30,
        n_buckets=5,
    )
    v = report.verdicts[0]
    assert v.result.summary.mean_bps > 0  # positive overall
    assert v.result.n_buckets_positive == 1  # concentrated in one window
    assert not v.survives  # consistency gate rejects the lucky window


def test_empty_hypothesis_set_is_empty_report() -> None:
    report = search_hypotheses([], _rows(40), [100.0] * 40, round_trip_cost_bps=0.0)
    assert report.verdicts == []
    assert report.n_hypotheses == 0
    assert report.n_survivors == 0
