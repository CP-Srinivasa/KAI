"""Phase-0 Telegram-Commands für Live-Trading.

Spec: docs/security/kai_light_live_phase0_spec.md §6.

Public API:
    handle_live_unlock(text, engine) -> str
    handle_live_status(engine) -> str
    handle_live_lock(engine) -> str
    handle_trade(text, engine) -> str   # async

Pattern: jeder Handler parsed den Telegram-Text, ruft die Engine, gibt
Pretty-Print-Antwort zurück. KEINE Telegram-Bot-Library-Imports hier —
``telegram_bot.py`` ist der Caller und macht das Routing.

Phase-0 Skeleton:
- /trade braucht volle Args (symbol, side, qty, entry_price, sl_price, hotp).
  Der KAI-Bot-Signal-Replay-Pfad ("/trade BTCUSDT buy 0.001 <hotp>" mit
  vorher geposteter Signal-Empfehlung) kommt mit N+4/N+5.
- Phase 0: Spot-only, LIMIT-only, Notional = qty × price (USDT-Pair).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.execution.exchanges.base import OrderRequest, OrderSide, OrderType
from app.execution.live_engine import LiveExecutionEngine, LiveOrderOutcome
from app.security.hotp_auth import (
    HotpReplayDetected,
    HotpSeedInvalid,
    HotpSeedMissing,
    HotpVerificationFailed,
)
from app.security.live_caps import MAX_OPEN_POSITIONS, MAX_POSITION_USD

logger = logging.getLogger(__name__)


# --------------- /live unlock ---------------


@dataclass(frozen=True)
class LiveUnlockArgs:
    hotp_code: str


class LiveCommandError(Exception):
    """Caller bekommt String-Reply mit '❌ ' Prefix — niemals raise zum User."""


def _parse_live_unlock(text: str) -> LiveUnlockArgs:
    """Erwartet ``/live unlock <hotp>`` mit 6-stelligem Code."""
    parts = text.strip().split()
    if len(parts) != 3 or parts[0] != "/live" or parts[1] != "unlock":
        raise LiveCommandError(
            "Format: /live unlock <hotp> (6 Ziffern aus deiner Authenticator-App)"
        )
    code = parts[2]
    if len(code) != 6 or not code.isdigit():
        raise LiveCommandError(
            f"HOTP-Code muss 6 Ziffern sein, erhielt: '{code}'"
        )
    return LiveUnlockArgs(hotp_code=code)


def handle_live_unlock(text: str, engine: LiveExecutionEngine) -> str:
    """Telegram-Reply für ``/live unlock <hotp>``.

    Returns:
        Pretty-Print-Reply mit Status + Cap-Übersicht oder Error-Message.
    """
    try:
        args = _parse_live_unlock(text)
    except LiveCommandError as exc:
        return f"❌ {exc}"

    try:
        result = engine.unlock(args.hotp_code)
    except HotpSeedMissing as exc:
        logger.error("hotp_seed_missing telegram_unlock_fail %s", exc)
        return "❌ HOTP-Seed fehlt auf Pi. Operator-Setup-Runbook prüfen."
    except HotpSeedInvalid as exc:
        logger.error("hotp_seed_invalid telegram_unlock_fail %s", exc)
        return "❌ HOTP-Seed-File ist korrupt (kein base32). Re-Initialisierung nötig."
    except (HotpVerificationFailed, HotpReplayDetected) as exc:
        # Keine Detail-Info an User — Side-Channel-Schutz (Spec §2).
        logger.warning("hotp_verify_fail telegram_unlock_attempt detail=%s", exc)
        return "❌ HOTP-Code abgelehnt."
    except ValueError as exc:
        return f"❌ {exc}"

    status = engine.status()
    return (
        f"✅ Live-Mode aktiv\n"
        f"Counter: {result.counter_used} (advance +{result.counter_advance})\n"
        f"Idle-TTL: {status['idle_lock_remaining_s']}s\n"
        f"Cap: ${MAX_POSITION_USD:.0f}/Position, max {MAX_OPEN_POSITIONS} offen\n"
        f"Offen aktuell: {status['open_positions']}"
    )


# --------------- /live status ---------------


def handle_live_status(engine: LiveExecutionEngine) -> str:
    """Telegram-Reply für ``/live status``. Read-only, no auth needed."""
    s = engine.status()
    state_emoji = "🔓" if s["state"] == "unlocked" else "🔒"
    return (
        f"{state_emoji} Live-Mode: {s['state']}\n"
        f"Idle-Remaining: {s['idle_lock_remaining_s']}s\n"
        f"HOTP last: {s['hotp_last_counter']} / next: {s['hotp_next_expected']}\n"
        f"Offen: {s['open_positions']} / {MAX_OPEN_POSITIONS}\n"
        f"Versuche: {s['orders_attempted']} | Placed: {s['orders_placed']}\n"
        f"Cap: ${MAX_POSITION_USD:.0f}/Position"
    )


# --------------- /live lock ---------------


def handle_live_lock(engine: LiveExecutionEngine) -> str:
    """Telegram-Reply für ``/live lock`` — sofort, kein HOTP nötig."""
    engine.lock()
    return "🔒 Live-Mode locked. /live unlock <hotp> zum Re-Aktivieren."


# --------------- /trade ---------------


@dataclass(frozen=True)
class TradeArgs:
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    stop_loss: float
    hotp_code: str
    exchange: str  # "binance" | "bybit"


def _parse_trade(text: str) -> TradeArgs:
    """Erwartet ``/trade <SYMBOL> <side> <qty> <price> <sl> <hotp> [exchange]``.

    Phase-0 Skeleton — voller Args-Set. Späterer Sprint baut den
    Signal-Replay-Pfad (kurzer ``/trade <sym> <side> <qty> <hotp>`` der
    Entry+SL aus dem zuletzt geposteten KAI-Signal nimmt).

    Default exchange = "binance" wenn nicht angegeben.
    """
    parts = text.strip().split()
    if len(parts) < 7 or parts[0] != "/trade":
        raise LiveCommandError(
            "Format: /trade <SYM> <buy|sell> <qty> <entry> <sl> <hotp> [exchange]\n"
            "Beispiel: /trade BTCUSDT buy 0.001 80100 78500 384733 binance"
        )

    _, sym, side_str, qty_str, price_str, sl_str, hotp = parts[:7]
    exchange = parts[7].lower() if len(parts) > 7 else "binance"

    if side_str.lower() not in {"buy", "sell"}:
        raise LiveCommandError(f"side muss 'buy' oder 'sell' sein, erhielt: '{side_str}'")
    try:
        qty = float(qty_str)
        entry = float(price_str)
        sl = float(sl_str)
    except ValueError as exc:
        raise LiveCommandError(f"qty/price/sl müssen Zahlen sein: {exc}") from exc

    if qty <= 0 or entry <= 0 or sl <= 0:
        raise LiveCommandError(
            f"qty/price/sl müssen > 0 sein: qty={qty} entry={entry} sl={sl}"
        )

    if len(hotp) != 6 or not hotp.isdigit():
        raise LiveCommandError(f"HOTP-Code muss 6 Ziffern sein, erhielt: '{hotp}'")

    if exchange not in {"binance", "bybit"}:
        raise LiveCommandError(
            f"exchange muss 'binance' oder 'bybit' sein, erhielt: '{exchange}'"
        )

    return TradeArgs(
        symbol=sym.upper(),
        side=OrderSide.BUY if side_str.lower() == "buy" else OrderSide.SELL,
        quantity=qty,
        entry_price=entry,
        stop_loss=sl,
        hotp_code=hotp,
        exchange=exchange,
    )


def _format_outcome(outcome: LiveOrderOutcome, args: TradeArgs) -> str:
    """Pretty-Print eines ``LiveOrderOutcome`` als Telegram-Reply."""
    notional = args.quantity * args.entry_price
    if outcome.success and outcome.exchange_result:
        ex = outcome.exchange_result
        return (
            f"✅ Live-Order placed @ {args.exchange}\n"
            f"{args.symbol} {args.side.upper()} {args.quantity} @ {args.entry_price}\n"
            f"SL: {ex.sl_price} (id {ex.sl_order_id})\n"
            f"Order-ID: {ex.order_id}\n"
            f"Notional: ${notional:.2f}\n"
            f"HOTP-Counter: {outcome.hotp_counter}\n"
            f"Audit: {outcome.audit_id}"
        )
    # Rejected — zeige Gate-Spur (außer hotp-fail-detail, Side-Channel-Schutz)
    failed_gate = next((g.name for g in outcome.gates if not g.passed), "pre_check")
    safe_detail = outcome.reject_reason
    if failed_gate == "hotp":
        safe_detail = "hotp_failed"  # kein Counter-Hint nach außen
    return (
        f"❌ Live-Order rejected\n"
        f"Gate: {failed_gate}\n"
        f"Reason: {safe_detail}\n"
        f"Audit: {outcome.audit_id}"
    )


async def handle_trade(text: str, engine: LiveExecutionEngine) -> str:
    """Telegram-Reply für ``/trade ...``. Geht durch alle 5 Gates der Engine.

    Phase-0: nur LIMIT-Orders, nur Spot, notional = qty × price (USDT-Pair).
    Signal-Confidence + Confluence-Count sind Caller-Defaults (Risk-Engine-Pflicht):
    confidence=1.0 (Operator-Confirmed = höchste), confluence=1 (manual-Trade).
    """
    try:
        args = _parse_trade(text)
    except LiveCommandError as exc:
        return f"❌ {exc}"

    notional_usd = args.quantity * args.entry_price
    order = OrderRequest(
        symbol=args.symbol,
        side=args.side,
        order_type=OrderType.LIMIT,
        quantity=args.quantity,
        price=args.entry_price,
        stop_loss=args.stop_loss,
        client_order_id=f"tg-trade-{int(notional_usd * 100)}",
    )

    outcome = await engine.submit_live_order(
        order,
        hotp_code=args.hotp_code,
        signal_confidence=1.0,  # Operator-Confirmed = höchste
        signal_confluence_count=1,  # manual trade
        exchange=args.exchange,
        notional_usd=notional_usd,
    )

    return _format_outcome(outcome, args)
