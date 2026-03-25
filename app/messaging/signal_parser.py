"""Telegram signal message parser.

Parses structured trading signal messages from Telegram into
a typed ParsedSignal dataclass. Supports multiple input formats.

Examples:
    /signal BUY BTC 65000 SL=62000 TP=70000
    /signal SELL ETH 3400
    /signal LONG SOL SL=120 TP=200 SIZE=0.5
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SignalDirection(StrEnum):
    BUY = "buy"
    SELL = "sell"


# Direction aliases → canonical direction
_DIRECTION_MAP: dict[str, SignalDirection] = {
    "buy": SignalDirection.BUY,
    "long": SignalDirection.BUY,
    "kaufen": SignalDirection.BUY,
    "sell": SignalDirection.SELL,
    "short": SignalDirection.SELL,
    "verkaufen": SignalDirection.SELL,
}

# Extract key=value params (SL=62000, TP=70000, SIZE=0.5)
_KV_PATTERN = re.compile(r"(SL|TP|SIZE|STOP|TAKE)\s*=\s*([\d.,]+)", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedSignal:
    """Parsed trading signal from Telegram message."""

    direction: SignalDirection
    asset: str  # e.g. "BTC", "ETH", "SOL"
    price: float | None  # entry price (None = market order)
    stop_loss: float | None
    take_profit: float | None
    size: float | None  # position size (None = use default)

    @property
    def is_market_order(self) -> bool:
        return self.price is None


class SignalParseError(Exception):
    """Raised when a signal message cannot be parsed."""


def _parse_float(value: str) -> float | None:
    """Parse a float from a string, handling comma as decimal separator."""
    cleaned = value.strip().replace(",", ".")
    try:
        result = float(cleaned)
        return result if result > 0 else None
    except ValueError:
        return None


def parse_signal_message(text: str) -> ParsedSignal:
    """Parse a Telegram signal message into a structured ParsedSignal.

    Expected format: DIRECTION ASSET [PRICE] [SL=x] [TP=x] [SIZE=x]

    Args:
        text: Raw signal text (without /signal prefix)

    Returns:
        ParsedSignal with all parsed fields

    Raises:
        SignalParseError: If the message cannot be parsed
    """
    if not text or not text.strip():
        raise SignalParseError("Empty signal message")

    # Extract key=value parameters first, then remove them from text
    params: dict[str, float] = {}
    for match in _KV_PATTERN.finditer(text):
        key = match.group(1).upper()
        value = _parse_float(match.group(2))
        if value is not None:
            if key in {"SL", "STOP"}:
                params["stop_loss"] = value
            elif key in {"TP", "TAKE"}:
                params["take_profit"] = value
            elif key == "SIZE":
                params["size"] = value

    # Remove key=value pairs from text for positional parsing
    clean_text = _KV_PATTERN.sub("", text).strip()
    tokens = clean_text.upper().split()

    if len(tokens) < 2:
        raise SignalParseError(
            f"Signal requires at minimum DIRECTION and ASSET. Got: '{text}'"
        )

    # Parse direction
    direction_str = tokens[0].lower()
    direction = _DIRECTION_MAP.get(direction_str)
    if direction is None:
        raise SignalParseError(
            f"Unknown direction '{tokens[0]}'. "
            f"Expected: {', '.join(sorted(_DIRECTION_MAP.keys()))}"
        )

    # Parse asset (must be alphabetic, 2-10 chars)
    asset = tokens[1].upper()
    if not asset.isalpha() or len(asset) < 2 or len(asset) > 10:
        raise SignalParseError(
            f"Invalid asset '{asset}'. Must be 2-10 alphabetic characters."
        )

    # Parse optional price (3rd positional token)
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
