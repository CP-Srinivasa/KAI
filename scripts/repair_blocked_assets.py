"""One-shot repair: backfill ``blocked_assets`` on recent blocked-alert records.

D-227 follow-up (2026-05-29). The pre-D-227 dispatch path persisted blocked
directional alerts *without* ``blocked_assets`` because the early eligibility
gates return before asset resolution. That left the D-148 recall proxy with no
symbol to evaluate, so the 0.6-0.8 bullish would-have-precision is
unmeasurable. The live path is fixed going forward; this script fills the field
for *recent* historical records by joining ``document_id`` ->
``canonical_documents.tickers`` and resolving to tradeable symbols with the
same resolver the live path uses — so the recall proxy can resolve ~3 days of
backlog immediately instead of forward-only.

Safety (KAI audit-integrity rules):
- ``--dry-run`` is the DEFAULT — reports only, writes nothing. Use ``--apply``.
- ``--apply`` always writes a timestamped backup before mutating.
- Atomic write (temp file + ``os.replace``) — never a half-written audit.
- Only touches records whose ``blocked_assets`` is empty AND whose
  ``blocked_at`` falls within ``--since-days`` (default 5; matches the
  annotator's 72h+ usable window with margin).
- Never deletes a record and never alters any other field. It only fills the
  ``blocked_assets`` that the upstream analysis already determined (``tickers``)
  — a faithful repair, not fabrication.

Usage:
    python -m scripts.repair_blocked_assets                 # dry-run
    python -m scripts.repair_blocked_assets --apply         # mutate + backup
    python -m scripts.repair_blocked_assets --since-days 7 --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Reuse the exact resolver the live dispatch path uses — no logic drift.
from app.alerts.eligibility import resolve_eligible_symbols


@dataclass
class RepairStats:
    scanned: int = 0
    already_populated: int = 0
    out_of_window: int = 0
    no_ticker_match: int = 0
    unresolvable: int = 0
    repaired: int = 0


def _parse_blocked_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def repair_records(
    records: list[dict[str, object]],
    tickers_by_doc: dict[str, list[str]],
    *,
    since_days: float,
    now: datetime,
) -> tuple[list[dict[str, object]], RepairStats]:
    """Return repaired records + stats.

    Pure function (no I/O) so it is unit-testable. A record is repaired only
    when its ``blocked_assets`` is empty, it is within the time window, its
    ``document_id`` maps to tickers, and those tickers resolve to >=1 tradeable
    symbol. All other records pass through byte-for-byte unchanged.
    """
    stats = RepairStats()
    cutoff = now - timedelta(days=since_days)
    out: list[dict[str, object]] = []
    for rec in records:
        stats.scanned += 1
        if rec.get("blocked_assets"):
            stats.already_populated += 1
            out.append(rec)
            continue
        blocked_at = _parse_blocked_at(rec.get("blocked_at"))
        if blocked_at is None or blocked_at < cutoff:
            stats.out_of_window += 1
            out.append(rec)
            continue
        tickers = tickers_by_doc.get(str(rec.get("document_id")))
        if not tickers:
            stats.no_ticker_match += 1
            out.append(rec)
            continue
        symbols = resolve_eligible_symbols(list(tickers))
        if not symbols:
            stats.unresolvable += 1
            out.append(rec)
            continue
        repaired = dict(rec)
        repaired["blocked_assets"] = symbols
        repaired["blocked_assets_repaired"] = True  # provenance marker
        stats.repaired += 1
        out.append(repaired)
    return out, stats


def _load_tickers_by_doc(db_path: Path) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    con = sqlite3.connect(str(db_path))
    try:
        for doc_id, tickers_json in con.execute(
            "SELECT id, tickers FROM canonical_documents WHERE tickers IS NOT NULL"
        ):
            if not tickers_json:
                continue
            try:
                tickers = json.loads(tickers_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(tickers, list) and tickers:
                mapping[str(doc_id)] = [str(t) for t in tickers]
    finally:
        con.close()
    return mapping


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _atomic_write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", default="artifacts", type=Path)
    parser.add_argument("--db", default="data/dev.db", type=Path)
    parser.add_argument("--since-days", default=5.0, type=float)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate the file (with backup). Default is dry-run.",
    )
    args = parser.parse_args(argv)

    audit_path = args.audit_dir / "blocked_alerts.jsonl"
    if not audit_path.exists():
        print(f"missing: {audit_path}", file=sys.stderr)
        return 1
    if not args.db.exists():
        print(f"missing: {args.db}", file=sys.stderr)
        return 1

    records = _load_jsonl(audit_path)
    tickers_by_doc = _load_tickers_by_doc(args.db)
    repaired, stats = repair_records(
        records, tickers_by_doc, since_days=args.since_days, now=datetime.now(UTC)
    )

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[{mode}] scanned={stats.scanned} repaired={stats.repaired} "
        f"already_populated={stats.already_populated} out_of_window={stats.out_of_window} "
        f"no_ticker_match={stats.no_ticker_match} unresolvable={stats.unresolvable}"
    )

    if not args.apply:
        print("dry-run: no files written. Re-run with --apply to persist.")
        return 0
    if stats.repaired == 0:
        print("nothing to repair — file untouched.")
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = audit_path.with_name(f"blocked_alerts.jsonl.bak.{stamp}")
    shutil.copy2(audit_path, backup)
    _atomic_write_jsonl(audit_path, repaired)
    print(f"backup: {backup}")
    print(f"wrote: {audit_path} ({stats.repaired} records repaired)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
