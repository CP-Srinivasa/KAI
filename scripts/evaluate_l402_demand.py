#!/usr/bin/env python
"""U4 — G0 L402 demand verdict + go-live preflight (capital-free, read-only).

Surfaces the pre-registered G0 demand verdict from the demand + earnings ledgers
AND the go-live preflight (config facts), so "where do we stand on G0" is one
command. It NEVER flips a flag and NEVER touches capital: enabling the paid
endpoint + a receive node (``APP_LN_L402_ENABLED`` / ``APP_LN_RECEIVE_ENABLED`` /
a reachable lnd) is Lightning Phase 2+ and remains an OPERATOR/capital decision.
This tool only makes the current state auditable.

Node-side facts are NOT probed here (capital-free): they are left unknown, so the
preflight fails closed to NO-GO — the honest state until the operator provisions
the receive path.

Run: ``python scripts/evaluate_l402_demand.py [--window-start YYYY-MM-DD] [--json]``
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.core.settings import get_settings
from app.lightning.demand_evaluator import evaluate_l402_demand
from app.lightning.golive_preflight import golive_preflight


def _render(demand: dict[str, Any], preflight: dict[str, Any]) -> str:
    th = demand["thresholds"]
    lines = [
        "G0 L402 DEMAND PROBE — capital-free verdict (read-only)",
        f"  scope: {demand['scope']}  window_start: {demand['window_start']}  "
        f"window_days: {demand['window_days']}",
        f"  interest: challenges={demand['challenges']} "
        f"distinct_fps={demand['distinct_challenge_fps']} "
        f"access_granted={demand['access_granted']}",
        f"  settled: payments={demand['settled_payments']} "
        f"distinct_payer_fps={demand['distinct_payer_fps']} "
        f"distinct_days={demand['distinct_days']}",
        f"  bar: >={th['min_payments']} payments, >={th['min_fingerprints']} fps, "
        f">={th['min_days']} days",
        f"  VERDICT: {demand['verdict']}",
    ]
    lines.extend(f"    - {reason}" for reason in demand["reasons"])

    lines.append("")
    lines.append(f"GO-LIVE PREFLIGHT: {preflight['verdict']}")
    for check in preflight["checks"]:
        mark = "PASS" if check["ok"] else "FAIL"
        lines.append(f"  [{mark}] {check['name']}: {check['detail']}")
    if preflight["blocking"]:
        lines.append(f"  blocking: {', '.join(preflight['blocking'])}")

    lines.append("")
    lines.append(
        "NOTE: go-live (serve the paid /oracle endpoint + provision a receive node) "
        "is OPERATOR-gated — Lightning Phase 2+, capital/external. This tool flips "
        "nothing; it only surfaces the current state."
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="G0 L402 demand verdict + go-live preflight (capital-free)"
    )
    ap.add_argument("--scope", default="fee-series")
    ap.add_argument(
        "--window-start", default=None, help="ISO date (YYYY-MM-DD); payments before are excluded"
    )
    ap.add_argument("--window-days", type=int, default=14)
    ap.add_argument("--json", action="store_true", help="Emit combined JSON instead of the render")
    args = ap.parse_args()

    demand = evaluate_l402_demand(
        scope=args.scope, window_start=args.window_start, window_days=args.window_days
    )
    # Config facts from the live settings; node facts left unknown (capital-free) →
    # preflight fails closed to NO-GO.
    preflight = golive_preflight(get_settings().lightning)

    if args.json:
        print(json.dumps({"demand": demand, "golive_preflight": preflight}, indent=2))
    else:
        print(_render(demand, preflight))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
