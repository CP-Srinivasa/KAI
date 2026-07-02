"""Aggregator for ``order_created_rejected_duplicate`` audit events.

Sprint C (2026-05-12) wired the paper engine to write one audit row
every time a ``create_order`` call hits a known idempotency_key. That
keeps replay-guards forensically visible but produces noise on long-
lived envelopes — the 2026-05-26 baseline had 30 Q/USDT rejections
sharing the single key ``opbridge:ENV-20260509162116-6c51f5bf``.

This module is read-only. It buckets events by idempotency_key with
first/last seen timestamps so the operator can see at a glance whether
a "thing keeps replaying" or "many different replays happened once".
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_AUDIT = Path("artifacts/paper_execution_audit.jsonl")
_REJECT_EVENT = "order_created_rejected_duplicate"


@dataclass
class _KeyBucket:
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    symbol: str = "?"
    side: str = "?"


BucketRow = dict[str, str | int]


@dataclass(frozen=True)
class PaperDuplicateRejectionSummary:
    total: int
    by_idempotency_key: list[BucketRow]
    first_rejected_at: str | None
    last_rejected_at: str | None
    audit_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "by_idempotency_key": self.by_idempotency_key,
            "first_rejected_at": self.first_rejected_at,
            "last_rejected_at": self.last_rejected_at,
            "audit_path": self.audit_path,
        }


def build_paper_duplicate_rejection_summary(
    *,
    audit_path: str | Path = _DEFAULT_AUDIT,
) -> PaperDuplicateRejectionSummary:
    path = Path(audit_path)
    buckets: dict[str, _KeyBucket] = defaultdict(_KeyBucket)
    total = 0
    first_overall: str | None = None
    last_overall: str | None = None

    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("event_type") != _REJECT_EVENT:
                continue
            total += 1
            key = str(rec.get("idempotency_key", "?"))
            ts = str(rec.get("rejected_at") or rec.get("timestamp_utc") or "")
            bucket = buckets[key]
            bucket.count += 1
            if not bucket.first_seen or (ts and ts < bucket.first_seen):
                bucket.first_seen = ts
            if not bucket.last_seen or (ts and ts > bucket.last_seen):
                bucket.last_seen = ts
            bucket.symbol = str(rec.get("symbol", bucket.symbol or "?"))
            bucket.side = str(rec.get("side", bucket.side or "?"))
            if ts:
                if first_overall is None or ts < first_overall:
                    first_overall = ts
                if last_overall is None or ts > last_overall:
                    last_overall = ts

    by_key: list[BucketRow] = []
    for key, bucket in buckets.items():
        row: BucketRow = {
            "idempotency_key": key,
            "count": bucket.count,
            "first_seen": bucket.first_seen,
            "last_seen": bucket.last_seen,
            "symbol": bucket.symbol,
            "side": bucket.side,
        }
        by_key.append(row)

    def _sort_key(row: BucketRow) -> tuple[int, str]:
        count_val = row["count"]
        count = count_val if isinstance(count_val, int) else 0
        key_val = row["idempotency_key"]
        key = key_val if isinstance(key_val, str) else ""
        return (-count, key)

    by_key.sort(key=_sort_key)

    return PaperDuplicateRejectionSummary(
        total=total,
        by_idempotency_key=by_key,
        first_rejected_at=first_overall,
        last_rejected_at=last_overall,
        audit_path=str(path),
    )


__all__ = [
    "PaperDuplicateRejectionSummary",
    "build_paper_duplicate_rejection_summary",
]
