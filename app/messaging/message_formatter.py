"""Telegram message formatters for NEWS, SIGNAL, EXCHANGE_RESPONSE.

Produces human-readable Telegram text and canonical JSON.
"""

from __future__ import annotations

import json

from app.messaging.message_models import (
    ExchangeAction,
    ExchangeResponse,
    NewsMessage,
    ResponseStatus,
    TradingSignal,
)


def format_news_telegram(msg: NewsMessage) -> str:
    lines = ["\U0001F4F0 *NEWS*", ""]
    if msg.source:
        lines.append(f"Source: {msg.source}")
    if msg.market:
        lines.append(f"Market: {msg.market}")
    if msg.symbol:
        lines.append(f"Symbol: `{msg.symbol}`")
    lines.append(f"Title: *{_escape_md(msg.title)}*")
    if msg.message:
        lines.append(f"Message: {_escape_md(msg.message)}")
    lines.append(f"Priority: {msg.priority.value.capitalize()}")
    lines.append(f"Timestamp: {msg.timestamp_utc}")
    return "\n".join(lines)


def format_signal_telegram(sig: TradingSignal) -> str:
    """Minimal, exchange-taugliches Signal-Format."""
    symbol = sig.display_symbol or sig.symbol
    lines = [f"\U0001F4E1 *SIGNAL* \u2014 `{symbol}`", ""]

    # Side & Direction auf einer Zeile
    lines.append(f"{sig.side.value.upper()} {sig.direction.value.upper()}")

    # Entry
    if sig.entry_type.value == "range" and sig.entry_min is not None and sig.entry_max is not None:
        lines.append(f"Entry: {sig.entry_min} \u2013 {sig.entry_max}")
    elif sig.entry_value is not None:
        lines.append(f"Entry: {sig.entry_value}")
    else:
        lines.append("Entry: MARKET")

    # Targets
    if sig.targets:
        lines.append(f"Targets: {', '.join(str(t) for t in sig.targets)}")

    # Stop Loss
    if sig.stop_loss is not None:
        lines.append(f"Stop Loss: {sig.stop_loss}")

    # Leverage
    if sig.leverage > 1:
        lines.append(f"Leverage: {sig.leverage}x")

    return "\n".join(lines)


def format_exchange_response_telegram(resp: ExchangeResponse) -> str:
    """Compact operator confirmation — no report, no signal echo."""
    symbol = resp.symbol or "\u2014"
    if resp.status == ResponseStatus.SUCCESS:
        return f"\u2705 *Ausgef\u00fchrt* \u2014 `{symbol}`"
    return f"\u26D4 *Nicht Ausgef\u00fchrt* \u2014 `{symbol}`"


def format_exchange_response_internal(resp: ExchangeResponse) -> str:
    """Verbose Exchange-Response for internal logs and reports."""
    emoji = _response_emoji(resp.action, resp.status)
    symbol = resp.symbol or "\u2014"
    lines = [f"{emoji} *RESPONSE* \u2014 `{symbol}`", ""]

    lines.append(f"{resp.action.value.upper()} | {resp.status.value.upper()}")

    if resp.entry_price is not None:
        lines.append(f"Entry: {resp.entry_price}")
    if resp.stop_loss is not None:
        lines.append(f"Stop Loss: {resp.stop_loss}")
    if resp.leverage is not None:
        lines.append(f"Leverage: {resp.leverage}x")

    if resp.exchange:
        lines.append(f"Exchange: {_exchange_display_name(resp.exchange)}")
    if resp.related_signal_id:
        lines.append(f"Signal: `{resp.related_signal_id}`")

    if resp.result:
        lines.append(f"Result: {resp.result}")
    if resp.realized_profit:
        lines.append(f"Profit: {resp.realized_profit}")
    if resp.error_code:
        lines.append(f"Error: {resp.error_code}")
    if resp.message:
        lines.append(f"Info: {_escape_md(resp.message)}")

    return "\n".join(lines)


def format_as_json(msg: NewsMessage | TradingSignal | ExchangeResponse) -> str:
    return json.dumps(msg.to_dict(), indent=2, ensure_ascii=False)


def _escape_md(text: str) -> str:
    return text.replace("`", "'").replace("*", "\\*")


_EXCHANGE_DISPLAY_NAMES: dict[str, str] = {
    "binance": "Binance",
    "binance_spot": "Binance Spot",
    "binance_futures": "Binance Futures",
    "bybit": "Bybit",
    "okx": "OKX",
    "bitget": "Bitget",
    "deribit": "Deribit",
    "kucoin": "KuCoin",
}


def _exchange_display_name(exchange_id: str) -> str:
    return _EXCHANGE_DISPLAY_NAMES.get(exchange_id.lower(), exchange_id)


def _response_emoji(action: ExchangeAction, status: ResponseStatus) -> str:
    if status == ResponseStatus.ERROR:
        return "\u274C"
    if action in {ExchangeAction.TAKE_PROFIT_HIT, ExchangeAction.POSITION_CLOSED}:
        return "\U0001F3AF"
    if action == ExchangeAction.STOP_LOSS_HIT:
        return "\U0001F6D1"
    if action in {ExchangeAction.FILLED, ExchangeAction.ORDER_CREATED}:
        return "\u2705"
    if action == ExchangeAction.REJECTED:
        return "\u26D4"
    return "\U0001F4CB"
