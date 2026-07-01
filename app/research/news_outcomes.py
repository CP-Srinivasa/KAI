"""FILL-INDEPENDENT outcome loader for DIRECTIONAL-NEWS hypothesis evaluation.

The canonical shadow-candidate pool (:mod:`app.research.shadow_outcomes`) only
carries INTRADAY forward returns (<=1h) attached to trade candidates. A news
signal is a different, orthogonal, non-price hypothesis: *does the DIRECTION a
source assigns to a piece of news (bullish/bearish) carry a tradeable forward
return on the mentioned asset over the following hours-to-days?*

This module builds that outcome set with no coupling to paper fills and no
look-ahead:

  * :func:`load_news_events` — projects directional documents
    (``sentiment_label`` in {bullish, bearish} + a usable ticker +
    ``published_at``) into time-ordered :class:`NewsEvent` records.
  * :func:`forward_returns_for_event` — given a symbol's OHLCV series, enters at
    the OPEN of the first candle that opens *at or after* the news (the first
    price knowable without look-ahead) and measures open-to-open forward returns
    at each horizon, SIDE-ADJUSTED so ``fwd>0`` means the source's direction paid.
  * :func:`build_news_outcomes` — assembles the time-ordered outcome pool from
    events + a per-symbol OHLCV series map.

Network-free by design: OHLCV fetching is done by the caller (the CLI wires
``BinanceAdapter`` via :func:`app.research.runner.build_fetch` +
:func:`app.market_data.history_loader.load_ohlcv_history`) and passed in, so every
function here is deterministically unit-testable. Read-only analysis; nothing here
touches sizing or the execution path.
"""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.market_data.kline_windows import interval_to_ms
from app.market_data.models import OHLCV
from app.research.shadow_outcomes import parse_ts

# News moves markets over hours-to-days, not seconds — deliberately LONGER than
# the intraday canonical horizons. All are integer multiples of a 1h grid.
NEWS_HORIZONS_S: tuple[int, ...] = (3600, 14400, 86400, 259200)  # 1h, 4h, 24h, 72h
DEFAULT_TIMEFRAME = "1h"
# |fwd| at/above this is a delisted/bad-data sentinel, not a real return.
DEFAULT_MAX_ABS_BPS = 5000.0
# If the first candle open after the news is more than this far out, there is no
# clean entry (data gap) — drop the event rather than enter stale.
DEFAULT_MAX_ENTRY_LAG_S = 7200.0  # 2h

_BULL = {"bullish", "bull", "positive"}
_BEAR = {"bearish", "bear", "negative"}


@dataclass(frozen=True)
class NewsEvent:
    """A directional news event ready for forward-return measurement."""

    symbol: str  # KAI canonical pair, e.g. "BTC/USDT"
    side: str  # "long" (bullish) | "short" (bearish)
    entry_ts: datetime  # published_at, tz-aware UTC
    source: str  # source_name, e.g. "cointelegraph"
    confidence: float | None  # directional_confidence, if present


def sentiment_to_side(label: object) -> str | None:
    """Map a sentiment label to a trade side; ``None`` for neutral/unknown."""
    if not isinstance(label, str):
        return None
    key = label.strip().lower()
    if key in _BULL:
        return "long"
    if key in _BEAR:
        return "short"
    return None


def first_ticker_symbol(tickers: object) -> str | None:
    """First usable KAI pair from a JSON string or list of tickers; else ``None``."""
    raw = tickers
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            raw = json.loads(s)
        except ValueError:
            # A bare "BTC/USDT" string (not JSON) is still usable.
            raw = [s]
    if not isinstance(raw, list):
        return None
    for t in raw:
        if isinstance(t, str) and t.strip():
            return t.strip().upper()
    return None


def load_news_events(rows: list[dict[str, Any]]) -> list[NewsEvent]:
    """Directional documents -> time-ordered :class:`NewsEvent` records.

    A row needs a directional ``sentiment_label`` (bullish/bearish), a usable
    ``tickers`` entry, and a parseable ``published_at``; anything else is dropped.
    """
    out: list[NewsEvent] = []
    for r in rows:
        side = sentiment_to_side(r.get("sentiment_label"))
        symbol = first_ticker_symbol(r.get("tickers"))
        ets = _coerce_ts(r.get("published_at"))
        if side is None or symbol is None or ets is None:
            continue
        conf = r.get("directional_confidence")
        out.append(
            NewsEvent(
                symbol=symbol,
                side=side,
                entry_ts=ets,
                source=str(r.get("source_name") or "unknown"),
                confidence=float(conf) if isinstance(conf, int | float) else None,
            )
        )
    out.sort(key=lambda e: e.entry_ts)
    return out


def candle_open_ms(timestamp_utc: str) -> int | None:
    """Parse an adapter ISO timestamp to epoch ms; ``None`` if unparseable."""
    try:
        return int(round(datetime.fromisoformat(timestamp_utc).timestamp() * 1000))
    except (ValueError, TypeError):
        return None


def index_series(candles: list[OHLCV]) -> tuple[list[int], dict[int, OHLCV]]:
    """Index a candle series by grid open-ms: (sorted open_ms list, open_ms->candle)."""
    by_open: dict[int, OHLCV] = {}
    for c in candles:
        ms = candle_open_ms(c.timestamp_utc)
        if ms is not None:
            by_open[ms] = c
    return sorted(by_open), by_open


def forward_returns_for_event(
    event: NewsEvent,
    open_ms_sorted: list[int],
    by_open: dict[int, OHLCV],
    *,
    horizons: tuple[int, ...] = NEWS_HORIZONS_S,
    max_entry_lag_s: float = DEFAULT_MAX_ENTRY_LAG_S,
    max_abs_bps: float = DEFAULT_MAX_ABS_BPS,
) -> dict[int, float | None] | None:
    """Side-adjusted open-to-open forward returns (bps) for one event.

    Entry = OPEN of the first candle opening at/after ``entry_ts`` (no look-ahead).
    Horizon return = ``(open[entry_open + h] / open[entry]) - 1`` in bps, multiplied
    by +1 (long) / -1 (short) so a positive value means the source's DIRECTION paid.
    Returns ``None`` (drop the event) when there is no clean entry, the entry price
    is non-positive, or any horizon trips the delisted/bad-data sentinel. Horizons
    with no candle at the exact grid point are ``None``.
    """
    entry_ms = int(round(event.entry_ts.timestamp() * 1000))
    idx = bisect_left(open_ms_sorted, entry_ms)
    if idx >= len(open_ms_sorted):
        return None
    entry_open_ms = open_ms_sorted[idx]
    if (entry_open_ms - entry_ms) > max_entry_lag_s * 1000:
        return None  # data gap: no clean entry candle near the news
    entry_price = by_open[entry_open_ms].open
    if entry_price <= 0:
        return None
    sign = 1.0 if event.side == "long" else -1.0
    fwd: dict[int, float | None] = {}
    for h in horizons:
        target = by_open.get(entry_open_ms + h * 1000)
        if target is None:
            fwd[h] = None
            continue
        raw_bps = (target.open / entry_price - 1.0) * 1e4
        if abs(raw_bps) >= max_abs_bps:  # delisted / bad-data sentinel, not signal
            return None
        fwd[h] = sign * raw_bps
    if all(v is None for v in fwd.values()):
        return None
    return fwd


def build_news_outcomes(
    events: list[NewsEvent],
    series_by_symbol: dict[str, list[OHLCV]],
    *,
    horizons: tuple[int, ...] = NEWS_HORIZONS_S,
    max_entry_lag_s: float = DEFAULT_MAX_ENTRY_LAG_S,
    max_abs_bps: float = DEFAULT_MAX_ABS_BPS,
) -> list[dict[str, Any]]:
    """Assemble the time-ordered directional-news outcome pool.

    Each outcome is ``{symbol, source, side, entry_ts (datetime), fwd:{h: bps|None}}``
    with SIDE-ADJUSTED forward returns. Events whose symbol has no OHLCV series, or
    that yield no usable forward return, are dropped. Sorted by ``entry_ts`` so a
    downstream moving-block bootstrap preserves autocorrelation.
    """
    indexed: dict[str, tuple[list[int], dict[int, OHLCV]]] = {
        sym: index_series(candles) for sym, candles in series_by_symbol.items()
    }
    out: list[dict[str, Any]] = []
    for e in events:
        idx = indexed.get(e.symbol)
        if idx is None or not idx[0]:
            continue
        fwd = forward_returns_for_event(
            e,
            idx[0],
            idx[1],
            horizons=horizons,
            max_entry_lag_s=max_entry_lag_s,
            max_abs_bps=max_abs_bps,
        )
        if fwd is None:
            continue
        out.append(
            {
                "symbol": e.symbol,
                "source": e.source,
                "side": e.side,
                "entry_ts": e.entry_ts,
                "fwd": fwd,
            }
        )
    out.sort(key=lambda o: o["entry_ts"])
    return out


def timeframe_interval_s(timeframe: str = DEFAULT_TIMEFRAME) -> int:
    """Candle interval in seconds for a timeframe (raises on unsupported)."""
    return interval_to_ms(timeframe) // 1000


def _coerce_ts(value: object) -> datetime | None:
    """Parse published_at from an ISO string or accept an existing datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return parse_ts(value)


__all__ = [
    "DEFAULT_MAX_ABS_BPS",
    "DEFAULT_MAX_ENTRY_LAG_S",
    "DEFAULT_TIMEFRAME",
    "NEWS_HORIZONS_S",
    "NewsEvent",
    "build_news_outcomes",
    "candle_open_ms",
    "first_ticker_symbol",
    "forward_returns_for_event",
    "index_series",
    "load_news_events",
    "sentiment_to_side",
    "timeframe_interval_s",
]
