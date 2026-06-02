"""Parser for premium Telegram signal channels.

Pure text → ParsedSignal. No network, no IO, no external calls. This module
is deliberately isolated so it can be unit-tested against a corpus of real
channel messages without any Telegram credentials.

Design: field-by-field extraction, layout-agnostic. The premium channel
under observation ships ~6+ format variants (categorical, emoji, exchange-
prefixed, range-entry, stop-above trigger, typo'd keywords). Treating each
as a separate layout explodes combinatorially. Instead, we run independent
regex extractors over the whole text for:

    symbol + direction      (required)
    entry   (value | range) (required — "Entry ist immer im Signal")
    stop_loss               (required)
    targets                 (optional; empty = no TPs detected)
    leverage                (optional — default 1x)
    margin_pct              (optional; "Margin: 5%" / "Risk: 5%")
    exchange_scope          (optional — from header line)

If symbol, direction, entry, or stop_loss are missing, the message is NOT
a complete new-signal and the parser returns ``None``. Callers should log
unparsed texts so new variants can be added.

Entry-Type semantics:
    "at"       — single price, limit-style (Entry Point / Entry / inline)
    "above"    — stop-style trigger above (Entry Above / Enter Above)
    "range"    — Entry Zone: X – Y (midpoint used by bridge downstream)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Unicode dash variants seen in the wild.
_DASH = r"[\u2012\u2013\u2014\u2015\-]"
# Raw dash characters (no brackets) — for embedding inside larger char classes
# without the broken-nesting pitfall `[:{_DASH}]`.
_DASH_CHARS = r"\u2012\u2013\u2014\u2015\-"

# Known exchange tokens (lowercase canonical). Kept conservative — only
# exchanges the channel actually names in its header line.
_KNOWN_EXCHANGES = {
    "binance": "binance",
    "okx": "okx",
    "deribit": "deribit",
    "bitget": "bitget",
    "bybit": "bybit",
    "kucoin": "kucoin",
    "huobi": "huobi",
    "blofin": "blofin",
    "bingx": "bingx",
    "mexc": "mexc",
    "gate": "gate",
}


@dataclass(frozen=True)
class ParsedSignal:
    symbol: str  # internal, e.g. "GUNUSDT"
    display_symbol: str  # human, e.g. "GUN/USDT"
    direction: str  # "long" | "short"
    side: str  # "buy" | "sell"
    entry_type: str  # "at" | "above" | "range"
    entry_value: float | None
    entry_min: float | None
    entry_max: float | None
    stop_loss: float | None
    targets: list[float]
    leverage: int
    margin_pct: float | None
    exchange_scope: list[str]
    raw_text: str

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "symbol": self.symbol,
            "display_symbol": self.display_symbol,
            "direction": self.direction,
            "side": self.side,
            "entry_type": self.entry_type,
            "entry_value": self.entry_value,
            "entry_min": self.entry_min,
            "entry_max": self.entry_max,
            "stop_loss": self.stop_loss,
            "targets": list(self.targets),
            "leverage": self.leverage,
            "exchange_scope": list(self.exchange_scope),
        }
        if self.margin_pct is not None:
            payload["margin_pct"] = self.margin_pct
        return payload


# ── Helpers ─────────────────────────────────────────────────────────────────

# Inline separators the channel uses between header fields
# ("US/USDT · LONG · 10x"). Middle-dot (·, U+00B7), bullet (•, U+2022) and the
# pipe never occur inside symbols, keywords or numbers, so collapsing them to a
# space is safe and lets the layout-agnostic extractors see normal whitespace.
_FIELD_SEPARATORS = re.compile(r"[·•∙|]")


def _normalize_separators(text: str) -> str:
    """Replace middle-dot / bullet / pipe field separators with spaces."""
    return _FIELD_SEPARATORS.sub(" ", text)


def _normalize_symbol(raw: str) -> tuple[str, str]:
    """'GUN/USDT' → ('GUNUSDT', 'GUN/USDT'); tolerant of '#', spaces, case."""
    cleaned = raw.strip().lstrip("#").strip().upper()
    cleaned = re.sub(r"\s+", "", cleaned)  # handle '# B3/USDT' → 'B3/USDT'
    if "/" in cleaned:
        return cleaned.replace("/", ""), cleaned
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if cleaned.endswith(quote) and len(cleaned) > len(quote):
            return cleaned, f"{cleaned[: -len(quote)]}/{quote}"
    return cleaned, cleaned


def _direction_to_side(raw: str) -> tuple[str, str]:
    d = raw.strip().lower()
    if d in {"long", "buy"}:
        return "long", "buy"
    if d in {"short", "sell"}:
        return "short", "sell"
    return "long", "buy"


def _to_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


# ── Field extractors ────────────────────────────────────────────────────────

# Symbol + direction can appear in two orders. The exchange-prefixed emoji
# forms ('🚀 #SOL/USDT Long/BUY – 84.48') also include the entry inline,
# so extractors run independently and we take the first successful match.
_SYMBOL_DIR_ORDER_1 = re.compile(  # "Long/Buy #ABC/USDT"
    r"(?im)\b(?P<direction>Long|Short)\s*/\s*(?P<side>Buy|Sell)\s*#\s*"
    r"(?P<symbol>[A-Z0-9]+\s*/\s*[A-Z0-9]+|[A-Z0-9]{2,}\s*/\s*[A-Z0-9]+)"
)
_SYMBOL_DIR_ORDER_2 = re.compile(  # "#ABC/USDT Long/BUY" (emoji or plain)
    r"(?im)#\s*(?P<symbol>[A-Z0-9]+\s*/\s*[A-Z0-9]+)\s+"
    r"(?P<direction>Long|Short)\s*/\s*(?P<side>Buy|Sell)"
)
_SYMBOL_DIR_PLAIN_1 = re.compile(  # "BTCUSDT LONG" / "BTC/USDT SHORT"
    r"(?im)^\s*(?P<symbol>[A-Z0-9]{2,}(?:\s*/\s*[A-Z0-9]+)?)\s+"
    r"(?P<direction>Long|Short|Buy|Sell)\b"
)
_SYMBOL_DIR_PLAIN_2 = re.compile(  # "LONG BTCUSDT" / "SHORT BTC/USDT"
    r"(?im)^\s*(?P<direction>Long|Short|Buy|Sell)\s+"
    r"(?P<symbol>[A-Z0-9]{2,}(?:\s*/\s*[A-Z0-9]+)?)\b"
)


def _extract_symbol_and_direction(
    text: str,
) -> tuple[str, str, str, str] | None:
    """Return (symbol_internal, symbol_display, direction, side) or None."""
    for pat in (
        _SYMBOL_DIR_ORDER_1,
        _SYMBOL_DIR_ORDER_2,
        _SYMBOL_DIR_PLAIN_1,
        _SYMBOL_DIR_PLAIN_2,
    ):
        m = pat.search(text)
        if m is None:
            continue
        internal, display = _normalize_symbol(m["symbol"])
        direction, side = _direction_to_side(m["direction"])
        return internal, display, direction, side
    return None


# Entry: four recognised shapes, tried in specificity order.
# 1) RANGE: 'Entry Zone: X – Y'
# 2) ABOVE: 'Entry Above - X' / 'Enter Above - X' / typos
# 3) AT   : 'Entry Point - X' / 'Entry - X'
# 4) AT   : inline 'Long/Buy - X' or '#SYM Long/BUY – X'
_ENTRY_RANGE = re.compile(
    rf"(?i)Entry(?:\s*Zone)?\s*[:{_DASH_CHARS}]\s*"
    rf"(?P<lo>[\d.,]+)\s*{_DASH}\s*(?P<hi>[\d.,]+)"
)
_ENTRY_ABOVE = re.compile(
    rf"(?i)(?:Entry|Enter)\s*(?:\s+A\s*[Bb]ove|\s+ABove)\s*{_DASH}?\s*(?P<v>[\d.]+)"
)
_ENTRY_POINT = re.compile(rf"(?i)Entry(?:\s*Point)?\s*[:{_DASH_CHARS}]\s*(?P<v>[\d.]+)")
_ENTRY_INLINE = re.compile(rf"(?i)(?:Long|Short)\s*/\s*(?:Buy|Sell)\s*{_DASH}\s*(?P<v>[\d.]+)")


def _extract_entry(text: str) -> tuple[str, float | None, float | None, float | None]:
    """Return (entry_type, entry_value, entry_min, entry_max)."""
    m = _ENTRY_RANGE.search(text)
    if m is not None:
        lo = _to_float(m["lo"])
        hi = _to_float(m["hi"])
        if lo is not None and hi is not None and hi > lo > 0:
            return "range", None, lo, hi
    m = _ENTRY_ABOVE.search(text)
    if m is not None:
        v = _to_float(m["v"])
        if v is not None and v > 0:
            return "above", v, None, None
    m = _ENTRY_POINT.search(text)
    if m is not None:
        v = _to_float(m["v"])
        if v is not None and v > 0:
            return "at", v, None, None
    m = _ENTRY_INLINE.search(text)
    if m is not None:
        v = _to_float(m["v"])
        if v is not None and v > 0:
            return "at", v, None, None
    return "at", None, None, None


# Stop-loss: "Stop Loss:", "StopLoss -", "Stop-Loss:", and the short "SL:" form.
# The short form requires the separator (colon/dash) + a number so a bare "SL"
# in prose never false-matches. ``\bSL`` anchors on a word boundary.
_STOP_LOSS = re.compile(
    rf"(?i)(?:Stop[\s{_DASH_CHARS}]*Loss|\bSL)\s*[:{_DASH_CHARS}]\s*(?P<sl>[\d.]+)"
)


def _extract_stop_loss(text: str) -> float | None:
    m = _STOP_LOSS.search(text)
    return _to_float(m["sl"]) if m else None


# Targets: four observed forms.
#  A) "Targets: 100 - 110 - 120 - 130"       (single-line, dash-separated)
#  B) "🎯 100\n🎯 110\n..."                    (bullseye + inline value per line)
#  C) "🎯 Target:\n100\n110\n..."             (bullseye label then values below)
#  D) "Targets:\n100\n110\n..."               (plain label then values below,
#                                              no emoji — Sprint F 2026-05-12,
#                                              Operator-Beispielsignale TRUTH/OPG/IRYS)
_TARGETS_LINE = re.compile(rf"(?im)^\s*Targets?\s*:\s*(?P<list>[\d.,/\s{_DASH_CHARS}]+?)\s*$")
_TARGET_EMOJI_INLINE = re.compile(r"(?im)^\s*🎯\s*(?P<v>\d[\d.]*)\s*$")
# Label form: "🎯 Target:" (any case, optional 's') followed by numeric lines.
_TARGET_EMOJI_LABEL = re.compile(r"(?im)^\s*🎯\s*Targets?\s*:?\s*$")
# Plain label form: "Targets:" (case-insensitive, optional 's') followed by
# numeric lines. Distinct from form A because A's regex demands AT LEAST one
# digit on the label-line itself; form D has an empty label line.
_TARGET_PLAIN_LABEL = re.compile(r"(?im)^\s*Targets?\s*:\s*$")
_NUMERIC_LINE = re.compile(r"(?im)^\s*(?P<v>\d[\d.]*)\s*$")


def _parse_targets_dashlist(list_text: str) -> list[float]:
    """Parse '100 - 110 - 120' / '100 – 110 – 120' → [100.0, 110.0, 120.0]."""
    out: list[float] = []
    parts = re.split(rf"(?:[,\s]*{_DASH}\s*|[,\s]*/[,\s]*|[,]+)", list_text.strip())
    for p in parts:
        p = p.strip()
        if not p:
            continue
        v = _to_float(p)
        if v is not None and v > 0:
            out.append(v)
    return out


def _extract_targets(text: str) -> list[float]:
    # Form B: inline emoji+value on each line.
    emoji_hits = [_to_float(m["v"]) for m in _TARGET_EMOJI_INLINE.finditer(text)]
    emoji_clean = [v for v in emoji_hits if v is not None and v > 0]
    if emoji_clean:
        return emoji_clean
    # Form C: "🎯 Target:" label, then numeric lines that follow until a
    # non-numeric/non-empty line interrupts (e.g. "🛑 Stop Loss").
    label = _TARGET_EMOJI_LABEL.search(text)
    if label is not None:
        after = text[label.end() :]
        values: list[float] = []
        for raw in after.splitlines():
            line = raw.strip()
            if not line:
                continue
            nm = _NUMERIC_LINE.match(line)
            if nm is None:
                break  # stop at first non-numeric line
            v = _to_float(nm["v"])
            if v is not None and v > 0:
                values.append(v)
        if values:
            return values
    # Form D (Sprint F 2026-05-12): "Targets:" plain label without emoji,
    # values on following lines. Operator-Beispielsignale TRUTH/OPG/IRYS.
    plain_label = _TARGET_PLAIN_LABEL.search(text)
    if plain_label is not None:
        after = text[plain_label.end() :]
        values_d: list[float] = []
        for raw in after.splitlines():
            line = raw.strip()
            if not line:
                continue
            nm = _NUMERIC_LINE.match(line)
            if nm is None:
                break  # stop at first non-numeric line (e.g. "Leverage - 10x")
            v = _to_float(nm["v"])
            if v is not None and v > 0:
                values_d.append(v)
        if values_d:
            return values_d
    # Form A: dash-list on a single "Targets:" line.
    m = _TARGETS_LINE.search(text)
    if m is None:
        return []
    return _parse_targets_dashlist(m["list"])


_LEVERAGE = re.compile(rf"(?i)Leverage\s*[:{_DASH_CHARS}]\s*(?P<lev>\d+)\s*x?")
# Fallback for the bare header form "· 10x ·" / "10x" when no explicit
# "Leverage:" label is present. Tightly anchored: digit(s) immediately followed
# by 'x', not embedded in a larger alnum token (so "0x1a" / "MAX" never match).
_LEVERAGE_BARE = re.compile(r"(?i)(?<![A-Za-z0-9])(?P<lev>\d{1,3})\s*x(?![A-Za-z0-9])")
_MARGIN_PCT = re.compile(
    rf"(?i)(?:Margin|Risk(?:\s+Allocation)?)\s*[:{_DASH_CHARS}]\s*"
    rf"(?P<pct>\d+(?:[,.]\d+)?)\s*%?"
)


def _extract_leverage(text: str) -> int:
    m = _LEVERAGE.search(text) or _LEVERAGE_BARE.search(text)
    if m is None:
        return 1
    try:
        lev = int(m["lev"])
    except (ValueError, TypeError):
        return 1
    # Clamp to a sane exchange range; out-of-range bare matches fall back to 1x.
    return lev if 1 <= lev <= 125 else 1


def _extract_margin_pct(text: str) -> float | None:
    m = _MARGIN_PCT.search(text)
    if m is None:
        return None
    pct = _to_float(m["pct"])
    if pct is None or pct <= 0:
        return None
    return pct


def _extract_exchange_scope(text: str) -> list[str]:
    """Scan leading lines for a known-exchange header (comma-separated).

    Only the FIRST line containing multiple known exchange names counts —
    avoids false positives when an exchange name appears inline later.
    """
    for raw_line in text.splitlines():
        line = raw_line.strip().lower()
        if not line:
            continue
        hits: list[str] = []
        for needle, canonical in _KNOWN_EXCHANGES.items():
            if needle in line and canonical not in hits:
                hits.append(canonical)
        if len(hits) >= 2:
            return hits
    return []


# ── Public entry ────────────────────────────────────────────────────────────


# ── Target-Completion / All-TP-Hit-Meldungen ───────────────────────────────
#
# 2026-05-12 Sprint D (per Operator-Auftrag Sektion 3+9). Channel postet nach
# einem vollständigen Take-Profit-Run kurze Bestätigungen:
#
#   🎯 #TRUTH/USDT has touched 15674 and has completed all the profit targets
#   🎯 #ON/USDT has touched 19561 and has completed all the profit targets
#
# Parser-Vertrag:
#   - liefert ``TargetCompletionEvent`` (symbol, touch_price, raw_text) oder None
#   - touch_price ist optional (manche Channel-Varianten lassen die Zahl weg)
#   - bewusst getrennt von ``parse_premium_channel_message`` weil das Schema
#     fundamental anders ist (kein entry/SL/targets/leverage — nur eine Outcome-
#     Bestätigung). Caller dispatcht: erst NewSignal-Parser, dann Completion-Parser.

_TARGET_COMPLETION = re.compile(
    r"(?im)🎯\s*#\s*(?P<symbol>[A-Z0-9]+\s*/\s*[A-Z0-9]+|[A-Z0-9]{2,}\s*/\s*[A-Z0-9]+)"
    r"\s+has\s+touched\s+(?P<price>[\d.,]+)?\s*"
    r".*?completed\s+all\s+the\s+profit\s+targets"
)
# Variante ohne touch-price ("has completed all the profit targets")
_TARGET_COMPLETION_NO_PRICE = re.compile(
    r"(?im)🎯\s*#\s*(?P<symbol>[A-Z0-9]+\s*/\s*[A-Z0-9]+|[A-Z0-9]{2,}\s*/\s*[A-Z0-9]+)"
    r"\s+(?:has\s+)?completed\s+all\s+(?:the\s+)?profit\s+targets"
)


@dataclass(frozen=True)
class TargetCompletionEvent:
    """All-TP-Hit-Bestätigung aus dem Premium-Channel."""

    symbol: str  # internal canonical e.g. "TRUTHUSDT"
    display_symbol: str  # human e.g. "TRUTH/USDT"
    touch_price: float | None  # None when channel omits it
    raw_text: str


def parse_target_completion(text: str) -> TargetCompletionEvent | None:
    """Return TargetCompletionEvent or None for 🎯 all-TP-completion messages.

    Pure, IO-free. Caller decides: dispatch to new-signal parser first; if that
    returns None, try this one before classifying the message as not-a-signal.
    """
    if not text or not text.strip():
        return None
    # Order matters: price-bearing form first (more specific).
    m = _TARGET_COMPLETION.search(text)
    if m is not None:
        symbol_internal, symbol_display = _normalize_symbol(m["symbol"])
        price = _to_float(m["price"]) if m["price"] else None
        return TargetCompletionEvent(
            symbol=symbol_internal,
            display_symbol=symbol_display,
            touch_price=price,
            raw_text=text,
        )
    m = _TARGET_COMPLETION_NO_PRICE.search(text)
    if m is not None:
        symbol_internal, symbol_display = _normalize_symbol(m["symbol"])
        return TargetCompletionEvent(
            symbol=symbol_internal,
            display_symbol=symbol_display,
            touch_price=None,
            raw_text=text,
        )
    return None


def parse_premium_channel_message(text: str) -> ParsedSignal | None:
    """Return a ParsedSignal or None. None = not a new-signal message.

    A message is considered a signal only when symbol, direction, entry and
    stop_loss are all present — per operator rule "Entry ist immer im Signal".
    """
    if not text or not text.strip():
        return None

    # Field separators (·, •, |) collapse to spaces so the layout-agnostic
    # extractors see normal whitespace. raw_text keeps the original verbatim.
    work = _normalize_separators(text)

    sym_dir = _extract_symbol_and_direction(work)
    if sym_dir is None:
        return None
    symbol_internal, symbol_display, direction, side = sym_dir

    entry_type, entry_value, entry_min, entry_max = _extract_entry(work)
    if entry_type == "range":
        if entry_min is None or entry_max is None:
            return None
    elif entry_value is None:
        return None

    stop_loss = _extract_stop_loss(work)
    if stop_loss is None:
        return None

    return ParsedSignal(
        symbol=symbol_internal,
        display_symbol=symbol_display,
        direction=direction,
        side=side,
        entry_type=entry_type,
        entry_value=entry_value,
        entry_min=entry_min,
        entry_max=entry_max,
        stop_loss=stop_loss,
        targets=_extract_targets(work),
        leverage=_extract_leverage(work),
        margin_pct=_extract_margin_pct(work),
        exchange_scope=_extract_exchange_scope(work),
        raw_text=text,
    )


__all__ = [
    "ParsedSignal",
    "TargetCompletionEvent",
    "parse_premium_channel_message",
    "parse_target_completion",
]
