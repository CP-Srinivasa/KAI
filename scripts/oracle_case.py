#!/usr/bin/env python
"""Oracle-Vorgänge bearbeiten: offene Meldungen/Widerrufe zeigen, Antwort und Abschluss belegen.

Auf der Pi im Checkout (``cd ~/ai_analyst_trading_bot``):

  ./.venv/bin/python scripts/oracle_case.py liste                 offene Vorgänge (mit Kontakt)
  ./.venv/bin/python scripts/oracle_case.py beantwortet <ID> "..."  erste persönliche Antwort belegt
  ./.venv/bin/python scripts/oracle_case.py erledigt <ID> "..."     Lösung/Ablehnung mit Begründung

Append-only: nichts wird überschrieben, jeder Schritt steht mit Zeit und Notiz im
Fall-Log ``artifacts/oracle/oracle_cases.jsonl``. Der Health-Check warnt, solange ein
Vorgang die Serviceziele (2 / 7 Werktage) überschreitet.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import oracle_legal as legal  # noqa: E402


def _list(path: Path) -> int:
    now = datetime.now(UTC)
    open_cases = [c for c in legal.read_cases(path) if not c["resolved_at"]]
    if not open_cases:
        print("Keine offenen Vorgänge.")
        return 0
    for c in open_cases:
        received = datetime.fromisoformat(c["received_at"].replace("Z", "+00:00"))
        age = legal.workdays_between(received, now)
        what = (
            "Widerruf" if c["kind"] == "widerruf" else legal.PROBLEMS.get(c.get("problem", ""), "?")
        )
        answered = "beantwortet" if c["answered_at"] else "OHNE ANTWORT"
        print(f"{c['case_id']}  {what}  {age} Werktage  {answered}  {c.get('email', '')}")
        print(f"    Referenz: {c.get('referenz') or '-'}  Bereich: {c.get('bereich') or '-'}")
        if c.get("beschreibung"):
            print(f"    {c['beschreibung'][:300]}")
    return 0


def main(argv: list[str] | None = None, *, path: Path | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = path or legal.CASES_PATH
    if args[:1] == ["liste"]:
        return _list(target)
    if len(args) == 3 and args[0] in legal.CASE_STATES:
        state, case_id, note = args
        if not note.strip():
            print("Bitte eine Notiz angeben (was wurde geantwortet bzw. entschieden?).")
            return 2
        try:
            legal.mark_case(case_id, state, note, now=datetime.now(UTC), path=target)
        except KeyError:
            print(f"Vorgang {case_id} nicht gefunden.")
            return 1
        print(f"{case_id}: {state} vermerkt.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
