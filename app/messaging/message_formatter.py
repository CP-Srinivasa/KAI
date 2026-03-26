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
    lines = ["\U0001F4E1 *SIGNAL*", ""]
    lines.append(f"ID: `{sig.signal_id}`")
    lines.append(f"Type: {sig.market_type.value.capitalize()}")
    lines.append(f"Symbol: `{sig.display_symbol or sig.symbol}`")
    lines.append(f"Side: {sig.side.value.upper()}")
    lines.append(f"Direction: {sig.direction.value.upper()}")
    lines.append("")

    if sig.entry_type.value == "range" and sig.entry_min is not None and sig.entry_max is not None:
        lines.append(f"Entry Rule: RANGE {sig.entry_min} - {sig.entry_max}")
    elif sig.entry_value is not None:
        lines.append(f"Entry Rule: {sig.entry_type.value.upper()} {sig.entry_value}")
    else:
        lines.append(f"Entry Rule: {sig.entry_type.value.upper()}")

    if sig.targets:
        lines.append(f"Targets: {', '.join(str(target) for target in sig.targets)}")
    if sig.stop_loss is not None:
        lines.append(f"Stop Loss: {sig.stop_loss}")
    if sig.leverage > 1:
        lines.append(f"Leverage: {sig.leverage}x")
    lines.append("")

    if sig.exchange_scope:
        scope = ", ".join(_exchange_display_name(exchange) for exchange in sig.exchange_scope)
        lines.append(f"Exchange Scope: {scope}")
    lines.append(f"Status: {sig.status.value.upper()}")

    if sig.notes:
        lines.append(f"Note: {_escape_md(sig.notes)}")

    return "\n".join(lines)


def format_exchange_response_telegram(resp: ExchangeResponse) -> str:
    emoji = _response_emoji(resp.action, resp.status)
    lines = [f"{emoji} *EXCHANGE RESPONSE*", ""]
    if resp.related_signal_id:
        lines.append(f"Related Signal ID: `{resp.related_signal_id}`")
    lines.append(f"Exchange: {_exchange_display_name(resp.exchange)}")
    lines.append(f"Symbol: `{resp.symbol}`")
    lines.append(f"Action: {resp.action.value.upper()}")
    lines.append(f"Status: {resp.status.value.upper()}")
    lines.append("")

    if resp.entry_price is not None:
        lines.append(f"Entry Price: {resp.entry_price}")
    if resp.quantity is not None:
        lines.append(f"Quantity: {resp.quantity}")
    if resp.leverage is not None:
        lines.append(f"Leverage: {resp.leverage}x")
    if resp.stop_loss is not None:
        lines.append(f"Stop Loss: {resp.stop_loss}")
    if resp.take_profit is not None:
        lines.append(f"Take Profit: {resp.take_profit}")
    if resp.exchange_order_id:
        lines.append(f"Order ID: `{resp.exchange_order_id}`")

    if resp.result:
        lines.append(f"Result: {resp.result}")
    if resp.realized_profit:
        lines.append(f"Realized Profit: {resp.realized_profit}")
    if resp.error_code:
        lines.append(f"Error Code: {resp.error_code}")
    if resp.message:
        lines.append(f"Message: {_escape_md(resp.message)}")

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
