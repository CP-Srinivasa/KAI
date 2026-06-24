#!/usr/bin/env python3
"""Evaluate the L2 on-chain evidence shadow log (KAI Sprint 2, B-003).

Reads the raw-feature shadow log (``artifacts/l2_evidence_shadow.jsonl``, written
by the L2 provider in measure-first mode) and an outcomes file, point-in-time joins
them (no look-ahead), and reports — per feature (fee/mempool percentile) — whether
there is a LEARNABLE direction, using an autocorrelation-robust moving-block
bootstrap (NOT a naive hit-rate). Read-only; learns nothing into sizing.

The outcomes file is JSONL with one object per resolved signal:
``{"symbol": "...", "entry_ts": "<iso-utc>", "net_bps": <float>}`` — produce it
from the resolved trade ledger. Without ``--outcomes`` the script only reports how
many measurements have accumulated (the join needs realized outcomes).

Verdict is honest: ``insufficient`` below sample size, ``inconclusive`` when the
bootstrap does not confirm a direction. Trust-promotion stays operator- AND
edge-gated — a learned direction here is a HYPOTHESIS, never an auto-activation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.observability.l2_evidence_eval import evaluate_feature_direction, pit_join


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate L2 on-chain evidence (B-003).")
    ap.add_argument("--shadow", default="artifacts/l2_evidence_shadow.jsonl")
    ap.add_argument("--outcomes", default="")
    ap.add_argument("--min-sample", type=int, default=8)
    args = ap.parse_args(argv)

    measurements = _read_jsonl(Path(args.shadow))
    print(f"l2-eval: {len(measurements)} shadow measurements in {args.shadow}")
    if not measurements:
        print("l2-eval: no measurements yet — enable APP_L2_EVIDENCE_ENABLED and let it run.")
        return 0
    if not args.outcomes:
        print("l2-eval: no --outcomes provided — cannot join. Provide resolved outcomes JSONL.")
        return 0

    outcomes = _read_jsonl(Path(args.outcomes))
    pairs = pit_join(measurements, outcomes)
    print(f"l2-eval: {len(pairs)} point-in-time pairs (no look-ahead)")
    for feature in ("fee_percentile", "mempool_percentile"):
        r = evaluate_feature_direction(pairs, feature_key=feature, min_sample=args.min_sample)
        print(
            f"  {feature}: direction={r['direction']} "
            f"n_high={r['n_high']} n_low={r['n_low']} "
            f"mean_high={r['mean_high']:.2f} mean_low={r['mean_low']:.2f} "
            f"p_high+={r['p_high_positive']} p_low+={r['p_low_positive']}"
        )
    print(
        "l2-eval: a learned direction is a HYPOTHESIS — trust-promotion stays "
        "operator- AND edge-gated (no auto-activation)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
