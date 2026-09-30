"""Strom-Waechter fuer den Health-Check: Audit-Schemata und L2-Ergebnisse.

Ausgelagert aus ``app/alerts/health_check.py`` (God-File-Ratchet); die Funktionen
werden dort namentlich gerufen und in ``run_health_check_report`` ausgewertet — der
Stream-Vertrag G4 verlangt genau diese Aufrufstelle.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.audit.stream_validation import AuditStreamName, load_audit_stream

if TYPE_CHECKING:
    from app.alerts.health_check import HealthIssue


def _issue(severity: str, component: str, message: str) -> HealthIssue:
    from app.alerts.health_check import HealthIssue as _HealthIssue  # Zyklus vermeiden

    return _HealthIssue(severity=severity, component=component, message=message)


def check_audit_stream_schemas(
    adir: Path, files: Sequence[tuple[AuditStreamName, str]], *, tail: int
) -> list[HealthIssue]:
    """Schema-Verletzungen in den letzten ``tail`` Zeilen je Audit-Strom melden."""
    issues: list[HealthIssue] = []
    for stream, filename in files:
        result = load_audit_stream(adir / filename, stream, tail=tail)
        if not result.issues:
            continue
        first = result.issues[0]
        issues.append(
            _issue(
                "warning",
                f"{stream}_schema",
                f"{result.issue_count} invalid row(s) in {filename}; "
                f"first at line {first.line_number}: {first.message.splitlines()[0]}",
            )
        )
    return issues


def check_l2_outcomes(adir: Path, now: datetime) -> list[HealthIssue]:
    """Waechter der L2-Ergebnisse ``l2_outcomes.jsonl`` (Stream-Vertrag G4, Weg 2 vom 30.09.).

    Jede L2-Messung mit Kandidatenkontext braucht nach ihrem 1-h-Horizont ein Ergebnis,
    sonst lernt die L2-Auswertung nichts. Der Rechner laeuft im Shadow-Resolver-Timer
    (alle 30 min). Warnung, sobald faellige Messungen aelter als 3 h kein Ergebnis
    haben — dann steht der Rechner oder die Kursquelle. Kein Eintrag ist normal, wenn
    L2 aus ist oder keine Signale entstehen; deshalb keine Frische-Schwelle.

    Solange die Datei noch nie geschrieben wurde (Anlauf nach dem Release: bis zum
    ersten Timer-Lauf stehen alle Altmessungen als Rueckstau da), schweigt der
    Waechter; den ersten Lauf prueft die Release-Nachkontrolle.
    """
    from app.observability.l2_outcomes import backlog

    outcomes = adir / "l2_outcomes.jsonl"
    if not outcomes.exists():
        return []
    late = backlog(now, shadow_path=adir / "l2_evidence_shadow.jsonl", outcomes_path=outcomes)
    if not late:
        return []
    return [
        _issue(
            "warning",
            "l2_outcomes",
            f"{len(late)} L2-Messung(en) seit ueber 3 h ohne Ergebnis in l2_outcomes.jsonl "
            f"(z. B. {late[0]}) -- Shadow-Resolver oder Kursquelle pruefen",
        )
    ]


__all__ = ["check_audit_stream_schemas", "check_l2_outcomes"]
