#!/usr/bin/env python3
"""Evaluate the L2 on-chain evidence shadow log (KAI Sprint 2, B-003).

Reads the raw-feature shadow log (``artifacts/l2_evidence_shadow.jsonl``, written
by the L2 provider in measure-first mode), point-in-time joins it against realized
outcomes (no look-ahead), and reports — per feature (fee/mempool percentile) —
whether there is a LEARNABLE direction, using an autocorrelation-robust
moving-block bootstrap (NOT a naive hit-rate). Read-only; learns nothing into sizing.

Outcomes are FILL-INDEPENDENT by default (since 2026-07-01): the canonical resolved
shadow-candidate pool (:mod:`app.research.shadow_outcomes`) is projected onto the
chosen ``--horizon`` — so the join no longer needs a hand-produced outcomes file.
``--outcomes <file>`` still overrides with a bespoke ``{symbol, entry_ts, net_bps}``
JSONL.

Verdict is honest: ``insufficient`` below sample size, ``inconclusive`` when the
bootstrap does not confirm a direction. Trust-promotion stays operator- AND
edge-gated — a learned direction here is a HYPOTHESIS, never an auto-activation.
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
    ap = argparse.ArgumentParser(description="Evaluate L2 on-chain evidence (B-003).")
    ap.add_argument("--shadow", default="artifacts/l2_evidence_shadow.jsonl")
    ap.add_argument(
        "--outcomes",
        default="",
        help="Override outcomes JSONL; default = canonical fill-independent pool.",
    )
    ap.add_argument("--horizon", type=int, default=3600, choices=HORIZONS)
    ap.add_argument("--min-sample", type=int, default=8)
    args = ap.parse_args(argv)

    measurements = read_jsonl(Path(args.shadow))
    print(f"l2-eval: {len(measurements)} shadow measurements in {args.shadow}")
    if not measurements:
        print("l2-eval: no measurements yet — enable APP_L2_EVIDENCE_ENABLED and let it run.")
        return 0

    if args.outcomes:
        outcomes = read_jsonl(Path(args.outcomes))
        src = args.outcomes
    else:
        outcomes = to_feature_outcomes(load_canonical_outcomes(), horizon=args.horizon)
        src = f"canonical shadow pool @ {args.horizon}s"
    print(f"l2-eval: {len(outcomes)} outcomes from {src} (fill-independent)")

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
