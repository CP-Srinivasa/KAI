#!/usr/bin/env python3
"""Einladungen für die begleitete Oracle-Beta verwalten (Operator, auf der Pi).

    python scripts/oracle_invite.py neu "<Bezeichnung>" [--tage 30] [--eigen] [--bereiche a,b]
    python scripts/oracle_invite.py liste
    python scripts/oracle_invite.py sperren <inv_id>

``neu`` zeigt den Einladungscode GENAU EINMAL an; gespeichert wird nur sein Hash.
Den Code bekommt der Teilnehmer zusammen mit der Vertragsbestätigung per E-Mail.
Er schickt ihn als Header ``X-KAI-Invite: <code>`` oder als ``?invite=<code>`` mit.
Bezeichnung: Vorname oder Partnerkürzel genügt, keine E-Mail-Adresse.
``--eigen`` markiert eine Einladung für eigene Tests; sie zählt in der Auswertung der
Beta (Prä-Reg oracle_invite_beta_v1) nicht mit. Eine Einladung je Partei.
``--bereiche``: erlaubte Bereiche, kommagetrennt (onchain-facts, fee-series, verdicts,
timestamp). Ohne Angabe die betriebsfertigen; ``timestamp`` nur ausdrücklich, weil dort die
Zusage „Eigenerstattung nach 7 Tagen ohne Nachweis“ gilt. Optionen in beliebiger Reihenfolge.
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
        days = invites.DEFAULT_DAYS
        scopes: list[str] | None = None
        while rest:
            flag = rest.pop(0)
            if flag == "--eigen":
                party = invites.PARTY_OPERATOR
            elif flag == "--tage" and rest and rest[0].isdigit():
                days = int(rest.pop(0))
            elif flag == "--bereiche" and rest:
                scopes = [b.strip() for b in rest.pop(0).split(",") if b.strip()]
            else:
                print(__doc__)
                return 2
        try:
            code, entry = invites.create(args[1], days=days, party=party, scopes=scopes)
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
            f"gültig bis {entry['expires_at'][:16]}Z, Bereiche: {', '.join(entry['scopes'])}"
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
            bereiche = ",".join(e["scopes"]) if isinstance(e.get("scopes"), list) else "alle"
            print(
                f"{e['id']}  {state:<10}  {party:<6}  bis {str(e.get('expires_at', ''))[:16]}Z  "
                f"{bereiche:<40}  {e.get('label', '')}"
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
