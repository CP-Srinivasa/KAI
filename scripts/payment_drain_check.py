"""Release-Vorbedingung: kein Geld unterwegs (Lueckenregister 2026-09-26).

``pi_release_deploy.sh`` startet alle release-gebundenen Dienste neu, kai-server
eingeschlossen. Ein Neustart zwischen Freigabe und Node-Antwort ist genau der
Fall, den der Reconciler erst hinterher klaeren muesste (ADR 0018 §8). Diese
Pruefung liest das Journal NUR und sagt, ob ein Intent in einem Zustand steht,
in dem ein Send bevorsteht oder draussen sein kann.

Exit: 0 = nichts unterwegs · 3 = blockiert (Intent unterwegs oder Journal
nicht lesbar — ein unlesbares Journal ist kein "nichts unterwegs").

Aufruf (auf der Pi, aus dem Checkout)::

    .venv/bin/python -m scripts.payment_drain_check \
        --journal artifacts/payments/payment_journal.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.payments.enums import PaymentStatus
from app.payments.journal import PaymentJournal

EXIT_BLOCKED = 3

#: Zustaende, in denen ein Send bevorsteht (AUTHORIZED) oder draussen sein kann.
IN_MOTION: frozenset[str] = frozenset(
    {
        PaymentStatus.AUTHORIZED.value,
        PaymentStatus.SUBMITTED.value,
        PaymentStatus.IN_FLIGHT.value,
        PaymentStatus.RECONCILIATION_REQUIRED.value,
    }
)


def in_motion(journal_path: Path) -> list[tuple[str, str]]:
    """``[(intent_id, status)]`` aller Intents, die gerade unterwegs sind."""
    if not journal_path.is_file():
        return []
    journal = PaymentJournal(journal_path)
    journal.open()
    index = journal.index
    found = []
    for intent_id in index.open_intents():
        status = index.intent_status(intent_id) or ""
        if status in IN_MOTION:
            found.append((intent_id, status))
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        blocked = in_motion(args.journal)
    except Exception as exc:  # noqa: BLE001 - unlesbar heisst blockiert, nie "frei"
        print(f"DRAIN_BLOCKED journal_unreadable={type(exc).__name__}")
        return EXIT_BLOCKED
    if blocked:
        for intent_id, status in blocked:
            print(f"DRAIN_BLOCKED intent={intent_id} status={status}")
        return EXIT_BLOCKED
    print("DRAIN_OK nothing in motion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
