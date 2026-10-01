#!/usr/bin/env python3
"""Einladungen für die begleitete Oracle-Beta verwalten (Operator, auf der Pi).

    python scripts/oracle_invite.py neu "<Bezeichnung>" [--tage 30]
    python scripts/oracle_invite.py liste
    python scripts/oracle_invite.py sperren <inv_id>

``neu`` zeigt den Einladungscode GENAU EINMAL an; gespeichert wird nur sein Hash.
Den Code bekommt der Teilnehmer zusammen mit der Vertragsbestätigung per E-Mail.
Er schickt ihn als Header ``X-KAI-Invite: <code>`` oder als ``?invite=<code>`` mit.
Bezeichnung: Vorname oder Partnerkürzel genügt, keine E-Mail-Adresse.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from app.oracle_legal import invites


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["neu"] and len(args) in (2, 4):
        days = invites.DEFAULT_DAYS
        if len(args) == 4:
            if args[2] != "--tage" or not args[3].isdigit():
                print(__doc__)
                return 2
            days = int(args[3])
        try:
            code, entry = invites.create(args[1], days=days)
        except ValueError as exc:
            print(exc)
            return 2
        print(
            f"Einladung {entry['id']} für „{entry['label']}“, gültig bis {entry['expires_at'][:16]}Z"
        )
        print(f"Code (nur jetzt sichtbar): {code}")
        print(
            f"Teilnehmer senden ihn als Header {invites.HEADER}: <code> oder ?{invites.QUERY}=<code>"
        )
        return 0
    if args == ["liste"]:
        now = datetime.now(UTC)
        rows = invites.listing()
        if not rows:
            print("Keine Einladungen.")
        for e in rows:
            state = (
                "gesperrt"
                if e.get("revoked_at")
                else ("abgelaufen" if str(e.get("expires_at", "")) <= now.isoformat() else "aktiv")
            )
            print(
                f"{e['id']}  {state:<10}  bis {str(e.get('expires_at', ''))[:16]}Z  {e.get('label', '')}"
            )
        return 0
    if args[:1] == ["sperren"] and len(args) == 2:
        try:
            invites.revoke(args[1])
        except KeyError:
            print(f"Einladung {args[1]} nicht gefunden.")
            return 1
        print(f"{args[1]} gesperrt.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
