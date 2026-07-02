"""Outcome-Dedupe Bestandssicht — RAW vs LATEST vs BEST-OUTCOME per document_id.

Zweck (DS-20260527-V3): Auto-Annotator schreibt pro document_id mehrere
outcome rows (Multi-Window: 4h/24h/72h/168h × Recalcs). Bestandszählung über
raw JSONL überschätzt Volumen und verfälscht Precision.

Drei Zählweisen:

- **RAW** — alle Rows; entspricht heutigem ``_resolved_directional_count``.
- **LATEST** — letzte Annotation pro document_id (jüngste annotated_at).
- **BEST** — definitivste Resolution pro document_id (hit > miss > inconclusive),
  bei Gleichstand kleinstes Window (4h bevorzugt vor 168h).

LATEST entspricht dem aktuellen Stand. BEST entspricht "was hat der
Auto-Annotator über die Lebenszeit des Dokuments jemals als sicherste
Resolution markiert".

30.05.-Decision-Pack-Pre-Sprint-relevant: BEST ist die belastbarste Sicht für
Re-Aktivierungs-Schwellen (Wilson-Lower brauchen klare hit/miss-Counts).

Usage:
    python scripts/outcome_dedupe_report.py
    python scripts/outcome_dedupe_report.py --output-md artifacts/outcome_dedupe_20260527.md
    python scripts/outcome_dedupe_report.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_OUTCOME_ORDER = {"hit": 0, "miss": 1, "inconclusive": 2}
_WINDOW_ORDER = {"4h": 0, "24h": 1, "72h": 2, "168h": 3, None: 99, "": 99}


def _load(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _is_better(a: dict[str, object], b: dict[str, object]) -> bool:
    """Return True if record a is a 'better' outcome than b."""
    oa = _OUTCOME_ORDER.get(str(a.get("outcome", "inconclusive")), 9)
    ob = _OUTCOME_ORDER.get(str(b.get("outcome", "inconclusive")), 9)
    if oa != ob:
        return oa < ob
    # Same outcome: prefer earlier window for hits
    if a.get("outcome") == "hit":
        wa = _WINDOW_ORDER.get(a.get("hit_at_window"), 99)
        wb = _WINDOW_ORDER.get(b.get("hit_at_window"), 99)
        return wa < wb
    return False


def _by_asset(records: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in records:
        asset = str(r.get("asset", "?"))
        outcome = str(r.get("outcome", "inconclusive"))
        d = out.setdefault(asset, {"hit": 0, "miss": 0, "inconclusive": 0})
        if outcome in d:
            d[outcome] += 1
    return out


def _precision(hit: int, miss: int) -> float:
    denom = hit + miss
    return 100.0 * hit / denom if denom else 0.0


def _build_report(records: list[dict[str, object]]) -> dict[str, object]:
    # RAW: every row
    raw_counts = {"hit": 0, "miss": 0, "inconclusive": 0}
    for r in records:
        o = str(r.get("outcome", "inconclusive"))
        if o in raw_counts:
            raw_counts[o] += 1
    raw_total = sum(raw_counts.values())

    # LATEST: by annotated_at per document_id
    latest: dict[str, dict[str, object]] = {}
    for r in records:
        doc_id = r.get("document_id")
        if not isinstance(doc_id, str):
            continue
        ts = str(r.get("annotated_at", ""))
        prev = latest.get(doc_id)
        if prev is None or ts > str(prev.get("annotated_at", "")):
            latest[doc_id] = r

    # BEST: definitive outcome per document_id
    best: dict[str, dict[str, object]] = {}
    for r in records:
        doc_id = r.get("document_id")
        if not isinstance(doc_id, str):
            continue
        prev = best.get(doc_id)
        if prev is None or _is_better(r, prev):
            best[doc_id] = r

    latest_counts = {"hit": 0, "miss": 0, "inconclusive": 0}
    for r in latest.values():
        o = str(r.get("outcome", "inconclusive"))
        if o in latest_counts:
            latest_counts[o] += 1

    best_counts = {"hit": 0, "miss": 0, "inconclusive": 0}
    for r in best.values():
        o = str(r.get("outcome", "inconclusive"))
        if o in best_counts:
            best_counts[o] += 1

    unique_docs = len(latest)
    multiplier = raw_total / unique_docs if unique_docs else 0.0

    return {
        "as_of_utc": datetime.now(UTC).isoformat(),
        "raw": {
            **raw_counts,
            "total": raw_total,
            "precision_pct": round(_precision(raw_counts["hit"], raw_counts["miss"]), 2),
        },
        "latest": {
            **latest_counts,
            "total": sum(latest_counts.values()),
            "precision_pct": round(_precision(latest_counts["hit"], latest_counts["miss"]), 2),
        },
        "best": {
            **best_counts,
            "total": sum(best_counts.values()),
            "precision_pct": round(_precision(best_counts["hit"], best_counts["miss"]), 2),
        },
        "unique_documents": unique_docs,
        "avg_rows_per_doc": round(multiplier, 2),
        "by_asset_best": _by_asset(list(best.values())),
    }


def _format_markdown(report: dict[str, object]) -> str:
    raw = report["raw"]
    lat = report["latest"]
    best = report["best"]
    assert isinstance(raw, dict)
    assert isinstance(lat, dict)
    assert isinstance(best, dict)

    md = f"""# Outcome-Dedupe Bestandssicht — {report["as_of_utc"]}

Read-only Aggregation über `artifacts/alert_outcomes.jsonl`. Beantwortet die
Frage: "Wie viele resolved directionals zählen wir wirklich — pro Row, pro
Latest-Annotation pro Doc, oder pro definitivem Best-Outcome?"

## Bestand

| Sicht | hit | miss | inconclusive | total | Precision |
|---|---:|---:|---:|---:|---:|
| **RAW** (alle Rows) | {raw["hit"]} | {raw["miss"]} | {raw["inconclusive"]} | {raw["total"]} | {raw["precision_pct"]}% |
| **LATEST** (letzte Annotation pro doc) | {lat["hit"]} | {lat["miss"]} | {lat["inconclusive"]} | {lat["total"]} | {lat["precision_pct"]}% |
| **BEST** (definitivste Resolution pro doc, hit > miss > inconclusive, frühestes Window bei Hits) | {best["hit"]} | {best["miss"]} | {best["inconclusive"]} | {best["total"]} | {best["precision_pct"]}% |

- **Unique documents**: {report["unique_documents"]}
- **Avg rows per doc**: {report["avg_rows_per_doc"]}× (Multi-Window-Recalc-Inflation)

## Lesart

- **RAW** ist heutige `_resolved_directional_count`-Sicht. Verzerrt das Volumen.
- **LATEST** zeigt "was sagt der Auto-Annotator jetzt zu jedem doc". Empfohlen für aktuelle Precision-Reports.
- **BEST** zeigt "hat dieses doc jemals als hit aufgelöst (egal welches Window)". Empfohlen für Wilson-Lower-Schwellen + Re-Aktivierungs-Entscheidungen, weil hit-Information nicht durch spätere inconclusive-Recomputes überschrieben wird.

## Per-Asset (BEST)

| Asset | hit | miss | inconclusive | resolved | Precision |
|---|---:|---:|---:|---:|---:|
"""
    by_asset = report["by_asset_best"]
    assert isinstance(by_asset, dict)
    rows = []
    for asset, d in by_asset.items():
        resolved = d["hit"] + d["miss"]
        prec = (100.0 * d["hit"] / resolved) if resolved else 0.0
        rows.append((asset, d["hit"], d["miss"], d["inconclusive"], resolved, prec))
    rows.sort(key=lambda x: x[4], reverse=True)
    for asset, h, m, i, r, p in rows:
        md += f"| {asset} | {h} | {m} | {i} | {r} | {p:.1f}% |\n"
    return md


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument(
        "--outcomes-path",
        default="artifacts/alert_outcomes.jsonl",
        help="Path to alert_outcomes.jsonl",
    )
    p.add_argument(
        "--output-md",
        default=None,
        help="If set, write markdown to this path (else stdout)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print JSON report instead of markdown",
    )
    args = p.parse_args(argv)

    records = _load(Path(args.outcomes_path))
    report = _build_report(records)

    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, default=str))
        sys.stdout.write("\n")
        return 0

    md = _format_markdown(report)
    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(md, encoding="utf-8")
        sys.stderr.write(f"wrote: {args.output_md}\n")
    else:
        sys.stdout.write(md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
