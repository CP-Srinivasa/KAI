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

import hashlib
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


def build_source_uid(*, chat_id: int, message_id: int) -> str:
    """Stable Telegram source identity for exactly-once signal semantics."""
    return f"telegram:{int(chat_id)}:{int(message_id)}"


def _make_stable_signal_id(symbol: str, source_uid: str | None) -> str:
    if not source_uid:
        return _make_signal_id(symbol)
    clean = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    digest = hashlib.sha256(source_uid.encode("utf-8")).hexdigest()[:12].upper()
    return f"SIG-TGCH-{digest}-{clean}"


def _make_stable_envelope_id(source_uid: str | None, received_ts: str) -> str | None:
    if not source_uid:
        return None
    parts = source_uid.split(":")
    if len(parts) == 3 and parts[0] == "telegram":
        chat_part = re.sub(r"[^0-9]", "", parts[1])[-12:] or "0"
        msg_part = re.sub(r"[^0-9]", "", parts[2]) or "0"
        digest = hashlib.sha256(source_uid.encode("utf-8")).hexdigest()[:8]
        return f"ENV-TG-{chat_part}-{msg_part}-{digest}"
    digest = hashlib.sha256(source_uid.encode("utf-8")).hexdigest()[:16]
    stamp = "".join(ch for ch in received_ts if ch.isdigit())[:14]
    return f"ENV-SRC-{stamp}-{digest}"


def _stable_idempotency_key(source_uid: str | None) -> str | None:
    if not source_uid:
        return None
    return hashlib.sha256(f"premium-signal:{source_uid}".encode()).hexdigest()[:32]


def build_envelope_record(
    parsed: ParsedSignal,
    *,
    source: str = DEFAULT_SOURCE,
    chat_id: int | None = None,
    message_id: int | None = None,
    source_platform: str | None = None,
    source_uid: str | None = None,
    now: datetime | None = None,
    scale_factor: float | None = None,
) -> dict[str, object]:
    """Wrap a ParsedSignal into the canonical envelope-audit record.

    Keep this function pure — it does not touch disk or settings. The
    returned dict is append-ready for the envelope JSONL.

    2026-05-14 (P1 #8): ``scale_factor`` controls the channel-scale
    annotation that lets the bridge skip its own re-detection:
    - ``None``  → market_data was unreachable at receive time;
                   payload keeps raw values + ``scale_unknown=True``;
                   bridge will re-resolve every tick until it succeeds.
    - ``1.0``   → channel already in USD scale (BTC, ETH, …);
                   payload values unchanged + ``scale_resolved_at_emit=True``.
    - ``>1.0``  → integer-tick scale (e.g. 1e6 for SWARMS);
                   payload's entry/sl/targets divided by factor +
                   ``scale_resolved_at_emit=True`` + ``scale_factor=X``.
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
    stable_source_uid = source_uid
    if stable_source_uid is None and chat_id is not None and message_id is not None:
        stable_source_uid = build_source_uid(chat_id=chat_id, message_id=message_id)
    source_platform_value = source_platform or ("telegram" if stable_source_uid else None)
    signal_id = _make_stable_signal_id(parsed.symbol, stable_source_uid)

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
        margin_pct=parsed.margin_pct,
        timestamp_utc=ts,
    )

    envelope = MessageEnvelope.wrap(
        signal,
        source_channel=SourceChannel.TELEGRAM,
        chat_id=chat_id,
        received_ts=ts,
    )

    payload = dict(envelope.payload)
    if stable_source_uid is not None:
        payload["source_platform"] = source_platform_value or "telegram"
        payload["source_uid"] = stable_source_uid
        if chat_id is not None:
            payload["source_chat_id"] = int(chat_id)
        if message_id is not None:
            payload["source_message_id"] = int(message_id)
    # 2026-05-14 (P1 #8): apply scale at receive time so the bridge does
    # not have to re-resolve on every tick. See ``scale_resolver`` for the
    # detection logic. If scale_factor is None the worker could not reach
    # the market_data provider — payload stays raw and the bridge falls
    # back to its legacy detection until the price comes back.
    if scale_factor is None:
        payload["scale_unknown"] = True
    else:
        from app.execution.scale_resolver import apply_scale_to_payload

        apply_scale_to_payload(payload, scale_factor)
        payload["scale_resolved_at_emit"] = True
        payload["scale_factor"] = float(scale_factor)

    stable_envelope_id = _make_stable_envelope_id(stable_source_uid, ts)
    stable_idem = _stable_idempotency_key(stable_source_uid)
    record: dict[str, object] = {
        "timestamp_utc": ts,
        "event": "telegram_channel_envelope",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": source,
        "execution_enabled": False,
        "write_back_allowed": False,
        "envelope_id": stable_envelope_id or envelope.envelope_id,
        "idempotency_key": stable_idem or envelope.idempotency_key,
        "payload": payload,
    }
    if chat_id is not None:
        record["chat_id"] = chat_id
    if message_id is not None:
        record["message_id"] = message_id
    if stable_source_uid is not None:
        record["source_platform"] = source_platform_value or "telegram"
        record["source_uid"] = stable_source_uid
    return record


def _iter_prior_identity(path: Path, *, lookback: int) -> tuple[set[str], set[str]]:
    """Return accepted idempotency_keys and source_uids from the log tail."""
    if not path.exists():
        return set(), set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("[channel-envelope] log read failed: %s", exc)
        return set(), set()
    keys: set[str] = set()
    source_uids: set[str] = set()
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
        source_uid = rec.get("source_uid")
        if not isinstance(source_uid, str):
            payload = rec.get("payload")
            if isinstance(payload, dict):
                source_uid = payload.get("source_uid")
        if isinstance(source_uid, str) and source_uid:
            source_uids.add(source_uid)
    return keys, source_uids


def emit_parsed_signal(
    parsed: ParsedSignal,
    *,
    source: str = DEFAULT_SOURCE,
    chat_id: int | None = None,
    message_id: int | None = None,
    source_platform: str | None = None,
    source_uid: str | None = None,
    envelope_log: Path | None = None,
    lookback: int = 500,
    now: datetime | None = None,
    scale_factor: float | None = None,
) -> dict[str, object] | None:
    """Append an envelope record for `parsed`. Returns the record, or None on dup.

    Duplicate detection scans the tail of the log for accepted records
    with the same idempotency_key — so replaying the same channel post
    (e.g. Telethon reconnect) is a no-op.

    ``scale_factor`` is forwarded to ``build_envelope_record`` so the
    envelope carries the channel-scale annotation set by the worker.
    """
    log_path = envelope_log or _DEFAULT_ENVELOPE_LOG
    record = build_envelope_record(
        parsed,
        source=source,
        chat_id=chat_id,
        message_id=message_id,
        source_platform=source_platform,
        source_uid=source_uid,
        now=now,
        scale_factor=scale_factor,
    )
    idem = record["idempotency_key"]
    assert isinstance(idem, str)
    record_source_uid = record.get("source_uid")

    if isinstance(record_source_uid, str) and record_source_uid:
        try:
            from app.observability.premium_event_store import source_uid_exists

            if source_uid_exists(record_source_uid):
                logger.info(
                    "[channel-envelope] duplicate source_uid=%s in event-store — skipping",
                    record_source_uid,
                )
                return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[channel-envelope] event-store lookup failed: %s", exc)

    prior_keys, prior_source_uids = _iter_prior_identity(log_path, lookback=lookback)
    if idem in prior_keys or (
        isinstance(record_source_uid, str) and record_source_uid in prior_source_uids
    ):
        logger.info("[channel-envelope] duplicate idempotency_key=%s — skipping", idem)
        return None

    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("[channel-envelope] write failed: %s", exc)
        return None
    try:
        from app.observability.premium_event_store import record_envelope

        record_envelope(record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[channel-envelope] event-store write failed: %s", exc)
    return record


__all__ = [
    "DEFAULT_SOURCE",
    "build_envelope_record",
    "build_source_uid",
    "emit_parsed_signal",
]
