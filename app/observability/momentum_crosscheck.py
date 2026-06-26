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


def build_crosscheck_rows(
    universe_rows: Sequence[Mapping[str, Any]],
    ratings: Mapping[str, TaRating],
) -> list[dict[str, Any]]:
    """Pure: combine universe rows with per-symbol TA ratings + agreement flag."""
    out: list[dict[str, Any]] = []
    for row in universe_rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        momentum_score = float(row.get("momentum_score", 0.0))
        rating = ratings.get(symbol)
        rsi = round(rating.rsi, 2) if (rating is not None and rating.rsi is not None) else None
        out.append(
            {
                "symbol": symbol,
                "rank": int(row.get("rank", 0)),
                "momentum_score": round(momentum_score, 6),
                "ta_label": rating.label if rating is not None else "unavailable",
                "ta_score": round(rating.score, 6) if rating is not None else None,
                "ta_trend": rating.trend if rating is not None else "unavailable",
                "rsi": rsi,
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
    ratings: dict[str, TaRating] = {}
    for row in rows:
        symbol = row.get("symbol") if isinstance(row, dict) else None
        if not isinstance(symbol, str) or not symbol:
            continue
        try:
            candles = await source.get_ohlcv(symbol, "1d", lookback_days)
        except Exception:  # noqa: BLE001 — one bad symbol must not abort the cross-check
            continue
        rating = compute_ta_rating(candles)
        if rating is not None:
            ratings[symbol] = rating
    return build_crosscheck_rows(rows, ratings)


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
