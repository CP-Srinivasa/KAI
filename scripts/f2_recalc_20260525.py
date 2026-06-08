"""F2 D-142 Bearish Re-Evaluation Recalc — 2026-05-25 (live, on-Pi).

Pi-Run-Only. Wiederholt den F2-Recalc vom 24.05. einen Tag später, um die
Operator-Anforderung 'F1-sample-recalc fehlt' final zu schließen.
"""
from __future__ import annotations

import json
import math
import sqlite3


def wilson_low(hits: int, n: int, z: float = 1.96) -> float | None:
    if n == 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (center - margin) / denom) * 100.0


def prec(hits: int, n: int) -> float | None:
    return (hits / n * 100.0) if n else None


def main() -> None:
    outcomes: dict[str, str] = {}
    with open("artifacts/alert_outcomes.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            doc = d.get("document_id")
            o = d.get("outcome")
            if doc and o:
                outcomes[doc] = o

    db = sqlite3.connect("data/dev.db")
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute(
        """
        SELECT id, external_id, sentiment_label, priority_score, published_at, source_name
        FROM canonical_documents
        WHERE published_at >= ?
          AND sentiment_label IN ('bullish','bearish')
        """,
        ("2026-04-01",),
    )
    rows = cur.fetchall()

    bull_hit = bull_miss = bull_inc = 0
    bear_hit = bear_miss = bear_inc = 0
    bear_p8_h = bear_p8_m = 0
    bear_p10_h = bear_p10_m = 0
    bear_low_p_h = bear_low_p_m = 0
    bear_sources: dict[str, int] = {}
    unannotated = 0

    for r in rows:
        o = outcomes.get(str(r["id"])) or outcomes.get(str(r["external_id"] or ""))
        label = r["sentiment_label"]
        if o is None:
            unannotated += 1
            continue
        if label == "bullish":
            if o == "hit":
                bull_hit += 1
            elif o == "miss":
                bull_miss += 1
            else:
                bull_inc += 1
        else:
            if o == "hit":
                bear_hit += 1
            elif o == "miss":
                bear_miss += 1
            else:
                bear_inc += 1
            if o in ("hit", "miss") and r["priority_score"] is not None:
                p = r["priority_score"]
                if p >= 10:
                    if o == "hit":
                        bear_p10_h += 1
                    else:
                        bear_p10_m += 1
                elif p >= 8:
                    if o == "hit":
                        bear_p8_h += 1
                    else:
                        bear_p8_m += 1
                else:
                    if o == "hit":
                        bear_low_p_h += 1
                    else:
                        bear_low_p_m += 1
            src = r["source_name"] or "unknown"
            bear_sources[src] = bear_sources.get(src, 0) + 1

    total = len(rows)
    bull_n = bull_hit + bull_miss
    bear_n = bear_hit + bear_miss
    bp8_n = bear_p8_h + bear_p8_m
    bp10_n = bear_p10_h + bear_p10_m
    bplow_n = bear_low_p_h + bear_low_p_m

    print("=== F2-RECALC LIVE (Pi, 2026-05-25, Window 2026-04-01..now) ===")
    print(f"Q2 directional documents total: {total}")
    print(f"  bullish resolved hits/miss/inc: {bull_hit}/{bull_miss}/{bull_inc}")
    print(f"  bearish resolved hits/miss/inc: {bear_hit}/{bear_miss}/{bear_inc}")
    print(f"  unannotated:                    {unannotated}")
    print()
    print("=== Precision (excludes inconclusive) ===")
    print(
        f"bullish: hit={bull_hit:>3} miss={bull_miss:>3} n={bull_n}  "
        f"precision={prec(bull_hit, bull_n)}%  WilsonLow95={wilson_low(bull_hit, bull_n)}%"
    )
    print(
        f"bearish: hit={bear_hit:>3} miss={bear_miss:>3} n={bear_n}  "
        f"precision={prec(bear_hit, bear_n)}%  WilsonLow95={wilson_low(bear_hit, bear_n)}%"
    )
    print()
    print("=== Bearish Per-Priority ===")
    print(f"bearish p<8:  hit={bear_low_p_h} miss={bear_low_p_m} n={bplow_n}  precision={prec(bear_low_p_h, bplow_n)}%")
    print(f"bearish p=8/9: hit={bear_p8_h} miss={bear_p8_m} n={bp8_n}  precision={prec(bear_p8_h, bp8_n)}%")
    print(f"bearish p>=10: hit={bear_p10_h} miss={bear_p10_m} n={bp10_n}  precision={prec(bear_p10_h, bp10_n)}%")
    print()
    print("=== Bearish Sources (top 10) ===")
    for s, n in sorted(bear_sources.items(), key=lambda x: -x[1])[:10]:
        print(f"  {s}: {n}")


if __name__ == "__main__":
    main()
