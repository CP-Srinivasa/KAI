"""TradingView-alert → shadow-candidate feed (2026-06-22) — SHADOW-ONLY, gated.

The TV webhook pipeline registers alerts but the auto-promoter rejects almost
all of them (``unsupported_event``: the operator's alert messages omit ``price``
and use USD/.P chart tickers), and even promoted signals are consumed
measurement-only — so the TV alerts produced ZERO effectiveness data. This
feeder closes that gap WITHOUT execution: it maps each open TV buy/sell alert to
a resolvable USDT pair, stamps an entry price (the alert's own ``price`` if it
carries one, else a live last-close), and records it as a SHADOW candidate
(``source="tradingview_webhook"``, ``candidate_kind="technical"``) so the
existing shadow resolver measures its forward returns / MFE-MAE with the same
robust tooling as every other signal. No order, no position, no capital.

Default OFF (``ALERT_TRADINGVIEW_SHADOW_FEED_ENABLED``). Long-only by default
(``allow_short`` mirrors the technical path); idempotent via a consumed-id file.
Unmappable tickers (the BitMEX-only ``SPCXUSD.UMM2031``, dated futures like
``SOLM2026``) are counted and skipped — they have no Binance OHLCV source and
need a dedicated price feed before they can be measured.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.core.logging import get_logger
from app.market_data.models import OHLCV
from app.observability.shadow_candidate_ledger import (
    LEDGER_PATH,
    ShadowCandidate,
    record_candidate,
)

logger = get_logger(__name__)

DEFAULT_TIMEFRAME = "1h"
CONSUMED_PATH = Path("artifacts/tradingview_shadow_consumed.json")

_ACTION_TO_SIDE = {"buy": "long", "sell": "short"}
_ACTION_TO_SENTIMENT = {"buy": "bullish", "sell": "bearish"}
# Quote suffixes we accept; ALL normalise to the liquid USDT spot pair so a TV
# "BTCUSD.P" perp chart ticker resolves to the tradeable BTC/USDT we measure on.
_KNOWN_QUOTES = ("USDT", "USDC", "USD")
# TV chart decorations carrying a dated-future / exotic contract code we cannot
# map to a liquid USDT pair (e.g. "SPCXUSD.UMM2031", "SOLM2026", "SOLM2027").
_EXOTIC_MARKERS = (".UMM", ".UM", "M2026", "M2027", "M2031")


def tv_pair(ticker: str | None) -> str | None:
    """Normalise a TradingView chart ticker to a resolvable BASE/USDT pair.

    ``BTCUSD.P`` → ``BTC/USDT``; ``XRPUSD`` → ``XRP/USDT``; ``BYBIT:ETHUSDT`` →
    ``ETH/USDT``. Returns ``None`` for exotic/dated contracts (SPCX, *M2026) and
    anything without a recognised quote — those have no Binance OHLCV source.
    """
    up = (ticker or "").strip().upper().split(":", 1)[-1]
    if not up or any(marker in up for marker in _EXOTIC_MARKERS):
        return None
    for suffix in (".PERP", ".P"):  # ".PERP" first so ".P" doesn't truncate it
        if up.endswith(suffix):
            up = up[: -len(suffix)]
            break
    for quote in _KNOWN_QUOTES:
        if up.endswith(quote) and len(up) > len(quote):
            return f"{up[: -len(quote)]}/USDT"
    return None


class _OhlcvSource(Protocol):
    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]: ...


def _last_close(candles: Sequence[OHLCV]) -> float | None:
    if not candles:
        return None
    return sorted(candles, key=lambda c: c.timestamp_utc)[-1].close


def load_consumed(path: Path = CONSUMED_PATH) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def save_consumed(ids: set[str], path: Path = CONSUMED_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(ids)), encoding="utf-8")


async def _resolve_entry(
    ev: Any, pair: str, adapter: _OhlcvSource | None, timeframe: str
) -> float | None:
    """Prefer the alert's own signal-time price; fall back to a live last-close."""
    price = getattr(ev, "price", None)
    try:
        if price is not None and float(price) > 0:
            return float(price)
    except (TypeError, ValueError):
        pass
    if adapter is None:
        return None
    try:
        candles = await adapter.get_ohlcv(pair, timeframe, 5)
    except Exception as exc:  # noqa: BLE001 — fail-soft per symbol
        logger.warning("tv_shadow_feed.ohlcv_failed", symbol=pair, error=str(exc)[:200])
        return None
    return _last_close(candles)


async def feed_tv_shadow(
    *,
    events: Iterable[Any],
    adapter: _OhlcvSource | None,
    consumed_ids: set[str],
    allow_short: bool = False,
    timeframe: str = DEFAULT_TIMEFRAME,
    write: bool = True,
    ledger_path: Path = LEDGER_PATH,
    now_utc: str | None = None,
) -> dict[str, object]:
    """Record open TV alerts as SHADOW candidates. Never executes. Idempotent.

    ``consumed_ids`` is mutated in place with every event the feeder reaches a
    terminal decision on (recorded / skipped). A transient price-fetch failure
    does NOT consume the event, so it is retried on the next tick.
    """
    ts = now_utc or datetime.now(UTC).isoformat()
    recorded = unmappable = no_price = short_skipped = bad_action = already = 0
    for ev in events:
        event_id = getattr(ev, "event_id", None)
        if not event_id or event_id in consumed_ids:
            already += 1
            continue
        action = (getattr(ev, "action", None) or "").lower()
        side = _ACTION_TO_SIDE.get(action)
        if side is None:
            bad_action += 1
            consumed_ids.add(event_id)
            continue
        if side == "short" and not allow_short:
            short_skipped += 1
            consumed_ids.add(event_id)
            continue
        pair = tv_pair(getattr(ev, "ticker", None))
        if pair is None:
            unmappable += 1
            consumed_ids.add(event_id)
            continue
        entry = await _resolve_entry(ev, pair, adapter, timeframe)
        if entry is None or entry <= 0:
            no_price += 1
            continue  # transient — do NOT consume, retry next tick
        sentiment = _ACTION_TO_SENTIMENT.get(action)
        candidate = ShadowCandidate.from_geometry(
            candidate_id=f"tv-{pair.replace('/', '')}-{event_id}",
            ts_utc=getattr(ev, "received_at", None) or ts,
            symbol=pair,
            side=side,
            entry_price=entry,
            stop_price=None,
            take_price=None,
            source="tradingview_webhook",
            candidate_kind="technical",
            signal_origin="tradingview_webhook",
            score_source=getattr(ev, "strategy", None),
            directional_state=sentiment,
            sentiment=sentiment,
        )
        if write and record_candidate(candidate, path=ledger_path):
            recorded += 1
        consumed_ids.add(event_id)
    return {
        "recorded": recorded,
        "unmappable": unmappable,
        "no_price": no_price,
        "short_skipped": short_skipped,
        "bad_action": bad_action,
        "already": already,
    }


async def run_from_settings(
    *, settings: Any | None = None, adapter: _OhlcvSource | None = None, write: bool = True
) -> dict[str, object]:
    """Gated entrypoint for CLI / timer. No-op summary when the flag is OFF."""
    from app.core.settings import get_settings

    settings = settings or get_settings()
    alerts = settings.alerts
    if not getattr(alerts, "tradingview_shadow_feed_enabled", False):
        return {"enabled": False}

    from app.signals.tradingview_promotion import (
        filter_open_events,
        load_decisions,
        load_pending_events,
    )

    pending_path = Path(settings.tradingview.webhook_pending_signals_log)
    decisions_path = Path(settings.tradingview.pending_decisions_log)
    events = filter_open_events(load_pending_events(pending_path), load_decisions(decisions_path))

    if adapter is None:
        from app.market_data.service import create_market_data_adapter

        adapter = create_market_data_adapter(provider=settings.market_data_provider)

    consumed = load_consumed()
    before = len(consumed)
    summary = await feed_tv_shadow(
        events=events,
        adapter=adapter,
        consumed_ids=consumed,
        allow_short=getattr(alerts, "allow_short_technical", False),
        write=write,
    )
    if write and len(consumed) != before:
        save_consumed(consumed)
    summary["enabled"] = True
    summary["open_events"] = len(events)
    return summary
