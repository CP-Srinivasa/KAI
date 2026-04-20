"""Telegram signal/message parser.

Supports two parsing layers:
- legacy command parsing for `/signal BUY BTC 65000 SL=...`
- structured parsing for NEWS / SIGNAL / EXCHANGE_RESPONSE blocks
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.messaging.message_models import (
        ExchangeResponse,
        NewsMessage,
        TradingSignal,
    )


class SignalDirection(StrEnum):
    BUY = "buy"
    SELL = "sell"


_DIRECTION_MAP: dict[str, SignalDirection] = {
    "buy": SignalDirection.BUY,
    "long": SignalDirection.BUY,
    "kaufen": SignalDirection.BUY,
    "sell": SignalDirection.SELL,
    "short": SignalDirection.SELL,
    "verkaufen": SignalDirection.SELL,
}

_KV_PATTERN = re.compile(r"(SL|TP|SIZE|STOP|TAKE)\s*=\s*([\d.,]+)", re.IGNORECASE)
_FIELD_PATTERN = re.compile(r"^([A-Za-z][A-Za-z0-9_ ]*?)\s*:\s*(.+)$", re.MULTILINE)

# Heuristic crypto-signal detection (free-form pastes from Telegram groups etc.).
# Matches symbols like "#BTC/USDT", "BTC/USDT", "BTC-USDT" (quote ∈ known set).
_HEURISTIC_SYMBOL_PATTERN = re.compile(
    r"#?\s*([A-Za-z][A-Za-z0-9]{1,9})\s*[/\-]\s*(USDT|USDC|USD|EUR|BTC|ETH|BUSD|DAI|FDUSD)\b",
    re.IGNORECASE,
)
_HEURISTIC_DIRECTION_PATTERN = re.compile(
    r"\b(LONG\s*/\s*BUY|BUY\s*/\s*LONG|SHORT\s*/\s*SELL|SELL\s*/\s*SHORT|LONG|SHORT|BUY|SELL)\b",
    re.IGNORECASE,
)
_HEURISTIC_NUM = r"\d+(?:[.,]\d+)?"

_HEURISTIC_EXCHANGES = (
    "binance_futures", "binance", "bybit", "okx", "deribit", "bitget",
    "kucoin", "huobi", "blofin", "bingx", "mexc", "gate", "gateio",
)

# Validation errors the operator can fix by supplying a single field.
# Anything outside this map is a hard structural problem (missing symbol,
# invalid direction, bad entry) that requires re-pasting the signal.
COMPLETABLE_FIELD_FOR_ERROR: dict[str, str] = {
    "missing_exchange_scope": "exchange_scope",
    "missing_stop_loss": "stop_loss",
    "missing_targets": "targets",
    "missing_source": "source",
    "invalid_leverage": "leverage",
}


def split_validation_errors(errors: list[str]) -> tuple[list[str], list[str]]:
    """Split validation errors into (completable_fields, blocking_errors).

    Completable errors map to fields the operator can supply without
    re-pasting the whole signal. Blocking errors require a new paste.
    """
    completable: list[str] = []
    blocking: list[str] = []
    for err in errors:
        field = COMPLETABLE_FIELD_FOR_ERROR.get(err)
        if field and field not in completable:
            completable.append(field)
        elif not field:
            blocking.append(err)
    return completable, blocking


@dataclass(frozen=True)
class ParsedSignal:
    """Parsed legacy trading signal from Telegram command text."""

    direction: SignalDirection
    asset: str
    price: float | None
    stop_loss: float | None
    take_profit: float | None
    size: float | None

    @property
    def is_market_order(self) -> bool:
        return self.price is None


class SignalParseError(Exception):
    """Raised when a signal message cannot be parsed."""


def _parse_float(value: str) -> float | None:
    cleaned = value.strip().replace(",", ".")
    try:
        result = float(cleaned)
    except ValueError:
        return None
    return result if result > 0 else None


def parse_signal_message(text: str) -> ParsedSignal:
    """Parse legacy signal format: DIRECTION ASSET [PRICE] [SL=x] [TP=x] [SIZE=x]."""
    if not text or not text.strip():
        raise SignalParseError("Empty signal message")

    params: dict[str, float] = {}
    for match in _KV_PATTERN.finditer(text):
        key = match.group(1).upper()
        value = _parse_float(match.group(2))
        if value is None:
            continue
        if key in {"SL", "STOP"}:
            params["stop_loss"] = value
        elif key in {"TP", "TAKE"}:
            params["take_profit"] = value
        elif key == "SIZE":
            params["size"] = value

    clean_text = _KV_PATTERN.sub("", text).strip()
    tokens = clean_text.upper().split()
    if len(tokens) < 2:
        raise SignalParseError(
            f"Signal requires at minimum DIRECTION and ASSET. Got: '{text}'"
        )

    direction_token = tokens[0].lower()
    direction_parts = [part for part in direction_token.split("/") if part]
    direction: SignalDirection | None = None
    for part in direction_parts or [direction_token]:
        direction = _DIRECTION_MAP.get(part)
        if direction is not None:
            break
    if direction is None:
        raise SignalParseError(
            f"Unknown direction '{tokens[0]}'. Expected: {', '.join(sorted(_DIRECTION_MAP.keys()))}"
        )

    asset = tokens[1].upper().lstrip("#$")
    if not asset.isalpha() or len(asset) < 2 or len(asset) > 10:
        raise SignalParseError(
            f"Invalid asset '{asset}'. Must be 2-10 alphabetic characters."
        )

    price: float | None = None
    if len(tokens) >= 3:
        price = _parse_float(tokens[2])

    return ParsedSignal(
        direction=direction,
        asset=asset,
        price=price,
        stop_loss=params.get("stop_loss"),
        take_profit=params.get("take_profit"),
        size=params.get("size"),
    )


def _detect_bracket_header(text: str) -> str | None:
    """Detect explicit structured header from the first non-empty line."""
    first_line = ""
    for line in text.splitlines():
        if line.strip():
            first_line = line.strip()
            break
    if not first_line:
        return None

    bracket_match = re.match(
        r"^\[?\s*(NEWS|SIGNAL|EXCHANGE[\s_]?RESPONSE)\s*\]?\s*$",
        first_line,
        re.IGNORECASE,
    )
    if bracket_match:
        raw = bracket_match.group(1).strip().lower().replace(" ", "_")
        return raw if raw in {"news", "signal", "exchange_response"} else None

    upper_tokens = first_line.upper().replace("[", " ").replace("]", " ").split()
    if len(upper_tokens) >= 2 and upper_tokens[1] in {"NEWS", "SIGNAL"}:
        return upper_tokens[1].lower()
    if (
        len(upper_tokens) >= 3
        and upper_tokens[1] == "EXCHANGE"
        and upper_tokens[2] == "RESPONSE"
    ):
        return "exchange_response"

    return None


def _looks_like_crypto_signal(text: str) -> bool:
    """Heuristic: free-form text is treated as a SIGNAL if it contains both
    a crypto pair symbol (e.g. BTC/USDT) and a direction keyword (LONG/BUY/…).
    """
    if not text:
        return False
    return bool(
        _HEURISTIC_SYMBOL_PATTERN.search(text)
        and _HEURISTIC_DIRECTION_PATTERN.search(text)
    )


def detect_message_type(text: str) -> str | None:
    """Detect message type.

    1. Explicit bracket header (`[SIGNAL]` / `📡 SIGNAL` / …) wins.
    2. Fallback: if the body contains a crypto pair + direction keyword,
       treat as "signal" (covers free-form Telegram-group pastes).
    """
    bracket = _detect_bracket_header(text)
    if bracket is not None:
        return bracket
    if _looks_like_crypto_signal(text):
        return "signal"
    return None


def _parse_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _FIELD_PATTERN.finditer(text):
        key = match.group(1).strip().lower().replace(" ", "_")
        value = match.group(2).strip()
        fields[key] = value
    return fields


def _parse_targets(value: str) -> list[float]:
    cleaned = value.strip()
    if not cleaned:
        return []

    if cleaned.startswith("[") and cleaned.endswith("]"):
        try:
            loaded = json.loads(cleaned)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, list):
            parsed_list: list[float] = []
            for item in loaded:
                parsed = _parse_float(str(item))
                if parsed is not None:
                    parsed_list.append(parsed)
            return parsed_list

    result: list[float] = []
    for token in re.findall(r"\d+(?:[.,]\d+)?", cleaned):
        parsed = _parse_float(token)
        if parsed is not None:
            result.append(parsed)
    return result


def _normalize_exchange_id(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _parse_exchange_scope(value: str) -> list[str]:
    cleaned = value.strip()
    if not cleaned:
        return []

    parts: list[str]
    if cleaned.startswith("[") and cleaned.endswith("]"):
        try:
            loaded = json.loads(cleaned)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, list):
            parts = [str(item) for item in loaded]
        else:
            parts = re.split(r"[,;]+", cleaned.strip("[]"))
    else:
        parts = re.split(r"[,;]+", cleaned)

    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = _normalize_exchange_id(part.strip("\"'"))
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def _parse_entry_rule(value: str) -> tuple[str, float | None, float | None, float | None]:
    """Parse entry rule into (entry_type, entry_value, entry_min, entry_max)."""
    from app.messaging.message_models import EntryType

    parts = value.strip().upper().split()
    if not parts:
        return EntryType.MARKET.value, None, None, None

    entry_type_str = parts[0].lower()
    valid_types = {e.value for e in EntryType}
    if entry_type_str not in valid_types:
        maybe_number = _parse_float(parts[0])
        if maybe_number is not None:
            return EntryType.AT.value, maybe_number, None, None
        return EntryType.MARKET.value, None, None, None

    numbers: list[float] = []
    for number_text in re.findall(r"\d+(?:[.,]\d+)?", " ".join(parts[1:])):
        parsed = _parse_float(number_text)
        if parsed is not None:
            numbers.append(parsed)

    if entry_type_str == EntryType.RANGE.value and len(numbers) >= 2:
        return entry_type_str, None, numbers[0], numbers[1]
    if numbers:
        return entry_type_str, numbers[0], None, None
    return entry_type_str, None, None, None


def _side_from_str(value: str) -> str:
    v = value.strip().lower()
    if v in {"buy", "long", "kaufen"}:
        return "buy"
    if v in {"sell", "short", "verkaufen"}:
        return "sell"
    return v


def _direction_from_str(value: str) -> str:
    v = value.strip().lower()
    if v in {"long", "buy", "bullish", "up"}:
        return "long"
    if v in {"short", "sell", "bearish", "down"}:
        return "short"
    if v in {"neutral", "flat", "sideways"}:
        return "neutral"
    return v


def _parse_leverage(value: str) -> int | None:
    cleaned = value.lower().replace("x", "").strip()
    if not cleaned:
        return None
    try:
        parsed = int(float(cleaned))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _normalize_symbol(raw_symbol: str) -> tuple[str, str]:
    raw = raw_symbol.strip().upper().replace(" ", "")
    if not raw:
        return "", ""

    if "/" in raw:
        base, quote = raw.split("/", 1)
        base = base.strip()
        quote = quote.strip()
        if base and quote:
            return f"{base}{quote}", f"{base}/{quote}"

    if "-" in raw:
        base, quote = raw.split("-", 1)
        base = base.strip()
        quote = quote.strip()
        if base and quote:
            return f"{base}{quote}", f"{base}/{quote}"

    for quote in ("USDT", "USD", "EUR", "BTC", "ETH"):
        if raw.endswith(quote) and len(raw) > len(quote):
            base = raw[: -len(quote)]
            return raw, f"{base}/{quote}"

    cleaned = re.sub(r"[^A-Z0-9]", "", raw)
    return cleaned, raw


_ENUM_ALIASES: dict[str, dict[str, str]] = {
    "ExchangeAction": {
        "order_rejected": "rejected",
        "order_failed": "error",
        "order_filled": "filled",
        "position_exit": "position_closed",
    },
    "EntryType": {
        "entry_point": "at",
        "buy_now": "market",
        "sell_now": "market",
    },
    "MarketType": {
        "future": "futures",
        "perp": "futures",
        "perpetual": "futures",
    },
    "Direction": {
        "bullish": "long",
        "bearish": "short",
    },
}


def _enum_or_default(enum_cls: type, value: str, default: object = None) -> object:
    if not value:
        return default
    normalized = value.strip().lower()
    alias_map = _ENUM_ALIASES.get(getattr(enum_cls, "__name__", ""), {})
    normalized = alias_map.get(normalized, normalized)
    for member in enum_cls:
        if member.value == normalized:
            return member
    return default


def _extract_heuristic_signal(text: str) -> dict[str, object] | None:
    """Extract signal fields from free-form text (no explicit header).

    Handles common Telegram-group formats:
    - "🟢 #BTC/USDT LONG/BUY" with "Entry Zone: 70565 – 70590", "🎯 …"
    - "Long/Buy #ENJ/USDT" with "Entry Point - 7570", "Targets: X - Y - Z"
    - "#SOL/USDT Long/BUY - 84.20" with "Targets : X", "Stop Loss : Y"
    """
    sym_match = _HEURISTIC_SYMBOL_PATTERN.search(text)
    if sym_match is None:
        return None

    base = sym_match.group(1).upper()
    quote = sym_match.group(2).upper()
    internal_symbol = f"{base}{quote}"
    display_symbol = f"{base}/{quote}"

    # Direction: prefer the token on the same line as the symbol.
    direction_str: str | None = None
    side_str: str | None = None
    symbol_line: str | None = None
    for line in text.splitlines():
        if _HEURISTIC_SYMBOL_PATTERN.search(line):
            symbol_line = line
            dmatch = _HEURISTIC_DIRECTION_PATTERN.search(line)
            if dmatch:
                token = dmatch.group(1).upper().replace(" ", "")
                if "LONG" in token or "BUY" in token:
                    direction_str, side_str = "long", "buy"
                else:
                    direction_str, side_str = "short", "sell"
            break
    if direction_str is None:
        dmatch = _HEURISTIC_DIRECTION_PATTERN.search(text)
        if dmatch is None:
            return None
        token = dmatch.group(1).upper().replace(" ", "")
        if "LONG" in token or "BUY" in token:
            direction_str, side_str = "long", "buy"
        else:
            direction_str, side_str = "short", "sell"

    # Entry — range ("Entry Zone: 70565 – 70590") or single price.
    entry_type_str = "market"
    entry_value: float | None = None
    entry_min: float | None = None
    entry_max: float | None = None

    range_match = re.search(
        rf"entry[\s_]*(?:zone|range)?\s*[:\-]\s*({_HEURISTIC_NUM})\s*[–\-]\s*({_HEURISTIC_NUM})",
        text,
        re.IGNORECASE,
    )
    if range_match:
        lo = _parse_float(range_match.group(1))
        hi = _parse_float(range_match.group(2))
        if lo is not None and hi is not None:
            if lo > hi:
                lo, hi = hi, lo
            entry_min, entry_max = lo, hi
            entry_type_str = "range"

    if entry_type_str == "market":
        point_match = re.search(
            rf"entry(?:\s*point|\s*price)?\s*[:\-]\s*({_HEURISTIC_NUM})",
            text,
            re.IGNORECASE,
        )
        if point_match:
            val = _parse_float(point_match.group(1))
            if val is not None:
                entry_value = val
                entry_type_str = "at"

    # Inline direction + price on symbol line, e.g. "#SOL/USDT Long/BUY - 84.20".
    if entry_type_str == "market" and symbol_line:
        dmatch = _HEURISTIC_DIRECTION_PATTERN.search(symbol_line)
        if dmatch:
            tail = symbol_line[dmatch.end():]
            tail_match = re.search(rf"[-–:]\s*({_HEURISTIC_NUM})", tail)
            if tail_match:
                val = _parse_float(tail_match.group(1))
                if val is not None:
                    entry_value = val
                    entry_type_str = "at"

    # Targets: emoji bullets first, then labelled line ("Targets: X - Y - Z").
    targets: list[float] = []
    for line in text.splitlines():
        tmatch = re.search(rf"🎯\s*({_HEURISTIC_NUM})", line)
        if tmatch:
            val = _parse_float(tmatch.group(1))
            if val is not None:
                targets.append(val)

    if not targets:
        label_match = re.search(
            r"targets?\s*[:\-]\s*([^\n]+)",
            text,
            re.IGNORECASE,
        )
        if label_match:
            chunk = label_match.group(1)
            for token in re.findall(_HEURISTIC_NUM, chunk):
                val = _parse_float(token)
                if val is not None:
                    targets.append(val)

    # Stop loss.
    stop_loss: float | None = None
    sl_match = re.search(
        rf"(?:stop\s*loss|stoploss|\bsl\b|\bstop\b)\s*[:\-]\s*({_HEURISTIC_NUM})",
        text,
        re.IGNORECASE,
    )
    if sl_match:
        stop_loss = _parse_float(sl_match.group(1))

    # Leverage — first integer after "Leverage", ignores trailing noise
    # like "10x (Recommended)15".
    leverage = 1
    lev_match = re.search(
        r"leverage\s*[:\-]?\s*(\d{1,3})",
        text,
        re.IGNORECASE,
    )
    if lev_match:
        try:
            parsed_lev = int(lev_match.group(1))
            if 1 <= parsed_lev <= 125:
                leverage = parsed_lev
        except ValueError:
            pass

    # Exchange scope — scan for known venue names (first 5 lines typical).
    # Match "binance futures" → binance_futures before plain "binance".
    exchange_scope: list[str] = []
    seen: set[str] = set()
    joined = " ".join(text.splitlines()[:5]).lower()
    for venue in _HEURISTIC_EXCHANGES:
        token = venue.replace("_", " ")  # "binance_futures" → "binance futures"
        if token in joined and venue not in seen:
            exchange_scope.append(venue)
            seen.add(venue)
    # If specific "X_futures" already captured, drop the plain "X" duplicate.
    for venue in list(exchange_scope):
        if venue.endswith("_futures"):
            plain = venue[: -len("_futures")]
            if plain in seen:
                exchange_scope.remove(plain)
                seen.discard(plain)

    # Kein stiller Exchange-Default: wenn kein Venue im Text erkannt wurde,
    # bleibt exchange_scope leer. Der API-/Telegram-Handler muss beim
    # Operator explizit nachfragen, bevor das Signal akzeptiert/übertragen
    # wird. Silent defaults für ausführungsrelevante Felder sind verboten.

    return {
        "internal_symbol": internal_symbol,
        "display_symbol": display_symbol,
        "side": side_str,
        "direction": direction_str,
        "entry_type": entry_type_str,
        "entry_value": entry_value,
        "entry_min": entry_min,
        "entry_max": entry_max,
        "targets": targets,
        "stop_loss": stop_loss,
        "leverage": leverage,
        "exchange_scope": exchange_scope,
    }


def parse_structured_message(
    text: str,
) -> NewsMessage | TradingSignal | ExchangeResponse:
    """Parse structured Telegram message with NEWS/SIGNAL/EXCHANGE_RESPONSE header."""
    from app.messaging.message_models import (
        Direction,
        EntryType,
        ExchangeAction,
        ExchangeResponse,
        MarketType,
        NewsMessage,
        Priority,
        ResponseStatus,
        RiskMode,
        Side,
        SignalStatus,
        TradingSignal,
        _generate_response_id,
        _generate_signal_id,
    )

    msg_type = detect_message_type(text)
    if msg_type is None:
        raise SignalParseError(
            "Kein Signal erkannt. Erwartet: Header [NEWS] / [SIGNAL] / "
            "[EXCHANGE_RESPONSE] ODER freier Text mit Paar (z. B. BTC/USDT) "
            "und Richtung (LONG/SHORT/BUY/SELL)."
        )

    # Free-form signal without bracket header → heuristic path.
    if msg_type == "signal" and _detect_bracket_header(text) is None:
        heur = _extract_heuristic_signal(text)
        if heur is None:
            raise SignalParseError(
                "Signal-Heuristik konnte Paar/Richtung nicht extrahieren."
            )
        signal_id = _generate_signal_id(str(heur["internal_symbol"]) or "UNKNOWN")
        return TradingSignal(
            signal_id=signal_id,
            source="external_paste",
            exchange_scope=list(heur["exchange_scope"]),  # type: ignore[arg-type]
            market_type=MarketType.FUTURES,
            symbol=str(heur["internal_symbol"]),
            display_symbol=str(heur["display_symbol"]),
            side=_enum_or_default(Side, str(heur["side"] or "buy"), Side.BUY),
            direction=_enum_or_default(
                Direction, str(heur["direction"] or "long"), Direction.LONG
            ),
            entry_type=_enum_or_default(
                EntryType, str(heur["entry_type"]), EntryType.MARKET
            ),
            entry_value=heur["entry_value"],  # type: ignore[arg-type]
            entry_min=heur["entry_min"],  # type: ignore[arg-type]
            entry_max=heur["entry_max"],  # type: ignore[arg-type]
            targets=list(heur["targets"]),  # type: ignore[arg-type]
            stop_loss=heur["stop_loss"],  # type: ignore[arg-type]
            leverage=int(heur["leverage"]) if heur["leverage"] else 1,
            risk_mode=RiskMode.ISOLATED,
            status=SignalStatus.NEW,
            timestamp_utc=datetime.now(UTC).isoformat(),
            notes="heuristic_parse",
        )

    fields = _parse_fields(text)

    if msg_type == "news":
        timestamp_value = fields.get("timestamp", fields.get("timestamp_utc", "")).strip()
        return NewsMessage(
            source=fields.get("source", ""),
            title=fields.get("title", ""),
            message=fields.get("message", ""),
            market=fields.get("market", ""),
            symbol=fields.get("symbol", ""),
            priority=_enum_or_default(Priority, fields.get("priority", ""), Priority.MEDIUM),
            timestamp_utc=timestamp_value or datetime.now(UTC).isoformat(),
        )

    if msg_type == "signal":
        symbol_raw = fields.get("symbol", fields.get("asset", ""))
        internal_symbol, display_symbol = _normalize_symbol(symbol_raw)

        entry_rule = fields.get("entry_rule", fields.get("entry_type", ""))
        entry_type_str, entry_value, entry_min, entry_max = (
            _parse_entry_rule(entry_rule)
            if entry_rule
            else (EntryType.MARKET.value, None, None, None)
        )
        if entry_value is None:
            entry_value = _parse_float(fields.get("entry_value", fields.get("entry_price", "")))

        side_str = _side_from_str(fields.get("side", "buy"))
        direction_str = _direction_from_str(
            fields.get("direction", "long" if side_str == "buy" else "short")
        )

        signal_id = fields.get("signal_id", fields.get("id", ""))
        if not signal_id:
            signal_id = _generate_signal_id(internal_symbol or "UNKNOWN")
        timestamp_value = fields.get("timestamp", fields.get("timestamp_utc", "")).strip()

        return TradingSignal(
            signal_id=signal_id,
            source=fields.get("source", ""),
            exchange_scope=_parse_exchange_scope(fields.get("exchange_scope", "")),
            market_type=_enum_or_default(
                MarketType,
                fields.get("market_type", fields.get("type", "")),
                MarketType.FUTURES,
            ),
            symbol=internal_symbol,
            display_symbol=display_symbol,
            side=_enum_or_default(Side, side_str, Side.BUY),
            direction=_enum_or_default(Direction, direction_str, Direction.LONG),
            entry_type=_enum_or_default(EntryType, entry_type_str, EntryType.MARKET),
            entry_value=entry_value,
            entry_min=entry_min,
            entry_max=entry_max,
            targets=_parse_targets(fields.get("targets", "")),
            stop_loss=_parse_float(fields.get("stop_loss", fields.get("sl", ""))),
            leverage=_parse_leverage(fields.get("leverage", "")) or 1,
            risk_mode=_enum_or_default(
                RiskMode,
                fields.get("risk_mode", fields.get("margin_mode", "")),
                RiskMode.ISOLATED,
            ),
            status=_enum_or_default(SignalStatus, fields.get("status", ""), SignalStatus.NEW),
            timestamp_utc=timestamp_value or datetime.now(UTC).isoformat(),
            notes=fields.get("note", fields.get("notes", "")),
            confidence=_parse_float(fields.get("confidence", "")),
            strategy_tag=fields.get("strategy_tag", fields.get("strategy", "")),
        )

    response_id = fields.get("response_id", fields.get("id", ""))
    normalized_symbol = fields.get("symbol", "")
    if not response_id:
        response_id = _generate_response_id(normalized_symbol or "UNKNOWN")
    timestamp_value = fields.get("timestamp", fields.get("timestamp_utc", "")).strip()

    return ExchangeResponse(
        response_id=response_id,
        related_signal_id=fields.get("related_signal_id", ""),
        exchange=_normalize_exchange_id(fields.get("exchange", "")),
        symbol=normalized_symbol,
        market_type=_enum_or_default(
            MarketType,
            fields.get("market_type", ""),
            MarketType.FUTURES,
        ),
        action=_enum_or_default(
            ExchangeAction,
            fields.get("action", ""),
            ExchangeAction.RECEIVED,
        ),
        status=_enum_or_default(
            ResponseStatus,
            fields.get("status", ""),
            ResponseStatus.PENDING,
        ),
        order_side=_enum_or_default(Side, fields.get("order_side", ""), None),
        position_side=_enum_or_default(Direction, fields.get("position_side", ""), None),
        entry_price=_parse_float(fields.get("entry_price", "")),
        order_type=fields.get("order_type", ""),
        quantity=_parse_float(fields.get("quantity", "")),
        leverage=_parse_leverage(fields.get("leverage", "")),
        stop_loss=_parse_float(fields.get("stop_loss", "")),
        take_profit=_parse_float(fields.get("take_profit", "")),
        exchange_order_id=fields.get("exchange_order_id", fields.get("order_id", "")),
        result=fields.get("result", ""),
        realized_profit=fields.get("realized_profit", ""),
        error_code=fields.get("error_code", ""),
        message=fields.get("message", ""),
        timestamp_utc=timestamp_value or datetime.now(UTC).isoformat(),
    )
