#!/usr/bin/env python
"""Offene Altbefunde ``orphan_settlement`` als bekannte Wallet-Zahlungen schliessen.

D-278 (Operator 2026-09-15): Die Node-Zahlungen ohne Intent vom 2026-09-04 waren
Zahlungen der Alltags-Wallet am selben Node (D-277 (4)). Sie stehen seither als
``attention`` im Journal, ohne dass jemand sie schliessen konnte. Dieses Skript
haengt je Altbefund genau ein ``wallet_settlement`` an (append-only, idempotent)
und gibt aus, was es geschlossen hat.

**Dieses Skript liest keinen Node und sendet nie.** Es schreibt nur ins Journal.

Aufruf auf der Pi (aus dem Checkout, wie ``ln_reconcile.py``):

    .venv/bin/python scripts/ln_close_orphans.py            # zeigt offene Altbefunde
    .venv/bin/python scripts/ln_close_orphans.py --confirm  # schliesst sie

Exit 0 bei Erfolg (auch wenn nichts offen war), 2 wenn ohne ``--confirm``
Altbefunde offen sind.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from app.core.payment_settings import get_payment_settings
from app.payments import reconcile as payment_reconcile
from app.payments.journal import PaymentJournal

DECISION_REF = "D-278"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm", action="store_true", help="Altbefunde wirklich schliessen")
    args = parser.parse_args(argv)

    journal = PaymentJournal(get_payment_settings().resolved_journal_path())
    journal.open()
    open_before = sorted(journal.index.open_orphan_keys())
    if not args.confirm:
        print(json.dumps({"open_orphans": open_before, "closed": [], "dry_run": True}, indent=1))
        return 2 if open_before else 0
    closed = payment_reconcile.close_orphans_as_wallet(
        journal, now=datetime.now(UTC), decision_ref=DECISION_REF
    )
    print(
        json.dumps(
            {
                "open_orphans_before": open_before,
                "closed": list(closed),
                "open_orphans_after": sorted(journal.index.open_orphan_keys()),
                "decision_ref": DECISION_REF,
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
