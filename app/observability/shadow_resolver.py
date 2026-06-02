"""Binance-backed resolver for the shadow-candidate ledger (Phase B).

Network shell around the IO-free ``shadow_candidate_ledger.resolve_pending``:
fetches 1m klines from the Binance public REST endpoint (no auth, read-only) and
feeds them to the pure resolver. Kept separate from the core so the ledger stays
unit-testable offline and the kline source stays swappable.

NEVER touches paper_engine / exchange / order-router — it only reads prices and
writes resolved diagnostic metrics. A missing/failed kline fetch leaves the
candidate pending (resolver counts it as ``no_data``); it never crashes.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.observability.shadow_candidate_ledger import (
    LEDGER_PATH,
    RESOLVED_PATH,
    Bar,
    resolve_pending,
)

logger = logging.getLogger(__name__)

_BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
# Exotic / non-Binance-spot symbols seen in the audit cohort. Skipped early so a
# guaranteed-404 is not even attempted (resolver leaves them pending).
_NON_BINANCE_HINT = "USDT"


def _to_binance_pair(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


def binance_kline_fetcher(symbol: str, start_ms: int, end_ms: int) -> Sequence[Bar] | None:
    """Fetch 1m klines as (open_ms, high, low, close). None on any failure."""
    pair = _to_binance_pair(symbol)
    url = (
        f"{_BINANCE_KLINES}?symbol={pair}&interval=1m"
        f"&startTime={int(start_ms)}&endTime={int(end_ms)}&limit=1000"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 — fixed https host
            raw = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 — any network/parse error → pending
        logger.info("[shadow] kline fetch failed for %s: %s", symbol, exc)
        return None
    bars: list[Bar] = []
    for k in raw:
        try:
            bars.append((int(k[0]), float(k[2]), float(k[3]), float(k[4])))
        except (IndexError, TypeError, ValueError):
            continue
    return bars or None


def resolve_with_binance(
    *,
    now: datetime | None = None,
    ledger_path: Path = LEDGER_PATH,
    resolved_path: Path = RESOLVED_PATH,
) -> dict[str, int]:
    """Resolve all eligible pending candidates using Binance klines."""
    return resolve_pending(
        fetch_klines=binance_kline_fetcher,
        now=now or datetime.now(UTC),
        ledger_path=ledger_path,
        resolved_path=resolved_path,
    )


__all__ = ["binance_kline_fetcher", "resolve_with_binance"]
