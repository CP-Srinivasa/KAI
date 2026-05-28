#!/usr/bin/env python3
"""Bearish block-volume report (DS-20260528-V5) — decision-prep for 2026-06-15.

Read-only aggregate of ``bearish_directional_disabled`` blocks over a rolling
window. Answers "how much bearish directional flow does D-142 currently
suppress, and of what quality" — the volume side of the F2-V2 decision.

This DECIDES NOTHING. Bearish precision (would-they-have-been-hits) is NOT
computable here: D-142 disables bearish, so the auto-annotator never produces
bearish outcomes. That question lives in scripts/f2_v2_bearish_reeval.py and is
deferred to 2026-06-15 by sample size (n<30). This report only quantifies the
suppressed volume + its priority/confidence/source profile + sample headlines.

Usage:
    python scripts/bearish_block_volume.py
    python scripts/bearish_block_volume.py --days 14 --out-md artifacts/operator_memos/bearish_blocks_2026-05-28.md
    python scripts/bearish_block_volume.py --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_BEARISH_BLOCK_REASON = "bearish_directional_disabled"


def priority_bucket(priority: int | None) -> str:
    if priority is None:
        return "unknown"
    if priority >= 10:
        return "p>=10"
    if priority >= 8:
        return "p=8/9"
    return "p<8"


def window_start(today: date, days: int) -> date:
    return today - timedelta(days=days)


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).date()
    except (ValueError, AttributeError):
        return None


def aggregate_bearish_blocks(
    records: list[dict],
    today: date,
    days: int = 14,
) -> dict[str, object]:
    """Aggregate bearish_directional_disabled blocks within the rolling window.

    Pure: takes raw dict rows, returns a structured summary. No IO.
    """
    start = window_start(today, days)
    per_day: Counter[str] = Counter()
    by_priority: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    confidences: list[float] = []
    samples: list[dict[str, object]] = []
    total = 0

    for rec in records:
        if rec.get("block_reason") != _BEARISH_BLOCK_REASON:
            continue
        d = _parse_day(rec.get("blocked_at"))
        if d is None or d < start or d > today:
            continue
        total += 1
        per_day[d.isoformat()] += 1
        by_priority[priority_bucket(rec.get("priority"))] += 1
        by_source[rec.get("source_name") or "?"] += 1
        conf = rec.get("directional_confidence")
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))
        samples.append(
            {
                "priority": rec.get("priority"),
                "confidence": conf,
                "source": rec.get("source_name"),
                "title": rec.get("normalized_title"),
            }
        )

    n_days = max(1, (today - start).days)
    top_samples = sorted(
        samples,
        key=lambda s: s["priority"] if isinstance(s["priority"], int) else -1,
        reverse=True,
    )[:10]

    return {
        "window_start": start.isoformat(),
        "today": today.isoformat(),
        "window_days": days,
        "total_bearish_blocks": total,
        "blocks_per_day_avg": round(total / n_days, 2),
        "per_day": dict(sorted(per_day.items())),
        "by_priority": dict(by_priority),
        "by_source": dict(by_source.most_common(10)),
        "confidence": {
            "n_with_confidence": len(confidences),
            "mean": round(statistics.mean(confidences), 3) if confidences else None,
            "median": round(statistics.median(confidences), 3) if confidences else None,
            "ge_0_7": sum(1 for c in confidences if c >= 0.7),
        },
        "top_samples": top_samples,
    }


def render_memo(agg: dict[str, object]) -> str:
    conf = agg["confidence"]
    assert isinstance(conf, dict)
    lines = [
        f"# Bearish Block-Volume Report — {agg['today']}",
        "",
        "**Read-only. Decision-prep für 2026-06-15. D-142 bleibt — dieser Report",
        "quantifiziert nur das unterdrückte bearish-Volumen, keine Precision.**",
        "Precision-Frage: siehe `scripts/f2_v2_bearish_reeval.py` (vertagt, n<30).",
        "",
        f"Window: {agg['window_start']} → {agg['today']} ({agg['window_days']}d)",
        "",
        "## Volumen",
        f"- bearish_directional_disabled gesamt: **{agg['total_bearish_blocks']}**",
        f"- ⌀ Blocks/Tag: {agg['blocks_per_day_avg']}",
        "",
        "## Priority-Profil",
        "| Bucket | Blocks |",
        "|---|---:|",
    ]
    by_priority = agg["by_priority"]
    assert isinstance(by_priority, dict)
    for b in ("p>=10", "p=8/9", "p<8", "unknown"):
        if b in by_priority:
            lines.append(f"| {b} | {by_priority[b]} |")
    lines += [
        "",
        "## Directional-Confidence (der geblockten bearish)",
        f"- mit Confidence: {conf['n_with_confidence']}  ·  ⌀ {conf['mean']}  ·  "
        f"Median {conf['median']}  ·  >=0.7: {conf['ge_0_7']}",
        "",
        "## Top-Quellen",
        "| Source | Blocks |",
        "|---|---:|",
    ]
    by_source = agg["by_source"]
    assert isinstance(by_source, dict)
    for src, cnt in by_source.items():
        lines.append(f"| {src} | {cnt} |")
    lines += [
        "",
        "## Sample-Headlines (höchste Priority, max 10)",
        "| Prio | Conf | Source | Headline |",
        "|---:|---:|---|---|",
    ]
    top = agg["top_samples"]
    assert isinstance(top, list)
    for s in top:
        assert isinstance(s, dict)
        lines.append(f"| {s['priority']} | {s['confidence']} | {s['source']} | {s['title']} |")
    return "\n".join(lines) + "\n"


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bearish block-volume report (read-only).")
    ap.add_argument("--blocked", type=Path, default=Path("artifacts/blocked_alerts.jsonl"))
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument(
        "--today", type=lambda s: date.fromisoformat(s), default=datetime.now(UTC).date()
    )
    ap.add_argument("--out-md", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    agg = aggregate_bearish_blocks(_read_jsonl(args.blocked), args.today, args.days)

    if args.json:
        sys.stdout.write(json.dumps(agg, indent=2))
        sys.stdout.write("\n")
        return 0

    memo = render_memo(agg)
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(memo, encoding="utf-8")
        sys.stderr.write(f"wrote: {args.out_md}\n")
    else:
        sys.stdout.write(memo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
