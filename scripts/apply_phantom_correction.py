"""Apply a one-time portfolio_correction backing out phantom-close PnL (DS-20260529-V1).

The MATIC phantom closes (BitMEX delisted-instrument price 0.40875 vs real
~0.088) inflated the paper book's realized PnL and cash by the same amount
(each round-trip's cash delta equals its trade PnL, and MATIC ends flat). This
script computes that amount via phantom_close_forensics, then appends ONE
``portfolio_correction`` audit event that replay_paper_audit honors as an
explicit delta to realized_pnl_usd and cash_usd.

Safety:
  - Dry-run by default; ``--apply`` is required to write.
  - Idempotent: a correction carrying the same ``correction_id`` is written at
    most once (re-runs detect it and abort).
  - Append-only under the same file lock the engine uses — never rewrites or
    deletes existing audit rows.

    python -m scripts.apply_phantom_correction            # dry-run
    python -m scripts.apply_phantom_correction --apply     # write the correction
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.phantom_close_forensics import scan

from app.core.file_lock import append_lock

_AUDIT = Path("artifacts/paper_execution_audit.jsonl")
_CORRECTION_ID = "matic_phantom_DS-20260529-V1"


def _existing_correction(path: Path, correction_id: str) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "portfolio_correction" not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            rec.get("event_type") == "portfolio_correction"
            and rec.get("correction_id") == correction_id
        ):
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", type=Path, default=_AUDIT)
    ap.add_argument("--threshold-pct", type=float, default=200.0)
    ap.add_argument("--apply", action="store_true", help="Write the correction (default: dry-run).")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"audit not found: {args.path}")
        return

    if _existing_correction(args.path, _CORRECTION_ID):
        print(f"correction already present (correction_id={_CORRECTION_ID}) — nothing to do.")
        return

    report = scan(args.path, args.threshold_pct / 100.0)
    phantom = report["phantom_pnl_usd"]
    if report["phantom_count"] == 0 or phantom == 0:
        print("no phantom closes detected — nothing to correct.")
        return

    delta = -float(phantom)
    record = {
        "schema_version": "v2",
        "event_type": "portfolio_correction",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "correction_id": _CORRECTION_ID,
        "realized_pnl_delta_usd": delta,
        "cash_delta_usd": delta,
        "reason": "matic_phantom_quarantine: back out BitMEX delisted-instrument phantom closes",
        "phantom_count": report["phantom_count"],
        "raw_cumulative_before": report["raw_cumulative_realized_usd"],
        "corrected_cumulative_after": report["corrected_cumulative_realized_usd"],
    }

    print(json.dumps(record, indent=2))
    if not args.apply:
        print("\nDRY-RUN — re-run with --apply to write this correction.")
        return

    with append_lock(args.path):
        with args.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    print(f"\nWROTE portfolio_correction (delta={delta:.2f}) to {args.path}")


if __name__ == "__main__":
    main()
