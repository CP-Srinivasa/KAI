#!/usr/bin/env python
"""U4 — print the G0 L402 demand verdict from the demand + earnings ledgers.

Run: ``python scripts/evaluate_l402_demand.py [--window-start YYYY-MM-DD]``
"""

from __future__ import annotations

import argparse
import json

from app.lightning.demand_evaluator import evaluate_l402_demand


def main() -> int:
    ap = argparse.ArgumentParser(description="G0 L402 demand verdict")
    ap.add_argument("--scope", default="fee-series")
    ap.add_argument(
        "--window-start", default=None, help="ISO date (YYYY-MM-DD); payments before are excluded"
    )
    ap.add_argument("--window-days", type=int, default=14)
    args = ap.parse_args()
    out = evaluate_l402_demand(
        scope=args.scope, window_start=args.window_start, window_days=args.window_days
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
