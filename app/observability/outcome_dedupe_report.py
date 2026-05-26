"""Outcome dedupe report — raw vs latest-per-document_id.

The 2026-05-26 daily-strategy review reported 4409 raw rows /
3981 inconclusive vs 410 unique documents / 35 inconclusive once
deduped to the latest row per ``document_id``. Multi-Window-Outcome
(PR #74) writes new rows for each later window, so the raw aggregate
buries the resolved outcomes under historic inconclusives.

This module is read-only. It returns both raw and latest-per-document
counts so the operator (and the daily-strategy bootstrap, future use)
can decide on the deduped basis without re-implementing the rule per
caller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_AUDIT = Path("artifacts/alert_outcomes.jsonl")


def _precision(hit: int, miss: int) -> str:
    decided = hit + miss
    if decided == 0:
        return "n/a"
    return f"{100.0 * hit / decided:.1f}% ({hit}/{decided})"


@dataclass(frozen=True)
class OutcomeDedupeReport:
    raw_total: int
    raw_hit: int
    raw_miss: int
    raw_inconclusive: int
    deduped_total: int
    deduped_hit: int
    deduped_miss: int
    deduped_inconclusive: int
    dropped_inconclusive_dupes: int
    audit_path: str

    @property
    def raw_precision_str(self) -> str:
        return _precision(self.raw_hit, self.raw_miss)

    @property
    def deduped_precision_str(self) -> str:
        return _precision(self.deduped_hit, self.deduped_miss)

    def to_dict(self) -> dict[str, object]:
        return {
            "raw_total": self.raw_total,
            "raw_hit": self.raw_hit,
            "raw_miss": self.raw_miss,
            "raw_inconclusive": self.raw_inconclusive,
            "raw_precision": self.raw_precision_str,
            "deduped_total": self.deduped_total,
            "deduped_hit": self.deduped_hit,
            "deduped_miss": self.deduped_miss,
            "deduped_inconclusive": self.deduped_inconclusive,
            "deduped_precision": self.deduped_precision_str,
            "dropped_inconclusive_dupes": self.dropped_inconclusive_dupes,
            "audit_path": self.audit_path,
        }


def build_outcome_dedupe_report(
    *,
    audit_path: str | Path = _DEFAULT_AUDIT,
) -> OutcomeDedupeReport:
    path = Path(audit_path)
    raw_hit = 0
    raw_miss = 0
    raw_inconclusive = 0
    raw_total = 0
    latest: dict[str, dict[str, object]] = {}
    raw_inconclusive_by_doc: dict[str, int] = {}

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
            raw_total += 1
            outcome = rec.get("outcome")
            if outcome == "hit":
                raw_hit += 1
            elif outcome == "miss":
                raw_miss += 1
            elif outcome == "inconclusive":
                raw_inconclusive += 1
            doc_id = rec.get("document_id")
            if isinstance(doc_id, str) and doc_id:
                # The "latest" rule honours the file's append order — the
                # last write per document_id wins, mirroring the storage
                # contract used by app/regime/lookup.py.
                latest[doc_id] = rec
                if outcome == "inconclusive":
                    raw_inconclusive_by_doc[doc_id] = raw_inconclusive_by_doc.get(doc_id, 0) + 1

    deduped_hit = 0
    deduped_miss = 0
    deduped_inconclusive = 0
    dropped_inconclusive_dupes = 0
    for doc_id, rec in latest.items():
        outcome = rec.get("outcome")
        if outcome == "hit":
            deduped_hit += 1
        elif outcome == "miss":
            deduped_miss += 1
        elif outcome == "inconclusive":
            deduped_inconclusive += 1
        # Count redundant inconclusive rows superseded by a later
        # resolved outcome (hit/miss). Inconclusives that stay
        # inconclusive after dedupe are not "dropped" — only the
        # extras over the final state count.
        per_doc_inc = raw_inconclusive_by_doc.get(doc_id, 0)
        if outcome in {"hit", "miss"}:
            dropped_inconclusive_dupes += per_doc_inc
        elif outcome == "inconclusive" and per_doc_inc > 1:
            dropped_inconclusive_dupes += per_doc_inc - 1

    return OutcomeDedupeReport(
        raw_total=raw_total,
        raw_hit=raw_hit,
        raw_miss=raw_miss,
        raw_inconclusive=raw_inconclusive,
        deduped_total=len(latest),
        deduped_hit=deduped_hit,
        deduped_miss=deduped_miss,
        deduped_inconclusive=deduped_inconclusive,
        dropped_inconclusive_dupes=dropped_inconclusive_dupes,
        audit_path=str(path),
    )


__all__ = ["OutcomeDedupeReport", "build_outcome_dedupe_report"]
