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
_BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
# Exotic / non-Binance-spot symbols seen in the audit cohort. Skipped early so a
# guaranteed-404 is not even attempted (resolver leaves them pending).
_NON_BINANCE_HINT = "USDT"

# Per-process cache of the Binance-spot symbol universe (changes rarely; the
# screener runs as a short oneshot so this is fetched ~once per run).
_spot_symbols_cache: frozenset[str] | None = None


def to_binance_pair(symbol: str) -> str:
    """Normalise 'BTC/USDT' / 'btc-usdt' -> 'BTCUSDT' (Binance REST symbol form)."""
    return symbol.replace("/", "").replace("-", "").upper()


def binance_spot_symbols(*, force: bool = False) -> frozenset[str] | None:
    """TRADING Binance-spot symbols (e.g. ``{'BTCUSDT', ...}``), cached per process.

    The shadow resolver forward-resolves on Binance-spot 1m klines, so a symbol
    NOT in this set is never forward-resolvable — used to prune the screener's
    dynamic universe to measurable candidates. Read-only public REST, no auth.
    Fail-soft: returns ``None`` on any network/parse error (caller keeps the
    unfiltered universe). ``force=True`` bypasses the cache.
    """
    global _spot_symbols_cache
    if _spot_symbols_cache is not None and not force:
        return _spot_symbols_cache
    try:
        # Fixed https Binance endpoint; no user-controlled scheme/host.
        with urllib.request.urlopen(_BINANCE_EXCHANGE_INFO, timeout=10) as resp:  # noqa: S310  # nosec B310
            raw = json.loads(resp.read().decode())
        syms = frozenset(
            str(s["symbol"]).upper()
            for s in raw.get("symbols", [])
            if s.get("status") == "TRADING" and s.get("symbol")
        )
    except Exception as exc:  # noqa: BLE001 — any network/parse error → unfiltered
        logger.info("[shadow] exchangeInfo fetch failed: %s", exc)
        return None
    if syms:
        _spot_symbols_cache = syms
    return syms or None


def binance_kline_fetcher(symbol: str, start_ms: int, end_ms: int) -> Sequence[Bar] | None:
    """Fetch 1m klines as (open_ms, high, low, close). None on any failure."""
    pair = to_binance_pair(symbol)
    url = (
        f"{_BINANCE_KLINES}?symbol={pair}&interval=1m"
        f"&startTime={int(start_ms)}&endTime={int(end_ms)}&limit=1000"
    )
    try:
        # Host is the fixed https Binance endpoint; query params are
        # ints/sanitised symbols — no user-controlled scheme/host.
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310  # nosec B310
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
    include_canary: bool = False,
) -> dict[str, int]:
    """Resolve all eligible pending candidates using Binance klines.

    NEO-P-002 (Weg B): by default the resolver skips canary_probe / raw_scan /
    synthetic-default rows (counted as ``skipped_kind``) so it never burns kline
    fetches on the ~372/441 near-identical canary clones. ``include_canary=True``
    is the explicit diagnostic option to resolve them too.
    """
    return resolve_pending(
        fetch_klines=binance_kline_fetcher,
        now=now or datetime.now(UTC),
        ledger_path=ledger_path,
        resolved_path=resolved_path,
        include_canary=include_canary,
    )


__all__ = [
    "binance_kline_fetcher",
    "binance_spot_symbols",
    "resolve_with_binance",
    "to_binance_pair",
]
