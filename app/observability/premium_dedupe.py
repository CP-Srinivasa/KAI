"""Raw+Approved premium-signal deduplication (Dashboard double-count fix).

The premium pipeline emits a signal twice: once as ``telegram_premium_channel``
(raw envelope, ``ENV-TG-...``) and once as ``telegram_premium_channel_approved``
(approval re-emit, ``ENV-APP-...``). They are ONE business signal but were
counted/shown twice because the UI keyed on ``envelope_id`` (which differs)
rather than the stable signal identity (which is shared).

Both records share, in priority order:
  1. ``origin_signal_id``      (payload.signal_id — identical raw↔approved)
  2. ``source_uid``            (telegram:<chat>:<msg> — identical)
  3. ``telegram_message_id``   (source_message_id — identical)
  4. ``normalized_raw_hash``   (hash of symbol+side+entry+targets+sl+leverage)
  5. composite                 (…+timestamp bucket)

This module produces deduped GROUPS (one per business signal) so External
Signals + Matrix count each signal once, while the UI can still show the raw
envelope and the approved event as two lifecycle events of the same signal.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

# Records within this many seconds collapse to the same composite bucket when
# no stable id is present (fallback key 5).
_TIMESTAMP_BUCKET_SECONDS = 120

_APPROVED_SUFFIX = "_approved"
_RAW_SOURCE = "telegram_premium_channel"


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    p = record.get("payload")
    return p if isinstance(p, dict) else record


def _first_str(*values: Any) -> str | None:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _num(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.10g}"
    return ""


def normalized_raw_hash(record: dict[str, Any]) -> str:
    """Stable hash over the signal's defining fields (key 4)."""
    p = _payload(record)
    symbol = _first_str(p.get("symbol"), p.get("display_symbol")) or ""
    side = _first_str(p.get("side"), p.get("direction")) or ""
    raw_targets = p.get("targets")
    targets_norm = (
        ",".join(_num(t) for t in raw_targets if _num(t)) if isinstance(raw_targets, list) else ""
    )
    basis = "|".join(
        [
            symbol.upper().replace("/", ""),
            side.lower(),
            _num(p.get("entry_value")),
            _num(p.get("stop_loss")),
            _num(p.get("leverage")),
            targets_norm,
        ]
    )
    return "raw:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def compute_dedup_key(record: dict[str, Any]) -> str:
    """Resolve the strongest stable identity key for ``record`` (keys 1→5)."""
    p = _payload(record)
    # 1. origin_signal_id (shared by raw + approved)
    sig = _first_str(record.get("origin_signal_id"), p.get("origin_signal_id"), p.get("signal_id"))
    if sig:
        return f"sig:{sig}"
    # 2. source_uid
    uid = _first_str(record.get("source_uid"), p.get("source_uid"))
    if uid:
        return f"uid:{uid}"
    # 3. telegram_message_id (+ chat to avoid cross-channel collision)
    msg = _first_str(record.get("message_id"), p.get("source_message_id"))
    chat = _first_str(record.get("chat_id"), p.get("source_chat_id"))
    if msg:
        return f"msg:{chat or '?'}:{msg}"
    # 4. normalized_raw_hash
    nh = normalized_raw_hash(record)
    if not nh.endswith(":"):
        # 5. composite: add coarse timestamp bucket to disambiguate re-posts
        ts = _first_str(record.get("timestamp_utc"), p.get("timestamp_utc"))
        bucket = ""
        if ts:
            digits = "".join(ch for ch in ts if ch.isdigit())[:12]  # YYYYMMDDHHMM-ish
            if digits:
                bucket = digits
        return f"{nh}:{bucket}" if bucket else nh
    return nh


def _role(record: dict[str, Any]) -> str:
    src = _first_str(record.get("source"), _payload(record).get("source")) or ""
    if src.endswith(_APPROVED_SUFFIX):
        return "approved"
    if src.startswith(_RAW_SOURCE):
        return "raw"
    return "other"


@dataclass
class DedupGroup:
    """One business signal with its raw + approved lifecycle events."""

    key: str
    raw_event: dict[str, Any] | None = None
    approved_event: dict[str, Any] | None = None
    other_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def canonical(self) -> dict[str, Any]:
        """The record to count/display once: approved if present, else raw."""
        return (
            self.approved_event
            or self.raw_event
            or (self.other_events[0] if self.other_events else {})
        )

    @property
    def is_double_sourced(self) -> bool:
        return self.raw_event is not None and self.approved_event is not None

    def to_summary(self) -> dict[str, Any]:
        return {
            "dedup_key": self.key,
            "double_sourced": self.is_double_sourced,
            "has_raw": self.raw_event is not None,
            "has_approved": self.approved_event is not None,
            "event_count": sum(1 for e in (self.raw_event, self.approved_event) if e is not None)
            + len(self.other_events),
        }


def dedupe_premium_signals(records: list[dict[str, Any]]) -> list[DedupGroup]:
    """Group raw+approved emissions of the same signal into one DedupGroup.

    Preserves first-seen order of the business signals.
    """
    groups: dict[str, DedupGroup] = {}
    order: list[str] = []
    for rec in records:
        key = compute_dedup_key(rec)
        grp = groups.get(key)
        if grp is None:
            grp = DedupGroup(key=key)
            groups[key] = grp
            order.append(key)
        role = _role(rec)
        if role == "approved":
            grp.approved_event = rec
        elif role == "raw":
            grp.raw_event = rec
        else:
            grp.other_events.append(rec)
    return [groups[k] for k in order]


def deduped_count(records: list[dict[str, Any]]) -> int:
    """Number of distinct business signals (raw+approved counted once)."""
    return len(dedupe_premium_signals(records))


__all__ = [
    "DedupGroup",
    "compute_dedup_key",
    "deduped_count",
    "dedupe_premium_signals",
    "normalized_raw_hash",
]
