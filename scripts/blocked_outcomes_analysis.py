"""Recall-proxy blocked_outcomes report — runs ON the Pi via `python3 - ` stdin.

Reads artifacts/blocked_outcomes.jsonl + blocked_alerts.jsonl (cwd = repo root),
prints total count + would-have-precision per block_reason, plus the
low_directional_confidence bullish breakdown by confidence bucket. D-227 / D-148.
"""

import json
from collections import defaultdict

meta = {}
try:
    for line in open("artifacts/blocked_alerts.jsonl"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        meta[str(d.get("document_id"))] = (
            d.get("block_reason") or "unknown",
            (d.get("sentiment_label") or "").lower(),
            d.get("directional_confidence"),
        )
except FileNotFoundError:
    print("blocked_alerts.jsonl missing")

total = 0
by_reason = defaultdict(lambda: {"hit": 0, "miss": 0, "inc": 0})
bull_lowconf = defaultdict(lambda: {"hit": 0, "miss": 0})
try:
    for line in open("artifacts/blocked_outcomes.jsonl"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        total += 1
        o = (d.get("outcome") or "").lower()
        m = meta.get(str(d.get("document_id")))
        reason = m[0] if m else "unknown"
        if o in ("hit", "miss"):
            by_reason[reason][o] += 1
        else:
            by_reason[reason]["inc"] += 1
        if (
            m
            and reason == "low_directional_confidence"
            and m[1] == "bullish"
            and m[2] is not None
            and o in ("hit", "miss")
        ):
            conf = float(m[2])
            b = "0.7" if conf >= 0.7 else ("0.6" if conf >= 0.6 else "<0.6")
            bull_lowconf[b][o] += 1
except FileNotFoundError:
    print("blocked_outcomes.jsonl missing — proxy has not run yet")

print(f"TOTAL blocked_outcomes = {total}  (Startwert 2026-05-29 12:23 = 65; Delta = {total - 65})")
print("=== would-have-precision per block_reason (hit = recall loss) ===")
for r in sorted(by_reason, key=lambda x: -(by_reason[x]["hit"] + by_reason[x]["miss"])):
    v = by_reason[r]
    t = v["hit"] + v["miss"]
    p = f"{100 * v['hit'] / t:.0f}%" if t else "-"
    print(f"  {r:30s} hit={v['hit']:4d} miss={v['miss']:4d} inc={v['inc']:4d} prec={p} (n={t})")
print("=== low_directional_confidence BULLISH by conf bucket ===")
if not bull_lowconf:
    print("  (none resolved yet)")
for b in sorted(bull_lowconf):
    v = bull_lowconf[b]
    t = v["hit"] + v["miss"]
    p = f"{100 * v['hit'] / t:.0f}%" if t else "-"
    print(f"  conf {b:5s} hit={v['hit']:3d} miss={v['miss']:3d} would-have-prec={p} (n={t})")
