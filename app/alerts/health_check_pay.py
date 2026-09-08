"""Waechter des KAI-PAY-Stroms (D-CORE-006, Stream-Vertrag G4).

Ausgelagert aus ``app/alerts/health_check.py`` — dieselbe Bauart wie
``health_check_payments.py``: dort steht die Aufrufstelle, hier die Sonde.

**Ueberwacht wird die FORM, nicht die Kadenz.** Der Strom ist
ereignisgetrieben: er waechst, wenn jemand eine Zahlung anfordert, und im
Default-Zustand (``APP_PAY_ENABLED=false``) existiert er ueberhaupt nicht. Eine
Freshness-Schwelle waere hier entweder wirkungslos oder ein Daueralarm — beides
Formen, die eine Zusage vortaeuschen (deshalb ``monitoring:
alternative_watcher`` in ``config/stream_contracts.json``).

**Was ein Befund waere.** Der Strom ist ein CACHE ueber dem Geld-Journal. Er
kann kein Geld verlieren, aber er kann eine Forderung verlieren: eine Zeile,
die sich nicht mehr lesen laesst, nimmt die Verknuepfung ``payment_id ->
ref_hash`` mit. Der Eingang bliebe im Journal belegt — nur wuesste niemand
mehr, WOFUER er kam. Genau das meldet diese Sonde, und mehr behauptet sie
nicht.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from app.pay.store import PAY_REQUESTS_FILENAME

if TYPE_CHECKING:
    from app.alerts.health_check import HealthIssue

#: Ereignisse, die eine ``payment_id`` tragen MUESSEN. Ohne sie ist die Zeile
#: keiner Forderung zuzuordnen und damit wertlos.
_REQUIRED_KEYS = ("ts", "event", "payload")


def _issue(severity: str, component: str, message: str) -> HealthIssue:
    from app.alerts.health_check import HealthIssue as _HealthIssue  # Zyklus vermeiden

    return _HealthIssue(severity=severity, component=component, message=message)


def check_pay_requests(adir: Path) -> list[HealthIssue]:
    """Lesbarkeit und Schema des Pay-Stroms — ohne Kadenz-Annahme.

    ``warning``, nicht ``critical``: eine kaputte Zeile kostet die Zuordnung
    einer Forderung, nie einen Satoshi. Die kritische Zusage traegt
    ``check_payment_journal_chain`` ueber dem Geld-Journal.
    """
    path = adir / "pay" / PAY_REQUESTS_FILENAME
    if not path.is_file():
        # Kein Strom heisst: niemand hat je eine Zahlung angefordert. Das ist
        # der Default-Zustand (Flag aus) und ausdruecklich kein Befund.
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [
            _issue(
                severity="warning",
                component="pay_requests",
                message=f"kai pay request stream unreadable: {type(exc).__name__}: {exc}",
            )
        ]
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        problem = _line_problem(line)
        if problem:
            return [
                _issue(
                    severity="warning",
                    component="pay_requests",
                    message=(
                        f"{path.name} line {number} is malformed ({problem}) — the link "
                        "between payment_id and the money journal's ref_hash is lost for "
                        "that request; the settlement itself stays provable"
                    ),
                )
            ]
    return []


def _line_problem(line: str) -> str:
    """Was an einer Zeile nicht stimmt — leer heisst: nichts."""
    try:
        record = json.loads(line)
    except ValueError as exc:
        return f"{type(exc).__name__}"
    if not isinstance(record, dict):
        return "not an object"
    missing = [key for key in _REQUIRED_KEYS if key not in record]
    if missing:
        return f"missing {', '.join(missing)}"
    payload = record.get("payload")
    if not isinstance(payload, dict) or not str(payload.get("payment_id", "")).strip():
        return "payload without payment_id"
    return ""


__all__ = ["check_pay_requests"]
