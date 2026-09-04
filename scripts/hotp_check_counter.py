#!/usr/bin/env python
"""Zeige, welchen HOTP-Zaehler der naechste gueltige Code treffen muss.

**Der Befund, der dieses Werkzeug noetig gemacht hat** (LIVE-Fenster
2026-09-04): drei HOTP-Fehlversuche vor der ersten erfolgreichen Freigabe. Die
Ursache war, dass der Eintrag im YubiKey ein anderes Secret trug als der Seed
auf dem Pi — aber ein falscher Code und ein falsches Secret sehen an der API
identisch aus (beides ``approval refused``). Der Operator hatte keinen Weg zu
sehen, welchen Zaehler der Pi ueberhaupt erwartet.

**Was dieses Skript nicht tut.** Es liest den Seed nicht und gibt ihn nie aus.
Die Diagnose eines Geheimnisses ist kein Grund, das Geheimnis auf den
Bildschirm zu holen — und ein Terminal-Paste ist im selben Fenster bereits
einmal teuer geworden. Es bewegt auch nichts: kein Code wird verbraucht, kein
Zaehler fortgeschrieben, keine Zahlung beruehrt.

Aufruf auf dem Pi::

    .venv/bin/python scripts/hotp_check_counter.py

Ausgabe (Beispiel)::

    HOTP journal: /home/kai/secrets/ln_hotp_journal.jsonl
    last_used_counter=-1 next_expected_counter=0

``next_expected_counter=0`` und ein Authenticator-Eintrag mit Zaehler 0 gehoeren
zusammen. Weichen sie ab, ist nicht der Code falsch, sondern die Provisionierung
(siehe ``docs/runbooks/payment_fabric.md`` § HOTP-Provisionierung).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.settings import get_settings
from app.security.hotp_auth import HotpError, HotpVerifier


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zeige die HOTP-Zaehlerposition (liest den Seed nicht)"
    )
    parser.add_argument(
        "--journal-path",
        default=None,
        help="Journalpfad (Default: APP_LN_HOTP_JOURNAL_PATH)",
    )
    args = parser.parse_args()

    configured = args.journal_path or get_settings().lightning.hotp_journal_path
    if not configured:
        print(
            "kein Journalpfad: APP_LN_HOTP_JOURNAL_PATH setzen oder --journal-path angeben",
            file=sys.stderr,
        )
        return 2
    journal_path = Path(configured)

    # Der Seedpfad ist ein Pflichtargument des Verifiers, wird aber auf diesem
    # Weg nie gelesen: ``next_expected_counter`` beruehrt ausschliesslich das
    # Journal. Absichtlich derselbe Verifier wie im Server — ein zweiter Leser
    # mit eigener Meinung ueber das Journalformat waere die schlechtere Antwort.
    verifier = HotpVerifier(seed_path=journal_path, journal_path=journal_path)
    try:
        last = verifier.last_used_counter()
        expected = verifier.next_expected_counter()
    except HotpError as exc:
        print(
            f"HOTP-Zaehler nicht lesbar: {exc}\n"
            "Ist das Journal initialisiert? "
            "scripts/hotp_bootstrap.py --next-counter 0",
            file=sys.stderr,
        )
        return 2

    print(f"HOTP journal: {journal_path}")
    print(f"last_used_counter={last} next_expected_counter={expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
