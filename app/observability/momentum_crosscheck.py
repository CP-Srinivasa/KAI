"""momentum_crosscheck — G4: own momentum rank vs own-TA rating (cross-check).

Combines the G0 universe snapshot (momentum percentile = best-performer) with a
ToS-compliant TA rating (``app.market_data.ta_rating``, the TradingView-rating
substitute computed from our OWN OHLCV) and flags where the two AGREE or DIVERGE.
Purely informational — zero sizing impact; it never feeds the trading loop.

This is the first provider of the G4 "external Cross-Check"-Lane. TradingView is
deliberately NOT pulled (ToS): the rating is computed locally. Further legitimate
providers (liquidations, on-chain, sentiment) plug in behind the same pattern.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from app.market_data.models import OHLCV
from app.market_data.ta_rating import TaRating, compute_ta_rating
from app.observability.momentum_universe_ledger import read_latest

DEFAULT_UNIVERSE_LEDGER = Path("artifacts/momentum_universe_candidates.jsonl")
DEFAULT_CROSSCHECK_LEDGER = Path("artifacts/momentum_crosscheck.jsonl")

_BULLISH_MOMENTUM = 0.5
_TA_BULLISH = 0.15
_TA_BEARISH = -0.15
# |8h funding| >= 5 bps (0.05%) = crowded positioning (a documented mean-reversion
# pressure: crowded longs pay to hold, which historically caps the move).
_FUNDING_CROWDED_BPS = 5.0


class OhlcvSource(Protocol):
    async def get_ohlcv(
        self, symbol: str, timeframe: str = ..., limit: int = ...
    ) -> list[OHLCV]: ...


def _agreement(momentum_score: float, rating: TaRating | None) -> str:
    if rating is None:
        return "unavailable"
    momentum_bullish = momentum_score > _BULLISH_MOMENTUM
    if rating.score >= _TA_BULLISH:
        return "agree_bullish" if momentum_bullish else "ta_only_bullish"
    if rating.score <= _TA_BEARISH:
        # Strong recent performer but TA says sell → mean-reversion risk worth seeing.
        return "divergence" if momentum_bullish else "agree_bearish"
    return "neutral"


def _funding_signal(funding_bps: float | None) -> str:
    if funding_bps is None:
        return "unavailable"
    if funding_bps >= _FUNDING_CROWDED_BPS:
        return "long_crowded"  # longs paying shorts → overheated longs (mean-reversion risk)
    if funding_bps <= -_FUNDING_CROWDED_BPS:
        return "short_crowded"
    return "neutral"


def build_crosscheck_rows(
    universe_rows: Sequence[Mapping[str, Any]],
    ratings: Mapping[str, TaRating],
    funding: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Pure: combine universe rows with per-symbol TA ratings + funding + flags.

    ``funding`` maps a symbol to its 8h funding rate as a FRACTION (0.0001 = 1 bp);
    surfaced as ``funding_bps`` + a ``funding_signal`` (long_crowded/short_crowded).
    """
    funding = funding or {}
    out: list[dict[str, Any]] = []
    for row in universe_rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        momentum_score = float(row.get("momentum_score", 0.0))
        rating = ratings.get(symbol)
        rsi = round(rating.rsi, 2) if (rating is not None and rating.rsi is not None) else None
        funding_rate = funding.get(symbol)
        funding_bps = round(funding_rate * 10_000.0, 4) if funding_rate is not None else None
        out.append(
            {
                "symbol": symbol,
                "rank": int(row.get("rank", 0)),
                "momentum_score": round(momentum_score, 6),
                "ta_label": rating.label if rating is not None else "unavailable",
                "ta_score": round(rating.score, 6) if rating is not None else None,
                "ta_trend": rating.trend if rating is not None else "unavailable",
                "rsi": rsi,
                "funding_bps": funding_bps,
                "funding_signal": _funding_signal(funding_bps),
                "agreement": _agreement(momentum_score, rating),
            }
        )
    return out


async def build_crosscheck(
    source: OhlcvSource,
    *,
    ledger_path: Path = DEFAULT_UNIVERSE_LEDGER,
    top_n: int = 15,
    lookback_days: int = 40,
) -> list[dict[str, Any]]:
    """Read the latest universe snapshot, compute a TA rating per symbol, combine.

    Fail-soft: a symbol whose OHLCV is missing/short simply has no rating
    (``ta_label="unavailable"``); a dead source yields ratings-less rows.
    """
    snapshot = read_latest(ledger_path)
    universe = snapshot.get("universe") if isinstance(snapshot, dict) else None
    if not isinstance(universe, list) or not universe:
        return []
    rows = universe[:top_n]
    # Funding is optional enrichment: only if the source exposes get_funding_rate
    # (the exchange adapters do). A non-funding source (e.g. a pure OHLCV fake)
    # simply yields no funding column — fail-soft, backward compatible.
    get_funding = getattr(source, "get_funding_rate", None)
    ratings: dict[str, TaRating] = {}
    funding: dict[str, float] = {}
    for row in rows:
        symbol = row.get("symbol") if isinstance(row, dict) else None
        if not isinstance(symbol, str) or not symbol:
            continue
        try:
            candles = await source.get_ohlcv(symbol, "1d", lookback_days)
        except Exception:  # noqa: BLE001 — one bad symbol must not abort the cross-check
            candles = []
        rating = compute_ta_rating(candles)
        if rating is not None:
            ratings[symbol] = rating
        if callable(get_funding):
            try:
                snap = await get_funding(symbol)
            except Exception:  # noqa: BLE001 — funding is best-effort enrichment
                snap = None
            rate = getattr(snap, "rate", None)
            if isinstance(rate, (int, float)):
                funding[symbol] = float(rate)
    return build_crosscheck_rows(rows, ratings, funding=funding)


def append_crosscheck(
    path: Path, rows: Sequence[Mapping[str, Any]], *, now: datetime
) -> dict[str, Any]:
    """Append one cross-check snapshot to the JSONL ledger. Returns the record."""
    record: dict[str, Any] = {"ts": now.isoformat(), "count": len(rows), "rows": list(rows)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return record


def read_latest_crosscheck(path: Path) -> dict[str, Any] | None:
    """Return the newest cross-check snapshot, or ``None`` if missing/empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    latest: dict[str, Any] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            latest = obj
    return latest


__all__ = [
    "append_crosscheck",
    "build_crosscheck",
    "build_crosscheck_rows",
    "read_latest_crosscheck",
]
