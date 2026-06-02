"""Raw-store + parser quarantine for the premium Telegram channel.

Why this module exists
----------------------
The forensic sprint goal (§4) requires: store the raw Telegram payload BEFORE
parsing, and never silently drop a message that *looks like a signal* but the
parser could not turn into a ParsedSignal. A silent drop is the worst failure
mode — a tradeable signal vanishes with no audit trail.

This is a **standalone, additive** module: it does not modify the listener,
worker or envelope adapter (which are under active parallel development). The
worker can adopt ``parse_or_quarantine`` at its ingestion point when ready; the
functions are pure-ish (single append per call, fail-soft IO) and fully
unit-tested in isolation.

Contract
--------
- ``store_raw`` appends the raw payload to an append-only inbox JSONL *before*
  any parse attempt. Fail-soft: an IO error logs and returns False, never raises
  into the listener.
- ``parse_or_quarantine`` tries the parser. On success returns the ParsedSignal.
  On a parser miss it decides via ``signal_indicators`` whether the text looked
  like a signal; if so it writes a ``parser_quarantine.jsonl`` record (with the
  indicators that fired) and emits an operator alert (``rejected_by_parser``)
  through the injected callback. A text with no signal indicators is genuinely
  not-a-signal and is returned as None without quarantine noise.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ingestion.telegram_channel_parser import (
    ParsedSignal,
    parse_premium_channel_message,
)
from app.risk.reason_codes import RejectCode

logger = logging.getLogger(__name__)

_RAW_INBOX = Path("artifacts/telegram_raw_inbox.jsonl")
_QUARANTINE_LOG = Path("artifacts/parser_quarantine.jsonl")

# Signal-likeness indicators. A message is "signal-like" when it carries a
# direction word AND at least one numeric price AND at least one trade keyword.
_DIRECTION = re.compile(r"(?i)\b(long|short|buy|sell)\b")
_PRICE = re.compile(r"\d+[.,]?\d*")
_TRADE_KEYWORD = re.compile(
    r"(?i)\b(entry|enter|stop\s*loss|\bsl\b|stop|target|targets|\btp\b|leverage)\b"
)
_SLASH_PAIR = re.compile(r"(?i)\b[A-Z0-9]{2,}\s*/\s*USDT?C?\b")


@dataclass
class QuarantineRecord:
    timestamp_utc: str
    reason_code: str
    raw_text: str
    signal_indicators: list[str]
    source_uid: str | None = None
    chat_id: int | None = None
    message_id: int | None = None
    alert_emitted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "event": "parser_quarantine",
            "reason_code": self.reason_code,
            "signal_indicators": list(self.signal_indicators),
            "source_uid": self.source_uid,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "alert_emitted": self.alert_emitted,
            "raw_text": self.raw_text,
            **self.extra,
        }


def signal_indicators(text: str) -> list[str]:
    """Return the list of signal-likeness indicators that fired for `text`."""
    if not text:
        return []
    hits: list[str] = []
    if _DIRECTION.search(text):
        hits.append("direction")
    if _PRICE.search(text):
        hits.append("price")
    if _TRADE_KEYWORD.search(text):
        hits.append("trade_keyword")
    if _SLASH_PAIR.search(text):
        hits.append("symbol_pair")
    return hits


def is_signal_like(text: str) -> bool:
    """Heuristic: looked like a trading signal even if the parser missed it."""
    hits = set(signal_indicators(text))
    # Require a direction + a price + (a trade keyword or a symbol pair).
    return "direction" in hits and "price" in hits and (
        "trade_keyword" in hits or "symbol_pair" in hits
    )


def _append_jsonl(path: Path, record: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        logger.error("[parser-quarantine] write failed (%s): %s", path, exc)
        return False


def store_raw(
    *,
    raw_text: str,
    raw_json: dict[str, Any] | None = None,
    source_uid: str | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
    now: datetime | None = None,
    inbox_log: Path | None = None,
) -> bool:
    """Append the raw payload to the inbox BEFORE parsing. Fail-soft."""
    ts = (now or datetime.now(UTC)).isoformat()
    record = {
        "timestamp_utc": ts,
        "event": "telegram_raw_inbox",
        "source_uid": source_uid,
        "chat_id": chat_id,
        "message_id": message_id,
        "raw_text": raw_text,
        "raw_json": raw_json,
    }
    return _append_jsonl(inbox_log or _RAW_INBOX, record)


def parse_or_quarantine(
    raw_text: str,
    *,
    source_uid: str | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
    now: datetime | None = None,
    quarantine_log: Path | None = None,
    alert_cb: Callable[[QuarantineRecord], None] | None = None,
) -> ParsedSignal | None:
    """Parse `raw_text`; quarantine + alert if it looked like a signal but missed.

    Returns the ParsedSignal on success, else None. A None return for a
    signal-like text is always accompanied by a quarantine record + alert — so
    there is no path where a signal-like message disappears without a trace.
    """
    parsed = parse_premium_channel_message(raw_text)
    if parsed is not None:
        return parsed

    if not is_signal_like(raw_text):
        # Genuinely not a signal (chat, image caption, etc.) — no quarantine.
        return None

    record = QuarantineRecord(
        timestamp_utc=(now or datetime.now(UTC)).isoformat(),
        reason_code=RejectCode.REJECTED_BY_PARSER.value,
        raw_text=raw_text,
        signal_indicators=signal_indicators(raw_text),
        source_uid=source_uid,
        chat_id=chat_id,
        message_id=message_id,
    )

    # Operator alert — injected callback so this module stays decoupled from the
    # alert transport. Default: a warning log (the worker should pass the real
    # alert sender). Alert failure must not prevent the quarantine record.
    if alert_cb is not None:
        try:
            alert_cb(record)
            record.alert_emitted = True
        except Exception as exc:  # noqa: BLE001
            logger.error("[parser-quarantine] alert_cb failed: %s", exc)
    else:
        logger.warning(
            "[parser-quarantine] rejected_by_parser source_uid=%s indicators=%s",
            source_uid,
            record.signal_indicators,
        )

    _append_jsonl(quarantine_log or _QUARANTINE_LOG, record.to_dict())
    return None


__all__ = [
    "QuarantineRecord",
    "is_signal_like",
    "parse_or_quarantine",
    "signal_indicators",
    "store_raw",
]
