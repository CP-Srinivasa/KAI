"""Central identity helper for premium raw/approved signal views."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

RAW_APPROVED_DEDUPED_EVENT = "premium_raw_approved_deduped"


@dataclass(frozen=True)
class DedupeResult:
    records: list[dict[str, Any]]
    duplicates: int
    audit_event: str | None = None


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_token(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    try:
        return f"{float(value):.12g}"
    except (TypeError, ValueError):
        return ""


def _timestamp_bucket(record: dict[str, Any], *, bucket_minutes: int = 15) -> str:
    raw = _str(record.get("timestamp_utc")) or _str(_payload(record).get("timestamp_utc"))
    if raw is None:
        return ""
    cleaned = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return raw[:16]
    minute = (dt.minute // bucket_minutes) * bucket_minutes
    return dt.replace(minute=minute, second=0, microsecond=0).isoformat()


def _targets_token(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return ",".join(_float_token(v) for v in value if _float_token(v))


def _structural_key(record: dict[str, Any]) -> str | None:
    payload = _payload(record)
    parts = [
        _str(payload.get("display_symbol")) or _str(payload.get("symbol")),
        _str(payload.get("side")) or _str(payload.get("direction")),
        _float_token(payload.get("entry_value")),
        _targets_token(payload.get("targets")),
        _float_token(payload.get("stop_loss")),
        _float_token(payload.get("leverage")),
        _timestamp_bucket(record),
    ]
    if not any(parts[:5]):
        return None
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return "struct:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def premium_signal_dedupe_key(record: dict[str, Any]) -> str:
    """Return the central raw/approved identity key, in priority order."""
    payload = _payload(record)
    candidates = [
        _str(payload.get("origin_signal_id")) or _str(record.get("origin_signal_id")),
        _str(payload.get("source_uid")) or _str(record.get("source_uid")),
        _str(payload.get("source_message_id"))
        or _str(record.get("message_id"))
        or _str(record.get("telegram_message_id")),
        _str(payload.get("normalized_raw_hash")) or _str(record.get("normalized_raw_hash")),
        _structural_key(record),
    ]
    for idx, value in enumerate(candidates):
        if value:
            return f"premium:{idx}:{value}"
    fallback = _str(record.get("envelope_id")) or hashlib.sha256(repr(record).encode()).hexdigest()
    return "envelope:" + fallback


def _is_approved(record: dict[str, Any]) -> bool:
    source = _str(record.get("source")) or ""
    return source.endswith("_approved")


def _is_premium_telegram(record: dict[str, Any]) -> bool:
    source = _str(record.get("source")) or ""
    return source.startswith("telegram_premium_channel")


def _rank(record: dict[str, Any]) -> tuple[int, str]:
    return (1 if _is_approved(record) else 0, _str(record.get("timestamp_utc")) or "")


def dedupe_premium_signal_records(records: list[dict[str, Any]]) -> DedupeResult:
    """Collapse raw and approved premium records into one fachliches Signal."""
    by_key: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for record in records:
        key = (
            premium_signal_dedupe_key(record)
            if _is_premium_telegram(record)
            else "envelope:" + (_str(record.get("envelope_id")) or repr(id(record)))
        )
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = record
            continue
        duplicates += 1
        if _rank(record) >= _rank(previous):
            by_key[key] = record
    deduped = list(by_key.values())
    return DedupeResult(
        records=deduped,
        duplicates=duplicates,
        audit_event=RAW_APPROVED_DEDUPED_EVENT if duplicates else None,
    )


__all__ = [
    "RAW_APPROVED_DEDUPED_EVENT",
    "DedupeResult",
    "dedupe_premium_signal_records",
    "premium_signal_dedupe_key",
]
