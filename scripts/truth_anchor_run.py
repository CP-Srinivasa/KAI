#!/usr/bin/env python3
"""Automated truth-ledger anchor run (KAI L3 → research truth, ADR 0012/0013).

One hands-off step that turns "auditable falsification platform" into a standing
cryptographic guarantee:

  1. attest every new pre-registration into the tamper-evident truth ledger;
  2. attest every new attested verdict report ("we tested H and it passed/failed")
     into the SAME hash chain;
  3. if anything new was chained, OTS-anchor the ledger TIP — because the chain is
     forward-linked, anchoring the tip commits the existence + order of EVERY record
     before it, so one proof per run covers the whole history. The tip proof lands in
     the shared ``proofs_dir`` and is upgraded to a Bitcoin proof by the existing
     ``kai-integrity-ots-upgrade`` timer — no separate plumbing.

Attestation into the LOCAL chain always runs (capital-free, pure append). The
on-chain OTS anchor honours ``APP_INTEGRITY_*`` (default-off; ``stamper=null`` records
without a proof). Never moves capital. Meant for a daily systemd timer.

Exit codes: 0 = ok / disabled / nothing-new, 1 = anchor error.
"""

from __future__ import annotations

import sys


def main() -> int:
    from app.core.integrity_settings import IntegritySettings
    from app.integrity.anchor import anchor_record_digest
    from app.truth.ledger import attest_prereg_ledger, attest_verdict_reports, chain_tip

    pre = attest_prereg_ledger()
    ver = attest_verdict_reports()
    new = int(pre["attested"]) + int(ver["attested"])
    print(
        f"truth-anchor: prereg attested={pre['attested']}/{pre['total']} | "
        f"verdict attested={ver['attested']}/{ver['total']}"
    )

    if new == 0:
        print("truth-anchor: nothing new - chain tip unchanged, anchor skipped (idempotent)")
        return 0

    tip = chain_tip()
    res = anchor_record_digest(
        str(tip["record_hash"]), settings=IntegritySettings(), prefix="truthledger"
    )
    if res.state == "disabled":
        print(
            f"truth-anchor: chained {new} new record(s); OTS anchoring disabled "
            "(set APP_INTEGRITY_ENABLED=true + stamper=opentimestamps to anchor on-chain)"
        )
        return 0
    if res.state == "error":
        print(f"truth-anchor: ERROR anchoring tip seq={tip['seq']} — {res.reason}")
        return 1
    if res.state == "anchored":
        print(
            f"truth-anchor: chained {new}, anchored tip seq={tip['seq']} "
            f"hash={str(tip['record_hash'])[:16]} proof={res.proof_path}"
        )
        return 0
    print(
        f"truth-anchor: chained {new}, recorded tip seq={tip['seq']} "
        f"hash={str(tip['record_hash'])[:16]} (no OTS proof - stamper=null)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
