"""Adapter: ParsedSignal → envelope JSONL for the bridge worker.

The telegram_channel_parser turns raw channel text into a ParsedSignal.
This module wraps that result into the standard canonical MessageEnvelope
and appends a record to ``artifacts/telegram_message_envelope.jsonl`` —
the same log the bridge (envelope_to_paper_bridge) already reads.

Design invariants:
- stage=accepted / status=ok / message_type=signal  (so the bridge picks
  it up; any other values are filtered out at _collect_pending_signals).
- source="telegram_premium_channel" by default — must be added to the
  bridge allowlist before live fills happen (Shadow-Mode until then).
- Idempotency: records sharing the same canonical idempotency_key with a
  prior accepted row are skipped, so Telethon reconnect-replays don't
  double-emit the same channel post.
- Pure helper (``build_envelope_record``) is separated from IO
  (``emit_parsed_signal``) to keep unit tests free of filesystem state.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ingestion.telegram_channel_parser import ParsedSignal

logger = logging.getLogger(__name__)

_DEFAULT_ENVELOPE_LOG = Path("artifacts/telegram_message_envelope.jsonl")
DEFAULT_SOURCE = "telegram_premium_channel"


def _make_signal_id(symbol: str) -> str:
    """Channel-scoped signal id: SIG-TGCH-YYYYMMDDHHMMSS-SYMBOL."""
    now = datetime.now(UTC)
    clean = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    return f"SIG-TGCH-{now.strftime('%Y%m%d%H%M%S')}-{clean}"


def build_envelope_record(
    parsed: ParsedSignal,
    *,
    source: str = DEFAULT_SOURCE,
    chat_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Wrap a ParsedSignal into the canonical envelope-audit record.

    Keep this function pure — it does not touch disk or settings. The
    returned dict is append-ready for the envelope JSONL.
    """
    from app.messaging.message_models import (
        Direction,
        EntryType,
        MarketType,
        MessageEnvelope,
        Side,
        SourceChannel,
        TradingSignal,
    )

    ts = (now or datetime.now(UTC)).isoformat()
    signal_id = _make_signal_id(parsed.symbol)

    signal = TradingSignal(
        signal_id=signal_id,
        source=source,
        exchange_scope=list(parsed.exchange_scope),
        market_type=MarketType.FUTURES,
        symbol=parsed.symbol,
        display_symbol=parsed.display_symbol,
        side=Side(parsed.side),
        direction=Direction(parsed.direction),
        entry_type=EntryType(parsed.entry_type),
        entry_value=parsed.entry_value,
        entry_min=parsed.entry_min,
        entry_max=parsed.entry_max,
        targets=list(parsed.targets),
        stop_loss=parsed.stop_loss,
        leverage=parsed.leverage,
        timestamp_utc=ts,
    )

    envelope = MessageEnvelope.wrap(
        signal,
        source_channel=SourceChannel.TELEGRAM,
        chat_id=chat_id,
        received_ts=ts,
    )

    record: dict[str, object] = {
        "timestamp_utc": ts,
        "event": "telegram_channel_envelope",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": source,
        "execution_enabled": False,
        "write_back_allowed": False,
        "envelope_id": envelope.envelope_id,
        "idempotency_key": envelope.idempotency_key,
        "payload": dict(envelope.payload),
    }
    if chat_id is not None:
        record["chat_id"] = chat_id
    return record


def _iter_prior_idempotency_keys(
    path: Path, *, lookback: int
) -> set[str]:
    """Return accepted idempotency_keys from the tail of the envelope log."""
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("[channel-envelope] log read failed: %s", exc)
        return set()
    keys: set[str] = set()
    for raw in lines[-lookback:]:
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("stage") != "accepted" or rec.get("status") != "ok":
            continue
        key = rec.get("idempotency_key")
        if isinstance(key, str) and key:
            keys.add(key)
    return keys


def emit_parsed_signal(
    parsed: ParsedSignal,
    *,
    source: str = DEFAULT_SOURCE,
    chat_id: int | None = None,
    envelope_log: Path | None = None,
    lookback: int = 500,
    now: datetime | None = None,
) -> dict[str, object] | None:
    """Append an envelope record for `parsed`. Returns the record, or None on dup.

    Duplicate detection scans the tail of the log for accepted records
    with the same idempotency_key — so replaying the same channel post
    (e.g. Telethon reconnect) is a no-op.
    """
    log_path = envelope_log or _DEFAULT_ENVELOPE_LOG
    record = build_envelope_record(
        parsed, source=source, chat_id=chat_id, now=now
    )
    idem = record["idempotency_key"]
    assert isinstance(idem, str)

    prior = _iter_prior_idempotency_keys(log_path, lookback=lookback)
    if idem in prior:
        logger.info(
            "[channel-envelope] duplicate idempotency_key=%s — skipping", idem
        )
        return None

    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("[channel-envelope] write failed: %s", exc)
        return None
    return record


__all__ = [
    "DEFAULT_SOURCE",
    "build_envelope_record",
    "emit_parsed_signal",
]
