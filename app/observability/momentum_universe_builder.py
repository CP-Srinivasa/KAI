"""momentum_universe_builder — I/O layer that turns exchange data into a ranked universe.

Fetches the volume-ranked symbol list (sanctioned exchange data — NO scraping)
and per-symbol daily OHLCV, derives a turnover proxy + multi-window returns, and
hands them to the pure :func:`rank_universe`. Fail-soft by contract: any symbol
with bad/short data is skipped, and a dead source yields an empty universe
rather than an exception — this runs read-only and must never break a caller.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from app.market_data.models import OHLCV
from app.observability.momentum_universe import (
    RankedSymbol,
    UniverseCandidate,
    rank_universe,
)

# Return windows in *days*, mapped to the label used in the candidate's map.
_WINDOWS: tuple[tuple[str, int], ...] = (("24h", 1), ("7d", 7), ("30d", 30))


class MomentumUniverseSource(Protocol):
    """Structural type satisfied by the exchange adapters (Bybit/Binance/…)."""

    async def top_symbols_by_volume(self, limit: int = ...) -> list[str]: ...

    async def get_ohlcv(
        self, symbol: str, timeframe: str = ..., limit: int = ...
    ) -> list[OHLCV]: ...


def candidate_from_ohlcv(symbol: str, candles: Sequence[OHLCV]) -> UniverseCandidate | None:
    """Build a candidate from ascending (oldest→newest) daily candles.

    Returns ``None`` when there is not enough valid data for even the shortest
    window (needs ≥2 finite, positive closes). Longer windows are filled only
    when enough history exists; the pure ranker renormalizes over what's present.
    A window whose base close is non-finite or ≤0 is skipped (no fabricated %).
    """
    closes = [c.close for c in candles]
    if len(closes) < 2:
        return None
    last = closes[-1]
    if not (math.isfinite(last) and last > 0):
        return None
    returns: dict[str, float] = {}
    for label, days in _WINDOWS:
        if len(closes) > days:
            base = closes[-1 - days]
            if math.isfinite(base) and base > 0:
                returns[label] = (last / base - 1.0) * 100.0
    if not returns:
        return None
    last_vol = candles[-1].volume
    turnover = last_vol * last if (math.isfinite(last_vol) and last_vol >= 0) else 0.0
    return UniverseCandidate(symbol=symbol, turnover_24h=turnover, window_returns_pct=returns)


async def build_universe(
    source: MomentumUniverseSource,
    *,
    top_n: int = 15,
    universe_limit: int = 50,
    lookback_days: int = 31,
    timeframe: str = "1d",
) -> list[RankedSymbol]:
    """Fetch volume-ranked symbols + daily OHLCV, build candidates, rank top-N.

    Never raises: a failing ``top_symbols_by_volume`` yields ``[]``; a failing
    per-symbol ``get_ohlcv`` skips just that symbol.
    """
    try:
        symbols = await source.top_symbols_by_volume(universe_limit)
    except Exception:  # noqa: BLE001 — fail-soft: no universe rather than a crash
        return []
    candidates: list[UniverseCandidate] = []
    for sym in symbols:
        try:
            candles = await source.get_ohlcv(sym, timeframe, lookback_days)
        except Exception:  # noqa: BLE001 — skip this symbol, keep the rest
            continue
        candidate = candidate_from_ohlcv(sym, candles)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return []
    return rank_universe(candidates, top_n=top_n)
