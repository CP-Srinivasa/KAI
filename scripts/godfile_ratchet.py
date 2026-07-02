#!/usr/bin/env python3
"""God-file ratchet (Sprint S7, D-234) — Abbau über Monate, mechanisch erzwungen.

Die fünf benannten God-Files dürfen nur SCHRUMPFEN: dieses CI-Gate vergleicht
ihre aktuelle Zeilenzahl gegen die eingecheckte Baseline
(``scripts/godfile_baseline.json``) und schlägt fehl, sobald eine Datei über
ihrer Baseline liegt. Damit ist die S7-Regel („Wer ein God-File anfasst,
extrahiert das berührte Segment in ein Modul mit Tests") kein Appell, sondern
ein Merge-Gate: Wachstum erfordert entweder eine Extraktion im selben PR —
oder eine bewusste, im Diff sichtbare Baseline-Erhöhung, die der Review
rechtfertigen muss.

``--update`` zieht die Baseline auf die aktuellen Werte NACH UNTEN (nie nach
oben) — nach jeder Extraktion einchecken, damit der Fortschritt verriegelt ist.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "godfile_baseline.json"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _count_lines(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="God-file line-count ratchet (down-only)")
    parser.add_argument(
        "--update",
        action="store_true",
        help="tighten baselines down to current line counts (never up)",
    )
    args = parser.parse_args(argv)

    baseline: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    violations: list[str] = []
    updated = False

    for rel_path, max_lines in sorted(baseline.items()):
        current = _count_lines(REPO_ROOT / rel_path)
        if current is None:
            # Datei verschwunden (final aufgeteilt) → Eintrag manuell entfernen.
            print(f"[ratchet] {rel_path}: file gone — remove it from the baseline")
            violations.append(rel_path)
            continue
        if current > max_lines:
            violations.append(rel_path)
            print(
                f"[ratchet] FAIL {rel_path}: {current} lines > baseline {max_lines} "
                f"(+{current - max_lines}). Extract the touched segment into a module "
                "with tests (docs/runbooks/repo_hygiene_policy.md §5) — or raise the "
                "baseline consciously in this PR and justify it in the PR body."
            )
        elif current < max_lines:
            if args.update:
                baseline[rel_path] = current
                updated = True
                print(f"[ratchet] tightened {rel_path}: {max_lines} -> {current}")
            else:
                print(
                    f"[ratchet] ok {rel_path}: {current}/{max_lines} "
                    f"(headroom {max_lines - current} — run --update to lock it in)"
                )
        else:
            print(f"[ratchet] ok {rel_path}: {current}/{max_lines}")

    if updated:
        BASELINE_PATH.write_text(
            json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"[ratchet] baseline written -> {BASELINE_PATH}")

    if violations:
        print(f"[ratchet] {len(violations)} violation(s).")
        return 1
    print("[ratchet] all god-files at or below baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
