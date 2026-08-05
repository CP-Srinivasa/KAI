#!/usr/bin/env python
"""Build and attest a redacted/hash-chained LN ops ledger migration.

This command is deliberately non-destructive: SOURCE stays untouched and OUTPUT
must not exist.  The operator verifies the report before replacing the live file
under a stopped writer.  No LND call or capital action occurs here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.lightning.ops_ledger import migrate_legacy_ln_ops
from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH, append_attestation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--truth-ledger", type=Path, default=DEFAULT_TRUTH_LEDGER_PATH)
    parser.add_argument(
        "--no-attest",
        action="store_true",
        help="skip Truth-chain attestation (tests/dry local inspection only)",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = migrate_legacy_ln_ops(args.source, args.output)
    if not args.no_attest:
        subject = f"ln-ops-migration:{report['destination_sha256'][:16]}"
        attestation = append_attestation(
            "lightning_ops_migration",
            subject,
            report,
            path=args.truth_ledger,
        )
        report["truth_attestation"] = {
            "seq": attestation["seq"],
            "record_hash": attestation["record_hash"],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
