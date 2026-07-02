"""Append-only audit sink for premium truth-fix events (2026-06-08).

Centralises the audit event names introduced by the Premium Market-Data/Scale/
Trail truth-fix sprint so producers (scale_resolver read path, bridge,
dedupe) and consumers (tests, trail, dashboard) share one stable vocabulary.

Writes JSON lines to ``artifacts/premium_market_data_audit.jsonl``. Failures to
write are swallowed (observability must never break the read/execution path).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Stable event vocabulary (Goal 2026-06-08 §Audit Events).
EVENT_OUTLIER_REJECTED = "premium_market_price_outlier_rejected"
EVENT_SCALE_UNRESOLVED = "premium_scale_unresolved_or_bad_price"
EVENT_SCALE_RESOLVED_PERSISTED = "premium_scale_resolved_persisted"
EVENT_BAD_TICK_IGNORED = "premium_bad_tick_ignored"
EVENT_TERMINAL_STABILIZED = "premium_terminal_stabilized"
EVENT_RAW_APPROVED_DEDUPED = "premium_raw_approved_deduped"
EVENT_REQUIRES_QUOTE_SOURCE = "premium_requires_quote_source"

ALL_EVENTS = frozenset(
    {
        EVENT_OUTLIER_REJECTED,
        EVENT_SCALE_UNRESOLVED,
        EVENT_SCALE_RESOLVED_PERSISTED,
        EVENT_BAD_TICK_IGNORED,
        EVENT_TERMINAL_STABILIZED,
        EVENT_RAW_APPROVED_DEDUPED,
        EVENT_REQUIRES_QUOTE_SOURCE,
    }
)

DEFAULT_AUDIT_PATH = Path("artifacts/premium_market_data_audit.jsonl")


def audit_path() -> Path:
    raw = os.environ.get("KAI_PREMIUM_AUDIT_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_AUDIT_PATH


def append_premium_audit(event: str, *, path: Path | None = None, **fields: Any) -> dict[str, Any]:
    """Append one audit record and return it (so callers/tests can assert)."""
    rec: dict[str, Any] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    target = path or audit_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:  # observability must not break the pipeline
        logger.warning("[premium_audit] write failed event=%s: %s", event, exc)
    return rec


__all__ = [
    "ALL_EVENTS",
    "EVENT_BAD_TICK_IGNORED",
    "EVENT_OUTLIER_REJECTED",
    "EVENT_RAW_APPROVED_DEDUPED",
    "EVENT_REQUIRES_QUOTE_SOURCE",
    "EVENT_SCALE_RESOLVED_PERSISTED",
    "EVENT_SCALE_UNRESOLVED",
    "EVENT_TERMINAL_STABILIZED",
    "append_premium_audit",
    "audit_path",
]
