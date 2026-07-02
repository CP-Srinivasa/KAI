#!/usr/bin/env python3
"""Evaluate V5 funding / open-interest evidence vs realized outcomes (re-runnable).

Encapsulates the previously ad-hoc 2026-06-19 V5 evaluation
(``artifacts/v5_evidence_outcome_eval_20260619.md``) into a deterministic,
repeatable script so each "Tag N/7" re-eval is ONE command instead of a manual
notebook pass. Read-only; ``source_trust``-promotion stays operator- AND
edge-gated downstream — a direction learned here is a HYPOTHESIS, never an
auto-flip.

Since 2026-07-01 the mechanics live in reusable modules so funding/OI, momentum,
L2 and any future pre-registered hypothesis all draw from the SAME fill-independent
outcome pool instead of copy-pasted loaders:
  * outcome loading — :mod:`app.research.shadow_outcomes`
  * aligned-evidence scoring — :mod:`app.research.shadow_evidence_eval`
This script is the thin CLI over those; its numbers are unchanged.

Method (faithful to the 06-19 runbook):
  * Outcomes = resolved shadow candidates
    (``artifacts/shadow_candidate_resolved.jsonl``) with the SIDE-ADJUSTED forward
    returns ``fwd_{60,300,900,3600}s_bps`` (>0 ⇒ the candidate's OWN direction was
    profitable). Entry time = candidate ``ts_utc`` (from
    ``shadow_candidate_ledger.jsonl``; falls back to the ISO timestamp embedded in
    ``tech-<SYM>-<iso>`` candidate ids). Sentinel / no-data rows
    (``|fwd| >= --max-abs-bps``, default 5000 ≈ delisted/garbage) are dropped.
  * Evidence = ``funding_evidence_shadow.jsonl`` / ``oi_evidence_shadow.jsonl``,
    each carrying ``{ts, symbol, direction, evidence_direction_aligned (+1/-1/0)}``.
  * Outcome-centric join: each outcome is paired with the temporally NEAREST
    evidence record of the SAME symbol+side within ``+/- --tol-s`` seconds
    (default 300). Outcomes with no qualifying evidence (or aligned==0) are
    dropped — honest: not every outcome has contemporaneous evidence.
  * Per horizon, split joined outcomes by ``evidence_direction_aligned`` (+1 vs
    -1) → mean fwd_bps, hit-rate, and the spread ``mean(+1) - mean(-1)``. The
    actionable ``aligned+1`` cohort's TIME-ORDERED fwd series is bootstrapped
    (moving-block, autocorrelation-robust) for ``P(mean>0)``. A concentration
    metric flags single-symbol monoculture (the BTC-95%-share trap from 06-19).

Verdict is conservative: a signal is ACTIONABLE only if some horizon has
``mean(+1) >= --cost-bps`` AND ``P(mean(+1)>0) > 0.95`` AND top-symbol share
``<= --max-concentration``. Otherwise it stays ``trust=0.5 SHADOW_ONLY``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.research.shadow_evidence_eval import evaluate_signal, index_evidence, render
from app.research.shadow_outcomes import build_outcomes, load_entry_times, read_jsonl


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate V5 funding/OI evidence vs outcomes.")
    ap.add_argument("--resolved", default="artifacts/shadow_candidate_resolved.jsonl")
    ap.add_argument("--ledger", default="artifacts/shadow_candidate_ledger.jsonl")
    ap.add_argument("--funding", default="artifacts/funding_evidence_shadow.jsonl")
    ap.add_argument("--oi", default="artifacts/oi_evidence_shadow.jsonl")
    ap.add_argument("--tol-s", type=float, default=300.0)
    ap.add_argument("--cost-bps", type=float, default=20.0)
    ap.add_argument("--max-abs-bps", type=float, default=5000.0)
    ap.add_argument("--max-concentration", type=float, default=0.8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    resolved = read_jsonl(Path(args.resolved))
    entry_times = load_entry_times(read_jsonl(Path(args.ledger)))
    outcomes = build_outcomes(resolved, entry_times, max_abs_bps=args.max_abs_bps)

    funding_idx = index_evidence(read_jsonl(Path(args.funding)))
    oi_idx = index_evidence(read_jsonl(Path(args.oi)))

    fr = evaluate_signal(
        outcomes,
        funding_idx,
        tol_s=args.tol_s,
        cost_bps=args.cost_bps,
        max_concentration=args.max_concentration,
    )
    oir = evaluate_signal(
        outcomes,
        oi_idx,
        tol_s=args.tol_s,
        cost_bps=args.cost_bps,
        max_concentration=args.max_concentration,
    )

    if args.json:
        print(
            json.dumps({"funding": fr, "open_interest": oir, "n_outcomes": len(outcomes)}, indent=2)
        )
        return 0

    print(
        f"v5-eval: {len(resolved)} resolved, {len(outcomes)} valid outcomes "
        f"(sentinel/|fwd|>={args.max_abs_bps:.0f}bps dropped); tol={args.tol_s:.0f}s "
        f"cost={args.cost_bps:.0f}bps"
    )
    print()
    print(render("funding", fr))
    print()
    print(render("open_interest", oir))
    print()
    print(
        "v5-eval: a learned direction is a HYPOTHESIS — trust-promotion stays "
        "operator- AND edge-gated (no auto-activation)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
