#!/usr/bin/env python3
"""Evaluate the Momentum-Universe evidence shadow log (KAI G3, direction-learning).

Reads the shadow log (``artifacts/momentum_evidence_shadow.jsonl``, written by the
momentum provider in measure-first mode), point-in-time joins it against realized
outcomes (no look-ahead), and reports whether the ``momentum_score`` percentile has
a LEARNABLE forward-return direction — reusing the autocorrelation-robust
moving-block bootstrap (NOT a naive hit-rate). Read-only; learns nothing into sizing.

Outcomes are FILL-INDEPENDENT by default (since 2026-07-01): the canonical resolved
shadow-candidate pool (:mod:`app.research.shadow_outcomes`) is projected onto the
chosen ``--horizon`` — so the evaluation no longer starves waiting for a real paper
trade to land on a momentum-universe symbol (the design flaw that kept n<30 for
months). ``--outcomes <file>`` still overrides with a hand-produced
``{symbol, entry_ts, net_bps}`` JSONL if you want a bespoke cohort.

Verdict is honest: ``insufficient`` below sample size, ``inconclusive`` when the
bootstrap does not confirm a direction. A learned direction is a HYPOTHESIS —
promotion of ``APP_MOMENTUM_EVIDENCE_DIRECTION_ALIGNED`` (+1/-1) and
``_SOURCE_TRUST`` stays operator- AND edge-gated (never auto-activated).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.observability.l2_evidence_eval import evaluate_feature_direction, pit_join
from app.research.shadow_outcomes import (
    HORIZONS,
    load_canonical_outcomes,
    read_jsonl,
    to_feature_outcomes,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate Momentum-Universe evidence (G3).")
    ap.add_argument("--shadow", default="artifacts/momentum_evidence_shadow.jsonl")
    ap.add_argument(
        "--outcomes",
        default="",
        help="Override outcomes JSONL; default = canonical fill-independent pool.",
    )
    ap.add_argument("--horizon", type=int, default=3600, choices=HORIZONS)
    ap.add_argument("--min-sample", type=int, default=20)
    args = ap.parse_args(argv)

    measurements = read_jsonl(Path(args.shadow))
    print(f"momentum-eval: {len(measurements)} shadow measurements in {args.shadow}")
    if not measurements:
        print(
            "momentum-eval: no measurements yet — enable APP_MOMENTUM_EVIDENCE_ENABLED "
            "and let the loop run."
        )
        return 0

    if args.outcomes:
        outcomes = read_jsonl(Path(args.outcomes))
        src = args.outcomes
    else:
        outcomes = to_feature_outcomes(load_canonical_outcomes(), horizon=args.horizon)
        src = f"canonical shadow pool @ {args.horizon}s"
    print(f"momentum-eval: {len(outcomes)} outcomes from {src} (fill-independent)")

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
