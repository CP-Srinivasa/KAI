#!/usr/bin/env python
"""Check DECISION_LOG.md for ID-gaps, duplicates, and non-monotonic order.

Usage:
    python scripts/check_decision_log.py           # report, exit 0 if clean, 1 on issues
    python scripts/check_decision_log.py --quiet   # no stdout unless issues

Answers NEO-F-META-20260424-014: the 188-entry DECISION_LOG has 9 missing
D-IDs (D-110, D-112, D-114, D-116, D-144) and one placeholder (D-174). This
script surfaces the drift so future edits can be audited before commit.

Intended to be wired into .git/hooks/pre-commit as a warning (not a hard
block) — governance hygiene, not a gate.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECISION_LOG = ROOT / "DECISION_LOG.md"
HEADER_RE = re.compile(r"^###\s+D-(\d+)\b", re.MULTILINE)


def parse_ids(text: str) -> list[int]:
    return [int(m.group(1)) for m in HEADER_RE.finditer(text)]


def find_gaps(ids: list[int]) -> list[int]:
    """Return IDs present in the [min, max] interval but missing in the log."""
    if not ids:
        return []
    lo, hi = min(ids), max(ids)
    have = set(ids)
    return [i for i in range(lo, hi + 1) if i not in have]


def find_duplicates(ids: list[int]) -> list[int]:
    seen: set[int] = set()
    dups: list[int] = []
    for i in ids:
        if i in seen and i not in dups:
            dups.append(i)
        seen.add(i)
    return dups


def check_monotonic_order(ids: list[int]) -> list[tuple[int, int, int]]:
    """Newest first is the convention. Flag pairs where earlier line has smaller ID.

    Returns list of (line_index, prev_id, this_id) tuples for anomalies.
    """
    anomalies: list[tuple[int, int, int]] = []
    for i in range(1, len(ids)):
        # Log reads top-down: header at position 0 is newest. Later lines should
        # have STRICTLY smaller IDs. Equal IDs are reported as duplicates.
        if ids[i] > ids[i - 1]:
            anomalies.append((i, ids[i - 1], ids[i]))
    return anomalies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="No stdout when clean")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on gaps/duplicates (default: always exit 0, report-only — safe for pre-commit hook)",
    )
    parser.add_argument(
        "--check-order",
        action="store_true",
        help="Also flag non-monotonic order (default off — DECISION_LOG has mixed-order historical section)",
    )
    args = parser.parse_args()

    if not DECISION_LOG.exists():
        print(f"ERROR: {DECISION_LOG} not found", file=sys.stderr)
        return 2

    text = DECISION_LOG.read_text(encoding="utf-8")
    ids = parse_ids(text)

    gaps = find_gaps(ids)
    dups = find_duplicates(ids)
    order_anomalies = check_monotonic_order(ids) if args.check_order else []

    hard_issues = bool(gaps or dups)
    issues_found = hard_issues or bool(order_anomalies)

    if not args.quiet or issues_found:
        print(f"DECISION_LOG: {len(ids)} entries, range D-{min(ids)}..D-{max(ids)}")

    if gaps:
        print(f"  missing IDs ({len(gaps)}): {', '.join('D-' + str(g) for g in gaps)}")

    if dups:
        print(f"  duplicate IDs ({len(dups)}): {', '.join('D-' + str(d) for d in dups)}")

    if order_anomalies:
        print(f"  order anomalies ({len(order_anomalies)}): "
              + ", ".join(f"D-{prev} before D-{cur}" for _, prev, cur in order_anomalies))

    if not issues_found and not args.quiet:
        print("  OK — no gaps, duplicates" + (", or order anomalies" if args.check_order else ""))

    # Default exit 0 (report-only, safe for pre-commit warning).
    # --strict → exit 1 on hard issues (gaps/duplicates; order is never a strict fail).
    return 1 if (args.strict and hard_issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
