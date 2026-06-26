#!/usr/bin/env python3
"""Evaluate the Momentum-Universe evidence shadow log (KAI G3, direction-learning).

Reads the shadow log (``artifacts/momentum_evidence_shadow.jsonl``, written by the
momentum provider in measure-first mode) and an outcomes file, point-in-time joins
them (no look-ahead), and reports whether the ``momentum_score`` percentile has a
LEARNABLE forward-return direction — reusing the autocorrelation-robust
moving-block bootstrap from the L2 evaluator (NOT a naive hit-rate). Read-only;
learns nothing into sizing.

The outcomes file is JSONL with one object per resolved signal:
``{"symbol": "...", "entry_ts": "<iso-utc>", "net_bps": <float>}`` — produce it
from the resolved paper-trade ledger (filter the ``momentum_universe`` cohort).
Without ``--outcomes`` the script only reports how many measurements accumulated.

Verdict is honest: ``insufficient`` below sample size, ``inconclusive`` when the
bootstrap does not confirm a direction. A learned direction is a HYPOTHESIS —
promotion of ``APP_MOMENTUM_EVIDENCE_DIRECTION_ALIGNED`` (+1/-1) and
``_SOURCE_TRUST`` stays operator- AND edge-gated (never auto-activated).
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
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate Momentum-Universe evidence (G3).")
    ap.add_argument("--shadow", default="artifacts/momentum_evidence_shadow.jsonl")
    ap.add_argument("--outcomes", default="")
    ap.add_argument("--min-sample", type=int, default=20)
    args = ap.parse_args(argv)

    measurements = _read_jsonl(Path(args.shadow))
    print(f"momentum-eval: {len(measurements)} shadow measurements in {args.shadow}")
    if not measurements:
        print(
            "momentum-eval: no measurements yet — enable APP_MOMENTUM_EVIDENCE_ENABLED "
            "and let the loop run."
        )
        return 0
    if not args.outcomes:
        print(
            "momentum-eval: no --outcomes provided — cannot join. Provide resolved "
            "momentum_universe-cohort outcomes JSONL (symbol/entry_ts/net_bps)."
        )
        return 0

    outcomes = _read_jsonl(Path(args.outcomes))
    pairs = pit_join(measurements, outcomes)
    print(f"momentum-eval: {len(pairs)} point-in-time pairs (no look-ahead)")
    r = evaluate_feature_direction(pairs, feature_key="momentum_score", min_sample=args.min_sample)
    print(
        f"  momentum_score: direction={r['direction']} "
        f"n_high={r['n_high']} n_low={r['n_low']} "
        f"mean_high={r['mean_high']:.2f} mean_low={r['mean_low']:.2f} "
        f"p_high+={r['p_high_positive']} p_low+={r['p_low_positive']}"
    )
    print(
        "momentum-eval: a learned direction is a HYPOTHESIS — promotion of "
        "direction_aligned / source_trust stays operator- AND edge-gated."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
