#!/usr/bin/env python3
"""Automated truth-ledger anchor run (KAI L3 → research truth, ADR 0012/0013).

One hands-off step that turns "auditable falsification platform" into a standing
cryptographic guarantee:

  1. attest every new pre-registration into the tamper-evident truth ledger;
  2. attest every new attested verdict report ("we tested H and it passed/failed")
     into the SAME hash chain;
  3. ensure the ledger TIP is OTS-anchored on-chain — because the chain is
     forward-linked, anchoring the tip commits the existence + order of EVERY record
     before it, so one proof per run covers the whole history. The tip proof lands in
     the shared ``proofs_dir`` and is upgraded to a Bitcoin proof by the existing
     ``kai-integrity-ots-upgrade`` timer — no separate plumbing.

The anchor step is SELF-HEALING: it gates on "is the current tip already anchored"
(the ``truthledger-<tip16>.ots`` proof file), NOT on "were new records chained this
run". That closes two gaps of a naive new>0 guard: (a) a pre-existing backlog whose
records were attested by an earlier path (manual ``truth-attest-*``) still gets its
tip anchored on the next run; (b) an OTS attempt that fails (calendar outage) is
retried on the next run until the proof lands, instead of being stranded because the
"new" records were already consumed.

Attestation into the LOCAL chain always runs (capital-free, pure append). The
on-chain OTS anchor honours ``APP_INTEGRITY_*`` (default-off; ``stamper=null`` records
without a proof). Never moves capital. Meant for a daily systemd timer.

Exit codes: 0 = ok / disabled / already-anchored, 1 = anchor error.
"""

from __future__ import annotations

import sys
from pathlib import Path


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

    settings = IntegritySettings()
    tip = chain_tip()
    tip_hash = str(tip["record_hash"])
    tip16 = tip_hash[:16]

    if not settings.enabled:
        print(
            f"truth-anchor: chained {new} new; OTS anchoring disabled "
            "(set APP_INTEGRITY_ENABLED=true + stamper=opentimestamps to anchor on-chain)"
        )
        return 0

    # Self-healing idempotency: anchor iff the CURRENT tip has no proof yet. The proof
    # filename mirrors ``anchor_record_digest(prefix="truthledger")`` /
    # ``truth-anchor-status`` exactly (``truthledger-<tip16>.ots``).
    tip_proof = Path(settings.proofs_dir) / f"truthledger-{tip16}.ots"
    if tip_proof.exists():
        print(
            f"truth-anchor: chained {new} new; tip seq={tip['seq']} hash={tip16} "
            "already anchored (idempotent)"
        )
        return 0

    res = anchor_record_digest(tip_hash, settings=settings, prefix="truthledger")
    if res.state == "error":
        print(f"truth-anchor: ERROR anchoring tip seq={tip['seq']} — {res.reason}")
        return 1
    if res.state == "anchored":
        print(
            f"truth-anchor: chained {new}, anchored tip seq={tip['seq']} "
            f"hash={tip16} proof={res.proof_path}"
        )
        return 0
    # stamper=null → recorded without an on-chain proof (non-production config).
    print(
        f"truth-anchor: chained {new}, recorded tip seq={tip['seq']} "
        f"hash={tip16} (no OTS proof - stamper=null)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
