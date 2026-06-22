"""TradingView-alert → PAPER trade via the envelope bridge (2026-06-22).

Turns each fresh TV buy/sell alert into a signal ENVELOPE (explicit
entry/stop_loss/targets) appended to the bridge's envelope log
(``envelope_to_paper_bridge._ENVELOPE_LOG``). The already-scheduled bridge tick
then fills it as a PAPER position with stop/take that the monitor triggers — so
the trade appears in the portfolio and produces real entry/exit/PnL data.

PAPER only (no real capital): the bridge fills via the paper engine; live is
unreachable. Default OFF (``ALERT_TRADINGVIEW_PAPER_FEED_ENABLED``); long-only
by default; idempotent via a consumed-id file. Source tag ``tradingview_webhook``
must be in ``EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST`` for the bridge to act.

Default stop/take geometry (no TV alert carries levels): ``ALERT_TRADINGVIEW_PAPER_
STOP_PCT`` / ``..._TAKE_PCT`` (env, defaults 1.0% / 1.5%).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.core.logging import get_logger
from app.execution.envelope_to_paper_bridge import _ENVELOPE_LOG
from app.market_data.models import OHLCV
from app.observability.tradingview_shadow_feed import load_consumed, save_consumed, tv_pair

logger = get_logger(__name__)

SOURCE = "tradingview_webhook"
CONSUMED_PATH = Path("artifacts/tradingview_paper_consumed.json")
DEFAULT_TIMEFRAME = "1h"
_ACTION_TO_SIDE = {"buy": ("buy", "long"), "sell": ("sell", "short")}


def _stop_pct() -> float:
    try:
        return max(0.01, float(os.getenv("ALERT_TRADINGVIEW_PAPER_STOP_PCT", "1.0")))
    except ValueError:
        return 1.0


def _take_pct() -> float:
    try:
        return max(0.01, float(os.getenv("ALERT_TRADINGVIEW_PAPER_TAKE_PCT", "1.5")))
    except ValueError:
        return 1.5


class _OhlcvSource(Protocol):
    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]: ...


def _last_close(candles: list[OHLCV]) -> float | None:
    if not candles:
        return None
    return sorted(candles, key=lambda c: c.timestamp_utc)[-1].close


async def _resolve_entry(ev: Any, pair: str, adapter: _OhlcvSource | None, timeframe: str):
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
        logger.warning("tv_paper_feeder.ohlcv_failed", symbol=pair, error=str(exc)[:200])
        return None
    return _last_close(candles)


def build_envelope(
    *,
    event_id: str,
    pair: str,
    side: str,
    direction: str,
    entry: float,
    ts_utc: str,
    strategy: str | None,
) -> dict[str, Any]:
    """Assemble a bridge-acceptable accepted/ok signal envelope (paper)."""
    symbol = pair.replace("/", "")
    stop_mult = 1.0 - _stop_pct() / 100.0 if direction == "long" else 1.0 + _stop_pct() / 100.0
    take_mult = 1.0 + _take_pct() / 100.0 if direction == "long" else 1.0 - _take_pct() / 100.0
    stop_loss = round(entry * stop_mult, 10)
    target = round(entry * take_mult, 10)
    env_id = "ENV-TVP-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:16]
    payload: dict[str, Any] = {
        "message_type": "signal",
        "signal_id": f"SIG-TVP-{symbol}-{env_id[-8:]}",
        "source": SOURCE,
        "exchange_scope": [],
        "market_type": "futures",
        "symbol": symbol,
        "display_symbol": pair,
        "side": side,
        "direction": direction,
        "entry_type": "market",
        "targets": [target],
        "stop_loss": stop_loss,
        "leverage": 1,
        "risk_mode": "isolated",
        "status": "new",
        "timestamp_utc": ts_utc,
        "entry_value": round(entry, 10),
        "source_platform": "tradingview",
        "scale_factor": 1.0,
        "scale_resolved_at_emit": True,
    }
    if strategy:
        payload["strategy"] = strategy
    return {
        "timestamp_utc": ts_utc,
        "event": "tradingview_paper_feed",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": SOURCE,
        "execution_enabled": True,
        "write_back_allowed": False,
        "envelope_id": env_id,
        "idempotency_key": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32],
        "origin_source": SOURCE,
        "payload": payload,
        "source_platform": "tradingview",
    }


async def feed_tv_paper(
    *,
    events: Iterable[Any],
    adapter: _OhlcvSource | None,
    consumed_ids: set[str],
    allow_short: bool = False,
    timeframe: str = DEFAULT_TIMEFRAME,
    write: bool = True,
    envelope_log: Path = _ENVELOPE_LOG,
    now_utc: str | None = None,
) -> dict[str, object]:
    """Append bridge-acceptable PAPER envelopes for fresh TV alerts. Idempotent."""
    ts = now_utc or datetime.now(UTC).isoformat()
    emitted = unmappable = no_price = short_skipped = bad_action = already = 0
    lines: list[str] = []
    for ev in events:
        event_id = getattr(ev, "event_id", None)
        if not event_id or event_id in consumed_ids:
            already += 1
            continue
        sd = _ACTION_TO_SIDE.get((getattr(ev, "action", None) or "").lower())
        if sd is None:
            bad_action += 1
            consumed_ids.add(event_id)
            continue
        side, direction = sd
        if direction == "short" and not allow_short:
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
            continue  # transient — do not consume, retry next tick
        env = build_envelope(
            event_id=event_id,
            pair=pair,
            side=side,
            direction=direction,
            entry=entry,
            ts_utc=getattr(ev, "received_at", None) or ts,
            strategy=getattr(ev, "strategy", None),
        )
        lines.append(json.dumps(env))
        emitted += 1
        consumed_ids.add(event_id)
    if write and lines:
        envelope_log.parent.mkdir(parents=True, exist_ok=True)
        with envelope_log.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return {
        "emitted": emitted,
        "unmappable": unmappable,
        "no_price": no_price,
        "short_skipped": short_skipped,
        "bad_action": bad_action,
        "already": already,
    }


async def run_from_settings(
    *,
    settings: Any | None = None,
    adapter: _OhlcvSource | None = None,
    write: bool = True,
    pending_path: Path | None = None,
    consumed_path: Path | None = None,
) -> dict[str, object]:
    """Gated entrypoint. No-op when the flag is OFF. Sources ALL pending TV alerts."""
    from app.core.settings import get_settings

    settings = settings or get_settings()
    if not getattr(settings.alerts, "tradingview_paper_feed_enabled", False):
        return {"enabled": False}

    from app.signals.tradingview_promotion import load_pending_events

    p_path = Path(pending_path or settings.tradingview.webhook_pending_signals_log)
    events = load_pending_events(p_path)

    if adapter is None:
        from app.market_data.service import create_market_data_adapter

        adapter = create_market_data_adapter(provider=settings.market_data_provider)

    c_path = Path(consumed_path) if consumed_path else CONSUMED_PATH
    consumed = load_consumed(c_path)
    before = len(consumed)
    summary = await feed_tv_paper(
        events=events,
        adapter=adapter,
        consumed_ids=consumed,
        allow_short=getattr(settings.alerts, "allow_short_technical", False),
        write=write,
    )
    if write and len(consumed) != before:
        save_consumed(consumed, c_path)
    summary["enabled"] = True
    summary["open_events"] = len(events)
    return summary
