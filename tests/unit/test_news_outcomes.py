"""Unit tests for the directional-news fill-independent outcome loader."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.market_data.models import OHLCV
from app.research.news_outcomes import (
    NEWS_HORIZONS_S,
    NewsEvent,
    build_news_outcomes,
    first_ticker_symbol,
    forward_returns_for_event,
    index_series,
    load_news_events,
    merge_event_windows,
    sentiment_to_side,
    timeframe_interval_s,
)

_T0 = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def _series(
    n: int, *, base: float = 100.0, step: float = 0.1, start: datetime = _T0
) -> list[OHLCV]:
    """n contiguous 1h candles; open[i] = base + i*step (linear, sentinel-safe)."""
    out: list[OHLCV] = []
    for i in range(n):
        ts = (start + timedelta(hours=i)).isoformat()
        px = base + i * step
        out.append(
            OHLCV(
                symbol="BTC/USDT",
                timestamp_utc=ts,
                timeframe="1h",
                open=px,
                high=px,
                low=px,
                close=px,
                volume=1.0,
            )
        )
    return out


# ── sentiment_to_side ────────────────────────────────────────────────────────


def test_sentiment_to_side_maps_directional() -> None:
    assert sentiment_to_side("bullish") == "long"
    assert sentiment_to_side("BEARISH") == "short"
    assert sentiment_to_side(" Bull ") == "long"
    assert sentiment_to_side("neutral") is None
    assert sentiment_to_side("") is None
    assert sentiment_to_side(123) is None
    assert sentiment_to_side(None) is None


# ── first_ticker_symbol ──────────────────────────────────────────────────────


def test_first_ticker_symbol_handles_json_and_list_and_junk() -> None:
    assert first_ticker_symbol('["BTC/USDT"]') == "BTC/USDT"
    assert first_ticker_symbol(["eth/usdt", "sol/usdt"]) == "ETH/USDT"
    assert first_ticker_symbol("SOL/USDT") == "SOL/USDT"
    assert first_ticker_symbol("[]") is None
    assert first_ticker_symbol("") is None
    assert first_ticker_symbol(None) is None
    assert first_ticker_symbol([123]) is None


# ── load_news_events ─────────────────────────────────────────────────────────


def test_load_news_events_filters_and_sorts() -> None:
    rows = [
        {
            "sentiment_label": "bearish",
            "tickers": '["ETH/USDT"]',
            "published_at": "2026-06-01T05:00:00+00:00",
            "source_name": "cointelegraph",
            "directional_confidence": 0.7,
        },
        {
            "sentiment_label": "bullish",
            "tickers": ["BTC/USDT"],
            "published_at": "2026-06-01T01:00:00+00:00",
            "source_name": "decrypt",
        },
        # dropped: neutral
        {
            "sentiment_label": "neutral",
            "tickers": ["BTC/USDT"],
            "published_at": "2026-06-01T02:00:00",
        },
        # dropped: no ticker
        {"sentiment_label": "bullish", "tickers": "[]", "published_at": "2026-06-01T03:00:00"},
        # dropped: bad ts
        {"sentiment_label": "bullish", "tickers": ["BTC/USDT"], "published_at": "not-a-date"},
    ]
    events = load_news_events(rows)
    assert [e.symbol for e in events] == ["BTC/USDT", "ETH/USDT"]  # sorted by ts
    assert events[0].side == "long"
    assert events[0].source == "decrypt"
    assert events[0].confidence is None
    assert events[1].side == "short"
    assert events[1].confidence == 0.7


def test_load_news_events_accepts_naive_ts_as_utc() -> None:
    events = load_news_events(
        [
            {
                "sentiment_label": "bullish",
                "tickers": ["BTC/USDT"],
                "published_at": "2026-06-01T00:00:00",
            }
        ]
    )
    assert events[0].entry_ts == _T0


# ── forward_returns_for_event ────────────────────────────────────────────────


def _event(side: str, ts: datetime = _T0, symbol: str = "BTC/USDT") -> NewsEvent:
    return NewsEvent(symbol=symbol, side=side, entry_ts=ts, source="src", confidence=None)


def test_forward_returns_long_side_adjusted() -> None:
    open_ms, by_open = index_series(_series(80))
    fwd = forward_returns_for_event(_event("long"), open_ms, by_open)
    assert fwd is not None
    # entry open=100.0; open[1]=100.1 ->10bps; [4]->40; [24]=102.4 ->240; [72]=107.2 ->720
    assert round(fwd[3600], 1) == 10.0
    assert round(fwd[14400], 1) == 40.0
    assert round(fwd[86400], 1) == 240.0
    assert round(fwd[259200], 1) == 720.0


def test_forward_returns_short_flips_sign() -> None:
    open_ms, by_open = index_series(_series(80))
    fwd = forward_returns_for_event(_event("short"), open_ms, by_open)
    assert fwd is not None
    assert round(fwd[3600], 1) == -10.0
    assert round(fwd[259200], 1) == -720.0


def test_forward_returns_missing_horizon_is_none() -> None:
    open_ms, by_open = index_series(_series(30))  # no candle 72h out
    fwd = forward_returns_for_event(_event("long"), open_ms, by_open)
    assert fwd is not None
    assert fwd[86400] is not None  # 24h present (idx 24)
    assert fwd[259200] is None  # 72h absent


def test_forward_returns_enters_next_candle_no_lookahead() -> None:
    # Event 20min into the 00:00 candle -> entry is the 01:00 candle open (next).
    open_ms, by_open = index_series(_series(80))
    ev = _event("long", ts=_T0 + timedelta(minutes=20))
    fwd = forward_returns_for_event(ev, open_ms, by_open)
    assert fwd is not None
    # entry_price = open[1]=100.1; +1h -> open[2]=100.2 -> ~9.99 bps
    assert 9.0 < fwd[3600] < 11.0


def test_forward_returns_entry_lag_guard_drops_gapped_entry() -> None:
    open_ms, by_open = index_series(_series(80))
    ev = _event("long", ts=_T0 - timedelta(hours=3))  # first candle is 3h > 2h away
    assert forward_returns_for_event(ev, open_ms, by_open) is None


def test_forward_returns_sentinel_drops_event() -> None:
    candles = _series(80)
    # Blow out the 24h candle open -> raw >= 5000bps sentinel.
    spiked = (
        candles[:24]
        + [
            OHLCV(
                symbol="BTC/USDT",
                timestamp_utc=candles[24].timestamp_utc,
                timeframe="1h",
                open=1000.0,
                high=1000.0,
                low=1000.0,
                close=1000.0,
                volume=1.0,
            )
        ]
        + candles[25:]
    )
    open_ms, by_open = index_series(spiked)
    assert forward_returns_for_event(_event("long"), open_ms, by_open) is None


def test_forward_returns_no_entry_when_all_candles_before_event() -> None:
    open_ms, by_open = index_series(_series(10))
    ev = _event("long", ts=_T0 + timedelta(hours=100))  # after last candle
    assert forward_returns_for_event(ev, open_ms, by_open) is None


# ── build_news_outcomes ──────────────────────────────────────────────────────


def test_build_news_outcomes_skips_symbols_without_series_and_sorts() -> None:
    events = [
        _event("long", ts=_T0 + timedelta(hours=1), symbol="ETH/USDT"),
        _event("long", ts=_T0, symbol="BTC/USDT"),
        _event("short", ts=_T0 + timedelta(hours=2), symbol="NOSERIES/USDT"),
    ]
    series = {
        "BTC/USDT": _series(80),
        "ETH/USDT": [
            OHLCV(
                symbol="ETH/USDT",
                timestamp_utc=c.timestamp_utc,
                timeframe="1h",
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
            for c in _series(80)
        ],
    }
    out = build_news_outcomes(events, series)
    # NOSERIES dropped; BTC before ETH by entry_ts.
    assert [o["symbol"] for o in out] == ["BTC/USDT", "ETH/USDT"]
    assert all("fwd" in o and o["side"] in ("long", "short") for o in out)


def test_build_news_outcomes_empty_series_map() -> None:
    assert build_news_outcomes([_event("long")], {}) == []


# ── misc ─────────────────────────────────────────────────────────────────────


def test_timeframe_interval_s() -> None:
    assert timeframe_interval_s("1h") == 3600
    assert timeframe_interval_s("1d") == 86400


def test_news_horizons_are_hour_grid_aligned() -> None:
    assert all(h % 3600 == 0 for h in NEWS_HORIZONS_S)


# ── hedged construction (beta=1 excess vs BTC) ──────────────────────────────


def _series_sym(
    symbol: str,
    n: int,
    *,
    base: float = 100.0,
    step: float = 0.1,
    start: datetime = _T0,
) -> list[OHLCV]:
    out: list[OHLCV] = []
    for i in range(n):
        ts = (start + timedelta(hours=i)).isoformat()
        px = base + i * step
        out.append(
            OHLCV(
                symbol=symbol,
                timestamp_utc=ts,
                timeframe="1h",
                open=px,
                high=px,
                low=px,
                close=px,
                volume=1.0,
            )
        )
    return out


def test_hedged_returns_subtract_hedge_leg() -> None:
    asset = index_series(_series(80))  # 10bps per step at entry
    flat_hedge = index_series(_series_sym("BTC/USDT", 80, base=200.0, step=0.0))
    fwd = forward_returns_for_event(_event("long"), asset[0], asset[1], hedge=flat_hedge)
    assert fwd is not None
    # flat hedge (0 return) leaves the asset return untouched
    assert round(fwd[3600], 1) == 10.0

    same_slope = index_series(_series(80))  # identical relative path
    fwd0 = forward_returns_for_event(_event("long"), asset[0], asset[1], hedge=same_slope)
    assert fwd0 is not None
    assert round(fwd0[3600], 4) == 0.0  # excess return vanishes


def test_hedged_short_side_flips_excess_sign() -> None:
    asset = index_series(_series(80))
    flat_hedge = index_series(_series_sym("BTC/USDT", 80, base=200.0, step=0.0))
    fwd = forward_returns_for_event(_event("short"), asset[0], asset[1], hedge=flat_hedge)
    assert fwd is not None
    assert round(fwd[3600], 1) == -10.0


def test_hedged_drops_event_without_hedge_entry_candle() -> None:
    asset = index_series(_series(80))
    late_hedge = index_series(_series_sym("BTC/USDT", 40, start=_T0 + timedelta(hours=40)))
    assert forward_returns_for_event(_event("long"), asset[0], asset[1], hedge=late_hedge) is None


def test_hedged_missing_hedge_horizon_is_none_only_there() -> None:
    asset = index_series(_series(80))
    short_hedge = index_series(_series_sym("BTC/USDT", 2, base=200.0, step=0.0))
    fwd = forward_returns_for_event(_event("long"), asset[0], asset[1], hedge=short_hedge)
    assert fwd is not None
    assert fwd[3600] is not None  # hedge candle at +1h exists
    assert fwd[14400] is None  # hedge series too short at +4h


def test_build_news_outcomes_hedged_skips_hedge_symbol_events() -> None:
    events = [
        _event("long", ts=_T0 + timedelta(hours=1), symbol="ETH/USDT"),
        _event("long", ts=_T0 + timedelta(hours=2), symbol="BTC/USDT"),  # skipped
    ]
    series = {
        "ETH/USDT": _series_sym("ETH/USDT", 80),
        "BTC/USDT": _series_sym("BTC/USDT", 80, base=200.0, step=0.0),
    }
    out = build_news_outcomes(events, series, hedge_symbol="BTC/USDT")
    assert [o["symbol"] for o in out] == ["ETH/USDT"]


def test_build_news_outcomes_hedged_requires_hedge_series() -> None:
    import pytest

    with pytest.raises(ValueError, match="hedge series missing"):
        build_news_outcomes(
            [_event("long", symbol="ETH/USDT")],
            {"ETH/USDT": _series_sym("ETH/USDT", 10)},
            hedge_symbol="BTC/USDT",
        )


# ── merge_event_windows ──────────────────────────────────────────────────────


def test_merge_event_windows_merges_overlaps_and_gaps() -> None:
    assert merge_event_windows([]) == []
    assert merge_event_windows([(0, 10)]) == [(0, 10)]
    # overlapping + unsorted input
    assert merge_event_windows([(5, 15), (0, 10)]) == [(0, 15)]
    # disjoint stays disjoint without gap tolerance
    assert merge_event_windows([(0, 10), (20, 30)]) == [(0, 10), (20, 30)]
    # gap tolerance bridges near windows
    assert merge_event_windows([(0, 10), (20, 30)], gap_ms=10) == [(0, 30)]
    # containment collapses
    assert merge_event_windows([(0, 100), (10, 20)]) == [(0, 100)]


def test_micro_constants_shape() -> None:
    from app.research.news_outcomes import (
        DEFAULT_MICRO_MAX_ENTRY_LAG_S,
        MICRO_HORIZONS_S,
        MICRO_TIMEFRAME,
    )

    assert MICRO_TIMEFRAME == "1m"
    assert all(h % 60 == 0 for h in MICRO_HORIZONS_S)
    assert max(MICRO_HORIZONS_S) == 3600
    assert DEFAULT_MICRO_MAX_ENTRY_LAG_S <= 600
