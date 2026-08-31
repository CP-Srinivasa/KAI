#!/usr/bin/env python3
"""Periodic L3 OTS proof upgrade (KAI L3, default-off).

The daily anchor (``scripts/integrity_anchor_audit.py``) writes PENDING ``.ots``
proofs: a calendar commitment that is not yet Bitcoin-mined. This runner does the
asynchronous second half — it re-queries each pending calendar and upgrades any
proof whose aggregation has since been mined into a real Bitcoin attestation,
rewriting the ``.ots`` in place. Meant for a periodic systemd timer (mining lands
HOURS after submission, so it must run repeatedly, not once).

Default-off: a no-op (exit 0) unless ``APP_INTEGRITY_ENABLED=true`` AND
``APP_INTEGRITY_STAMPER=opentimestamps``. Read-only w.r.t. KAI's audit SSOT, no
capital path.

**Exit codes (G6, KMA-20260827 / A7-017).** 0 = disabled oder Lauf OHNE
Fehlschlag, 1 = harter Fehler (opentimestamps fehlt) ODER mindestens ein Proof
konnte nicht aufgewertet werden. Der zweite Fall ist neu und war der Defekt:
das Skript meldete monatelang ``failed=1`` und endete trotzdem mit 0. Die Unit
blieb gruen, ``OnFailure=kai-unit-failure-notify@`` feuerte nie, und ein
Beweis, der nicht mehr fortgeschrieben werden kann, sah aus wie ein
fortgeschriebener. (Der Audit vermutete ein ``SuccessExitStatus=0 1 2`` in der
Unit — das gibt es dort nicht; der Fehler sass hier, in der letzten Zeile.)

Die Ursache des konkreten Falls war ein Eigentuemer-Bruch: zwei am 02.07. als
``root`` geschriebene Dateien in ``monitor/integrity/`` liessen den als
``ubuntu`` laufenden Dienst am Schreiben scheitern. Nach der Uebereignung am
31.08. lief derselbe Lauf mit ``failed=0`` durch — deshalb kostet diese
Verschaerfung keinen Dauer-Alarm, sondern deckt den naechsten echten Fall auf.
"""

from __future__ import annotations

import sys

from app.core.integrity_settings import IntegritySettings
from app.integrity.anchor import AnchorUnavailableError
from app.integrity.upgrade import upgrade_pending_proofs


def main(cfg: IntegritySettings | None = None) -> int:
    if cfg is None:
        from app.core.settings import get_settings

        cfg = get_settings().integrity

    if not cfg.enabled:
        print("integrity-ots-upgrade: disabled (no-op) — set APP_INTEGRITY_ENABLED=true")
        return 0
    if cfg.stamper != "opentimestamps":
        print(
            f"integrity-ots-upgrade: stamper={cfg.stamper} (no-op) — "
            "needs APP_INTEGRITY_STAMPER=opentimestamps"
        )
        return 0

    try:
        report = upgrade_pending_proofs(cfg.proofs_dir)
    except AnchorUnavailableError as exc:
        print(f"integrity-ots-upgrade: ERROR — {exc}")
        return 1

    print(
        "integrity-ots-upgrade: "
        f"scanned={report.scanned} upgraded={report.upgraded} "
        f"confirmed_already={report.already_confirmed} "
        f"still_pending={report.still_pending} failed={report.failed}"
    )
    if report.failed:
        # Ein nicht fortschreibbarer Beweis ist ein Fehlschlag, kein Detail.
        # Exit 1 laesst die Unit fehlschlagen und damit OnFailure feuern.
        print(
            f"integrity-ots-upgrade: FAILED — {report.failed} Proof(s) nicht "
            "aufgewertet; ein Beweis, der nicht fortgeschrieben werden kann, "
            "darf nicht als Erfolg enden"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
