"""Feature-matrix composition tests.

Focus on the three real risks of composing indicators into a per-candle matrix:
  1. NO-LOOKAHEAD: a row at index i must depend only on candles[0..i].
     This is the integrity foundation for any forward-return backtest.
  2. Warm-up alignment (off-by-one): early rows are None, late rows populated.
  3. Correct wiring: each field equals the underlying indicator at that index.

Indicator math itself is verified in the per-indicator test modules; here we
verify composition, alignment, and causality.
"""

from __future__ import annotations

import math

from app.analysis.features.feature_matrix import build_feature_matrix
from app.analysis.indicators.adx import compute_adx_di
from app.analysis.indicators.realized_volatility import (
    compute_log_returns,
    compute_realized_volatility,
)
from app.analysis.indicators.rsi import compute_rsi
from app.market_data.models import OHLCV


def _closes_path(n: int) -> list[float]:
    # Deterministic, non-flat path so indicators are non-degenerate.
    return [100.0 + 10.0 * math.sin(i / 3.0) + i * 0.1 for i in range(n)]


def _make_candles(closes: list[float]) -> list[OHLCV]:
    return [
        OHLCV(
            symbol="BTC/USDT",
            timestamp_utc=f"bar-{i}",
            timeframe="1h",
            open=c,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1000.0 + i,
        )
        for i, c in enumerate(closes)
    ]


def test_matrix_length_matches_input() -> None:
    candles = _make_candles(_closes_path(60))
    matrix = build_feature_matrix(candles)
    assert len(matrix) == len(candles)


def test_trail_return_20_is_causal_and_correct() -> None:
    from app.analysis.features.feature_matrix import (
        TRAIL_RETURN_WINDOW,
        compute_trailing_returns,
    )

    closes = [float(100 + i) for i in range(60)]  # strictly increasing
    matrix = build_feature_matrix(_make_candles(closes))
    # Warm-up: the first TRAIL_RETURN_WINDOW rows have no anchor → None.
    for i in range(TRAIL_RETURN_WINDOW):
        assert matrix[i].trail_return_20 is None
    # Causal value: close[i]/close[i-window]-1, using only past data.
    i = 40
    expected = closes[i] / closes[i - TRAIL_RETURN_WINDOW] - 1.0
    assert matrix[i].trail_return_20 is not None
    assert math.isclose(matrix[i].trail_return_20, expected)
    # Direct helper matches the matrix wiring.
    helper = compute_trailing_returns(closes, TRAIL_RETURN_WINDOW)
    assert helper[i] is not None and math.isclose(helper[i], expected)
    assert helper[:TRAIL_RETURN_WINDOW] == [None] * TRAIL_RETURN_WINDOW


def test_empty_candles_returns_empty_matrix() -> None:
    assert build_feature_matrix([]) == []


def test_timestamp_and_close_passthrough() -> None:
    candles = _make_candles(_closes_path(40))
    matrix = build_feature_matrix(candles)
    for row, candle in zip(matrix, candles, strict=True):
        assert row.timestamp_utc == candle.timestamp_utc
        assert row.close == candle.close


def test_first_row_features_are_all_none() -> None:
    candles = _make_candles(_closes_path(60))
    first = build_feature_matrix(candles)[0]
    assert first.log_return is None
    assert first.rsi_14 is None
    assert first.adx_14 is None
    assert first.realized_vol_24 is None
    assert first.ema_12 is None
    assert first.ema_26 is None
    assert first.macd is None
    assert first.bollinger_z_20 is None


def test_last_row_is_fully_populated() -> None:
    candles = _make_candles(_closes_path(60))
    last = build_feature_matrix(candles)[-1]
    assert last.log_return is not None
    assert last.rsi_14 is not None
    assert last.adx_14 is not None
    assert last.plus_di_14 is not None
    assert last.minus_di_14 is not None
    assert last.realized_vol_24 is not None
    assert last.ema_12 is not None
    assert last.ema_26 is not None
    assert last.macd is not None
    assert last.bollinger_z_20 is not None


def test_fields_wire_to_underlying_indicators_exactly() -> None:
    closes = _closes_path(60)
    candles = _make_candles(closes)
    matrix = build_feature_matrix(candles)

    rsi = compute_rsi(closes, period=14)
    rv = compute_realized_volatility(closes, window=24)
    log_ret = compute_log_returns(closes)
    adx = compute_adx_di([c * 1.01 for c in closes], [c * 0.99 for c in closes], closes, period=14)

    for i, row in enumerate(matrix):
        assert row.rsi_14 == rsi[i]
        assert row.realized_vol_24 == rv[i]
        assert row.log_return == log_ret[i]
        assert row.adx_14 == adx.adx[i]
        assert row.plus_di_14 == adx.plus_di[i]
        assert row.minus_di_14 == adx.minus_di[i]


def test_macd_equals_ema_fast_minus_slow() -> None:
    candles = _make_candles(_closes_path(60))
    matrix = build_feature_matrix(candles)
    for row in matrix:
        if row.ema_12 is not None and row.ema_26 is not None:
            assert row.macd == row.ema_12 - row.ema_26
        else:
            assert row.macd is None


def test_no_lookahead_prefix_equals_full_prefix() -> None:
    # The integrity test: truncating future candles must NOT change any earlier
    # row. Every indicator used must be causal (depends only on past+current).
    candles = _make_candles(_closes_path(60))
    full = build_feature_matrix(candles)
    for k in (30, 45, 59):
        prefix = build_feature_matrix(candles[:k])
        assert prefix == full[:k]


def test_funding_defaults_to_none_without_series() -> None:
    # OHLCV-only callers (and every existing test) must leave funding fields None.
    candles = _make_candles(_closes_path(40))
    for row in build_feature_matrix(candles):
        assert row.funding_rate is None
        assert row.funding_rate_z is None


def _iso_candles(closes: list[float], step_ms: int) -> list[OHLCV]:
    from datetime import UTC, datetime

    return [
        OHLCV(
            symbol="BTC/USDT",
            timestamp_utc=datetime.fromtimestamp(i * step_ms / 1000, tz=UTC).isoformat(),
            timeframe="1h",
            open=c,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]


def test_funding_aligns_causally_onto_bars() -> None:
    from app.analysis.features.funding_align import FundingPoint

    h = 3_600_000
    closes = _closes_path(20)
    candles = _iso_candles(closes, h)  # 1h bars at t=0..19h
    # Funding settles at t=0 (0.0001) and t=8h (0.0004).
    funding = [FundingPoint(0, 0.0001), FundingPoint(8 * h, 0.0004)]
    matrix = build_feature_matrix(candles, funding)
    # Bars 0..7 carry the first settlement; 8..19 the second (forward-fill, causal).
    for i in range(8):
        assert matrix[i].funding_rate == 0.0001
    for i in range(8, 20):
        assert matrix[i].funding_rate == 0.0004
    # Length and close passthrough are unaffected by the funding overlay.
    assert len(matrix) == len(candles)


def test_whale_flow_defaults_to_none_without_series() -> None:
    # OHLCV-only callers (and every existing test) must leave whale fields None.
    candles = _make_candles(_closes_path(40))
    for row in build_feature_matrix(candles):
        assert row.coin_netflow_usd is None
        assert row.coin_netflow_z is None
        assert row.stable_netflow_usd is None
        assert row.stable_netflow_z is None


def test_whale_flow_aligns_and_populates() -> None:
    from app.analysis.features.whale_flow_align import FlowPoint

    h = 3_600_000
    closes = _closes_path(40)
    candles = _iso_candles(closes, h)  # 1h bars at t=0..39h
    # One coin inflow per hour from t=0 (lets the netflow z warm up and populate).
    coin_flows = [FlowPoint(i * h, 100_000.0 + 1000.0 * i) for i in range(40)]
    stable_flows = [FlowPoint(i * h, 50_000.0) for i in range(40)]
    matrix = build_feature_matrix(candles, coin_flows=coin_flows, stable_flows=stable_flows)
    # Early bar: defined netflow (an event exists) but z still warming up → may be None.
    assert matrix[0].coin_netflow_usd is not None
    # A late bar past the z warm-up is populated for both coin and stable.
    assert matrix[-1].coin_netflow_z is not None
    assert matrix[-1].stable_netflow_usd is not None


def test_whale_flow_no_lookahead_prefix_equals_full() -> None:
    # Integrity: truncating future candles must NOT change any earlier whale-flow
    # feature. Alignment only admits events confirmed at/before each bar and the
    # z-history grows monotonically — so earlier rows are invariant.
    from app.analysis.features.whale_flow_align import FlowPoint

    h = 3_600_000
    closes = _closes_path(60)
    candles = _iso_candles(closes, h)
    coin_flows = [FlowPoint(i * h, 100_000.0 + (i % 7) * 5000.0) for i in range(60)]
    stable_flows = [FlowPoint(i * h, 40_000.0 - (i % 5) * 3000.0) for i in range(60)]
    full = build_feature_matrix(candles, coin_flows=coin_flows, stable_flows=stable_flows)
    for k in (30, 45, 59):
        prefix = build_feature_matrix(candles[:k], coin_flows=coin_flows, stable_flows=stable_flows)
        assert prefix == full[:k]
