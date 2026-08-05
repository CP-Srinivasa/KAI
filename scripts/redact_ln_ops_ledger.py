#!/usr/bin/env python
"""Build (and optionally attest) a redacted/hash-chained LN ops-ledger migration.

Deliberately non-destructive: SOURCE is never modified or deleted and OUTPUT must
not exist. The operator verifies the printed report BEFORE putting the v2 file into
service under a stopped writer. No LND call and no capital action happens here.

Dry run (mandatory first step, see docs/runbooks/ln_ops_ledger_v2_migration.md):

    python scripts/redact_ln_ops_ledger.py /tmp/ledger_copy.jsonl \
      /tmp/ledger_copy.v2.jsonl --no-attest

Live run (writes one attestation into the truth chain):

    python scripts/redact_ln_ops_ledger.py artifacts/ln_ops_ledger.jsonl \
      artifacts/ln_ops_ledger_v2.jsonl

Exit codes: 0 = migrated + verified, 1 = refused (nothing written or left unverified).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.lightning.ops_ledger import LightningOpsLedgerError, migrate_legacy_ln_ops
from app.truth.ledger import DEFAULT_TRUTH_LEDGER_PATH, append_attestation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="legacy v1 JSONL (stays untouched)")
    parser.add_argument("output", type=Path, help="v2 destination (must not exist)")
    parser.add_argument("--truth-ledger", type=Path, default=DEFAULT_TRUTH_LEDGER_PATH)
    parser.add_argument(
        "--no-attest",
        action="store_true",
        help="skip the Truth-chain attestation (dry run against a COPY)",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = migrate_legacy_ln_ops(args.source, args.output)
    except LightningOpsLedgerError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    if not args.no_attest:
        # A migration that wrote nothing has no destination file (and no hash) — fall
        # back to the source hash so the attestation subject is never degenerate.
        fingerprint = report["destination_sha256"] or report["source_sha256"]
        subject = f"ln-ops-migration:{fingerprint[:16]}"
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
