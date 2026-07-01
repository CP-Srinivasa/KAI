#!/usr/bin/env python
"""Resolve a pre-registered hypothesis against measurement — the VERDICT half of
the falsification loop (ADR 0012). Read-only; appends one auditable row.

Canonical edge (default — auto-measured, mirrors ``trading edge-validation``):
    python scripts/prereg_resolve.py --canonical

Manual (any other pre-registered claim — operator states the verdict):
    python scripts/prereg_resolve.py --prereg-id <id> --name <n> \
        --verdict not_met --note "why"
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from app.research.prereg_resolution import (
    DEFAULT_PREREG_VERDICTS_PATH,
    Resolution,
    ResolutionLedger,
    manual_resolution,
    render_resolution,
    resolve_canonical,
)


def _measure_canonical(
    *,
    exec_audit_path: str,
    ledger_path: str,
    min_n: int,
    confidence: float,
    venue: str,
    implausible_threshold: float,
    trials_override: int | None,
) -> object:
    """Compute the canonical-edge EdgeValidationVerdict — the SAME data path the
    ``trading edge-validation`` CLI uses (attributed sources → net_bps → gate)."""
    from app.execution.cost_model import CostModel
    from app.observability.edge_report import (
        compute_trade_edge,
        load_audit_events,
        parse_closed_trades_with_exclusions,
    )
    from app.observability.edge_validation_gate import (
        evaluate_edge_validation,
        resolve_trial_count,
    )
    from app.observability.evidence_window import CANONICAL_EDGE_SOURCES, edge_source_of
    from app.research.ledger import HypothesisLedger

    ledger_count = HypothesisLedger(Path(ledger_path)).tested_count()
    resolved = resolve_trial_count(ledger_count, trials_override)
    events = load_audit_events(exec_audit_path)
    trades, _exclusions = parse_closed_trades_with_exclusions(
        events, implausible_move_threshold=implausible_threshold
    )
    canonical = [t for t in trades if edge_source_of(t) in CANONICAL_EDGE_SOURCES]
    cost_model = CostModel()
    net_bps = [compute_trade_edge(t, cost_model, venue=venue).net_bps for t in canonical]
    return evaluate_edge_validation(
        net_bps, trials=resolved.trials, min_n=min_n, confidence=confidence
    )


def _resolve(args: argparse.Namespace, now: str) -> Resolution | None:
    if args.canonical:
        from app.observability.edge_validation_gate import TrialCountUnavailableError
        from app.research.prereg_ledger import canonical_edge_claim, canonical_edge_prereg_id

        try:
            verdict = _measure_canonical(
                exec_audit_path=args.exec_audit_path,
                ledger_path=args.ledger_path,
                min_n=args.min_n,
                confidence=args.confidence,
                venue=args.venue,
                implausible_threshold=args.implausible_threshold,
                trials_override=args.trials,
            )
        except TrialCountUnavailableError as exc:
            print(f"prereg-resolve refused: {exc}")
            return None
        claim = canonical_edge_claim(min_n=args.min_n, confidence=args.confidence)
        pid = canonical_edge_prereg_id(min_n=args.min_n, confidence=args.confidence)
        return resolve_canonical(
            prereg_id=pid,
            claim=claim,
            verdict=verdict,  # type: ignore[arg-type]
            resolved_at_utc=now,
        )
    return manual_resolution(
        prereg_id=args.prereg_id,
        name=args.name,
        verdict=args.verdict,
        note=args.note,
        resolved_at_utc=now,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Resolve a pre-registered claim (verdict half of the falsification loop)"
    )
    ap.add_argument(
        "--canonical", action="store_true", help="Resolve the canonical edge claim (auto-measured)"
    )
    ap.add_argument("--prereg-id", default=None, help="Manual mode: the pre-registration id")
    ap.add_argument("--name", default="", help="Manual mode: hypothesis name (label)")
    ap.add_argument("--verdict", default=None, help="Manual mode: met | not_met | insufficient_n")
    ap.add_argument("--note", default="", help="Manual mode: justifying note")
    ap.add_argument("--min-n", type=int, default=100, help="Hard sample floor (canonical)")
    ap.add_argument("--confidence", type=float, default=0.95, help="DSR/MinTRL confidence bar")
    ap.add_argument("--venue", default="paper", help="CostModel venue key")
    ap.add_argument(
        "--exec-audit-path",
        default="artifacts/paper_execution_audit.jsonl",
        help="Paper execution audit JSONL",
    )
    ap.add_argument(
        "--ledger-path",
        default="artifacts/research/hypothesis_ledger.jsonl",
        help="Hypothesis ledger (honest trial count for DSR deflation)",
    )
    ap.add_argument(
        "--implausible-threshold",
        type=float,
        default=0.40,
        help="Exclude |exit/entry-1| above this",
    )
    ap.add_argument(
        "--trials", type=int, default=None, help="Trial-count override-floor (canonical)"
    )
    ap.add_argument("--verdicts-path", default=str(DEFAULT_PREREG_VERDICTS_PATH))
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of the render")
    args = ap.parse_args()

    if not args.canonical and not (args.prereg_id and args.verdict):
        ap.error("either --canonical or (--prereg-id and --verdict) required")

    res = _resolve(args, datetime.now(UTC).isoformat())
    if res is None:
        return 2

    ResolutionLedger(Path(args.verdicts_path)).record(res)
    if args.json:
        print(res.to_json())
    else:
        print(render_resolution(res))
        print(f"recorded -> {args.verdicts_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
