"""Regression tests for honest research-sample construction and disclosure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.primary_confirmatory import SymbolPanel, maturity_counts
from app.research.samples import decisions_to_trades_with_counts


def _row(index: int) -> FeatureRow:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index)
    return FeatureRow(
        timestamp_utc=timestamp.isoformat(),
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


@pytest.mark.parametrize(
    "unavailable_label",
    [None, float("nan"), float("inf"), float("-inf")],
    ids=["none", "nan", "positive-infinity", "negative-infinity"],
)
def test_non_finite_labels_are_data_unavailable_not_valid_samples(
    unavailable_label: float | None,
) -> None:
    trades, counts = decisions_to_trades_with_counts(
        [_row(0)],
        [unavailable_label],
        lambda _row_in: 1,
        round_trip_cost_bps=5.0,
    )

    assert trades == []
    assert counts.raw_fires == 1
    assert counts.label_capable_fires == 0
    assert counts.data_unavailable == 1


def test_zero_is_a_valid_label_not_data_unavailable() -> None:
    trades, counts = decisions_to_trades_with_counts(
        [_row(0)],
        [0.0],
        lambda _row_in: 1,
        round_trip_cost_bps=5.0,
    )

    assert len(trades) == 1
    assert trades[0].gross_bps == 0.0
    assert trades[0].net_bps == -5.0
    assert counts.raw_fires == 1
    assert counts.label_capable_fires == 1
    assert counts.data_unavailable == 0


def test_unavailable_label_without_a_fire_is_not_an_unavailable_fire() -> None:
    trades, counts = decisions_to_trades_with_counts(
        [_row(0)],
        [float("nan")],
        lambda _row_in: 0,
        round_trip_cost_bps=5.0,
    )

    assert trades == []
    assert counts.raw_fires == 0
    assert counts.label_capable_fires == 0
    assert counts.data_unavailable == 0


@pytest.mark.parametrize("boolean_side", [True, False])
def test_boolean_decider_outputs_are_rejected(boolean_side: bool) -> None:
    with pytest.raises(ValueError, match="decider must return integer -1, 0, or 1"):
        decisions_to_trades_with_counts(
            [_row(0)],
            [25.0],
            lambda _row_in: boolean_side,
            round_trip_cost_bps=5.0,
        )


@pytest.mark.parametrize("float_side", [1.0, 0.0, -1.0])
def test_float_decider_outputs_are_rejected(float_side: float) -> None:
    with pytest.raises(ValueError, match="decider must return integer -1, 0, or 1"):
        decisions_to_trades_with_counts(
            [_row(0)],
            [25.0],
            lambda _row_in: cast(int, float_side),
            round_trip_cost_bps=5.0,
        )


@pytest.mark.parametrize(
    "non_finite_cost",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_non_finite_round_trip_cost_is_rejected(non_finite_cost: float) -> None:
    with pytest.raises(ValueError, match="round_trip_cost_bps must be numeric, finite, and >= 0"):
        decisions_to_trades_with_counts(
            [_row(0)],
            [25.0],
            lambda _row_in: 1,
            round_trip_cost_bps=non_finite_cost,
        )


@pytest.mark.parametrize("boolean_cost", [True, False])
def test_boolean_round_trip_cost_is_rejected(boolean_cost: bool) -> None:
    with pytest.raises(ValueError, match="round_trip_cost_bps must be numeric, finite, and >= 0"):
        decisions_to_trades_with_counts(
            [_row(0)],
            [25.0],
            lambda _row_in: 1,
            round_trip_cost_bps=boolean_cost,
        )


def test_non_numeric_round_trip_cost_is_rejected() -> None:
    with pytest.raises(ValueError, match="round_trip_cost_bps must be numeric, finite, and >= 0"):
        decisions_to_trades_with_counts(
            [_row(0)],
            [25.0],
            lambda _row_in: 1,
            round_trip_cost_bps=cast(float, "5.0"),
        )


def test_decider_runs_once_per_row_and_the_same_outcomes_drive_counts_and_trades() -> None:
    rows = [_row(0), _row(1), _row(2)]
    labels: list[float | None] = [100.0, None, 200.0]
    returned_sides = iter([1, 1, 0, 0, 0, 0])
    calls: list[str] = []

    def stateful_decider(row: FeatureRow) -> int:
        calls.append(row.timestamp_utc)
        return next(returned_sides)

    trades, counts = decisions_to_trades_with_counts(
        rows,
        labels,
        stateful_decider,
        round_trip_cost_bps=10.0,
    )

    assert calls == [row.timestamp_utc for row in rows]
    assert [(trade.timestamp_utc, trade.net_bps) for trade in trades] == [
        (rows[0].timestamp_utc, 90.0)
    ]
    assert counts.raw_fires == 2
    assert counts.label_capable_fires == 1
    assert counts.data_unavailable == 1


def test_maturity_counts_exclude_all_non_finite_labels() -> None:
    panel = SymbolPanel(
        symbol="BTC/USDT",
        rows=[_row(index) for index in range(4)],
        labels=[float("nan"), float("inf"), float("-inf"), 25.0],
    )

    counts = maturity_counts(
        [panel],
        lambda _row_in: 1,
        round_trip_cost_bps=5.0,
        timeframe_ms=3_600_000,
        horizon=4,
    )

    assert counts.n_valid == 1
    assert counts.label_capable_fires == 1
    assert counts.raw_fires == 4
    assert counts.data_unavailable_count == 3
    assert counts.symbols_with_valid_signals == 1
