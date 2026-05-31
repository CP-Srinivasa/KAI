"""Backfill JSONL premium-signal audit trails into the SQLite event store."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.observability import premium_event_store as store

DEFAULT_ENVELOPE_LOG = Path("artifacts/telegram_message_envelope.jsonl")
DEFAULT_BRIDGE_LOG = Path("artifacts/bridge_pending_orders.jsonl")


@dataclass(frozen=True)
class BackfillSummary:
    envelope_records: int = 0
    approval_records: int = 0
    bridge_records: int = 0
    malformed_lines: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _iter_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    records: list[dict[str, Any]] = []
    malformed = 0
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
            else:
                malformed += 1
    return records, malformed


def _is_approval_record(record: dict[str, Any]) -> bool:
    return (
        record.get("event") == "telegram_channel_approval"
        or isinstance(record.get("origin_envelope_id"), str)
        or str(record.get("source", "")).endswith("_approved")
    )


def backfill_event_store(
    *,
    envelope_log: Path = DEFAULT_ENVELOPE_LOG,
    bridge_log: Path = DEFAULT_BRIDGE_LOG,
    store_path: Path | None = None,
) -> BackfillSummary:
    envelope_records, malformed_env = _iter_jsonl(envelope_log)
    bridge_records, malformed_bridge = _iter_jsonl(bridge_log)

    envelope_count = 0
    approval_count = 0
    for record in envelope_records:
        if _is_approval_record(record):
            store.record_approval(record, path=store_path)
            approval_count += 1
        else:
            store.record_envelope(record, path=store_path)
            envelope_count += 1

    for record in bridge_records:
        store.record_bridge_decision(record, path=store_path)

    return BackfillSummary(
        envelope_records=envelope_count,
        approval_records=approval_count,
        bridge_records=len(bridge_records),
        malformed_lines=malformed_env + malformed_bridge,
    )


__all__ = [
    "BackfillSummary",
    "DEFAULT_BRIDGE_LOG",
    "DEFAULT_ENVELOPE_LOG",
    "backfill_event_store",
]
