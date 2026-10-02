#!/usr/bin/env python3
"""Einladungen für die begleitete Oracle-Beta verwalten (Operator, auf der Pi).

    python scripts/oracle_invite.py neu "<Bezeichnung>" [--tage 30] [--eigen]
    python scripts/oracle_invite.py liste
    python scripts/oracle_invite.py sperren <inv_id>

``neu`` zeigt den Einladungscode GENAU EINMAL an; gespeichert wird nur sein Hash.
Den Code bekommt der Teilnehmer zusammen mit der Vertragsbestätigung per E-Mail.
Er schickt ihn als Header ``X-KAI-Invite: <code>`` oder als ``?invite=<code>`` mit.
Bezeichnung: Vorname oder Partnerkürzel genügt, keine E-Mail-Adresse.
``--eigen`` markiert eine Einladung für eigene Tests; sie zählt in der Auswertung der
Beta (Prä-Reg oracle_invite_beta_v1) nicht mit. Eine Einladung je Partei.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from app.oracle_legal import invites


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["neu"] and len(args) >= 2:
        rest = args[2:]
        party = invites.PARTY_THIRD
        if "--eigen" in rest:
            rest.remove("--eigen")
            party = invites.PARTY_OPERATOR
        days = invites.DEFAULT_DAYS
        if rest:
            if len(rest) != 2 or rest[0] != "--tage" or not rest[1].isdigit():
                print(__doc__)
                return 2
            days = int(rest[1])
        try:
            code, entry = invites.create(args[1], days=days, party=party)
        except ValueError as exc:
            print(exc)
            return 2
        eigen = (
            " (eigene Einladung, zählt nicht in der Auswertung)"
            if party != invites.PARTY_THIRD
            else ""
        )
        print(
            f"Einladung {entry['id']} für „{entry['label']}“{eigen}, "
            f"gültig bis {entry['expires_at'][:16]}Z"
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
            party = "eigen" if e.get("party") == invites.PARTY_OPERATOR else "dritte"
            print(
                f"{e['id']}  {state:<10}  {party:<6}  bis {str(e.get('expires_at', ''))[:16]}Z  "
                f"{e.get('label', '')}"
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
