"""Hypothesis -> net-bps trade-sample construction tests."""

from __future__ import annotations

import pytest

from app.analysis.features.feature_matrix import FeatureRow
from app.research.samples import decisions_to_trades


def _row(ts: str = "t", rsi_14: float | None = None) -> FeatureRow:
    return FeatureRow(
        timestamp_utc=ts,
        close=100.0,
        log_return=None,
        rsi_14=rsi_14,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
    )


def _long_if_oversold(row: FeatureRow) -> int:
    return 1 if (row.rsi_14 is not None and row.rsi_14 < 30) else 0


def test_long_trade_net_is_label_minus_cost() -> None:
    rows = [_row("t0", rsi_14=25.0), _row("t1", rsi_14=50.0)]
    labels: list[float | None] = [200.0, 300.0]
    trades = decisions_to_trades(rows, labels, _long_if_oversold, round_trip_cost_bps=20.0)
    assert len(trades) == 1  # second row is flat
    assert trades[0].timestamp_utc == "t0"
    assert trades[0].side == 1
    assert trades[0].gross_bps == 200.0
    assert trades[0].net_bps == 180.0


def test_short_loses_on_positive_label() -> None:
    rows = [_row("t0")]
    labels: list[float | None] = [200.0]
    trades = decisions_to_trades(rows, labels, lambda _r: -1, round_trip_cost_bps=10.0)
    assert trades[0].gross_bps == -200.0
    assert trades[0].net_bps == -210.0


def test_flat_decisions_produce_no_trades() -> None:
    rows = [_row(), _row()]
    labels: list[float | None] = [100.0, 100.0]
    assert decisions_to_trades(rows, labels, lambda _r: 0, round_trip_cost_bps=0.0) == []


def test_none_label_is_skipped() -> None:
    rows = [_row("t0"), _row("t1")]
    labels: list[float | None] = [None, 100.0]
    trades = decisions_to_trades(rows, labels, lambda _r: 1, round_trip_cost_bps=0.0)
    assert len(trades) == 1
    assert trades[0].timestamp_utc == "t1"
    assert trades[0].net_bps == 100.0


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        decisions_to_trades([_row()], [1.0, 2.0], lambda _r: 1, round_trip_cost_bps=0.0)


def test_negative_cost_raises() -> None:
    with pytest.raises(ValueError):
        decisions_to_trades([_row()], [1.0], lambda _r: 1, round_trip_cost_bps=-1.0)


def test_invalid_side_raises() -> None:
    with pytest.raises(ValueError):
        decisions_to_trades([_row()], [100.0], lambda _r: 2, round_trip_cost_bps=0.0)
