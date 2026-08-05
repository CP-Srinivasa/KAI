"""Report the evidence gate for a future SendPaymentV2 cutover."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.lightning.ops_ledger import payment_shadow_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=Path("artifacts/ln_ops_ledger.jsonl"))
    parser.add_argument("--required-samples", type=int, default=20)
    args = parser.parse_args()
    report = payment_shadow_evidence(args.ledger, required_samples=args.required_samples)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["eligible_for_v2_cutover"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
