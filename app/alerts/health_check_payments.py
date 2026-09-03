"""Payment-Fabric-Waechter fuer den Health-Check (ADR 0018 §5/§8/§10).

Ausgelagert aus ``app/alerts/health_check.py`` (God-File-Schwelle 1800 Zeilen);
die Funktionen werden dort namentlich importiert und in
``run_health_check_report`` aufgerufen — der Stream-Vertrag G4 verlangt genau
diese Aufrufstelle.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.alerts.health_check import HealthIssue


def _issue(severity: str, component: str, message: str) -> HealthIssue:
    from app.alerts.health_check import HealthIssue as _HealthIssue  # Zyklus vermeiden

    return _HealthIssue(severity=severity, component=component, message=message)


def check_payment_journal_chain(adir: Path) -> list[HealthIssue]:
    """Waechter des Geld-Journals (ADR 0018 §5/§10, Stream-Vertrag G4).

    Ueberwacht wird die KETTE, nicht die Kadenz: der Strom ist
    ereignisgetrieben und im Default-Modus SIMULATION legitim tagelang still.
    Eine Freshness-Schwelle waere hier entweder wirkungslos oder ein
    Daueralarm — beides Formen, die eine Zusage vortaeuschen.

    Ein gebrochenes Geld-Journal ist schlimmer als ein fehlendes: es sieht
    weiterhin aus wie ein Beweis. Deshalb ``critical``, nicht ``warning``.
    """
    from app.payments.journal import PAYMENT_JOURNAL_FILENAME, PaymentJournal

    path = adir / "payments" / PAYMENT_JOURNAL_FILENAME
    if not path.is_file():
        return []
    try:
        status = PaymentJournal(path).verify_chain()
    except Exception as exc:  # noqa: BLE001 - eine unlesbare Kette ist der Befund
        return [
            _issue(
                severity="critical",
                component="payment_journal",
                message=f"payment journal unreadable: {type(exc).__name__}: {exc}",
            )
        ]
    if status.ok:
        return []
    return [
        _issue(
            severity="critical",
            component="payment_journal",
            message=f"payment journal chain broken: {status.reason}",
        )
    ]


def check_payment_reconciliation(adir: Path) -> list[HealthIssue]:
    """Waechter des Reconcile-Laufs (ADR 0018 §8/§10).

    Der Reconcile-Timer laeuft als eigener Prozess und hinterlaesst sein
    Ergebnis in ``artifacts/payments/reconcile_state.json``. Ohne diesen
    Waechter waere ein Waisen-Settlement — Geld, das der Node bewegt hat, ohne
    dass ein Intent es beauftragt haette — ein Eintrag in einer Datei, die
    niemand liest. Der ``OnFailure=``-Pfad der Unit genuegt dafuer NICHT: der
    Lauf ist erfolgreich, sein BEFUND ist das Problem.

    ``critical``, nicht ``warning``: jeder der drei Ausloeser
    (Waise, ungeklaerter Send, Uhr-Sprung) ist eine offene Frage ueber Geld.
    """
    from app.payments.reconcile_types import STATE_FILENAME, load_state

    path = adir / "payments" / STATE_FILENAME
    if not path.is_file():
        return []
    state = load_state(path)
    if not state.last_run_utc:
        # Fail-closed: eine vorhandene, aber unlesbare Zustandsdatei ist kein
        # "alles in Ordnung" — sie ist ein Reconciler, dessen Ergebnis fehlt.
        return [
            _issue(
                severity="critical",
                component="payment_reconciliation",
                message=f"payment reconcile state unreadable: {path}",
            )
        ]
    if state.last_status == "ok":
        return []
    return [
        _issue(
            severity="critical",
            component="payment_reconciliation",
            message=(
                f"payment reconciliation needs attention (status={state.last_status}, "
                f"orphan settlements={state.last_orphans}, "
                f"clock_anomaly={state.last_clock_anomaly}, last run {state.last_run_utc})"
            ),
        )
    ]


def check_input_contract_rejection_streams(adir: Path) -> list[HealthIssue]:
    """Validate existing G5 reject streams without inventing a write cadence."""
    from app.audit.input_contract_rejections import inspect_input_rejection_streams

    return [
        _issue(
            severity="warning",
            component="input_contract_rejection_stream",
            message=f"{problem.stream}: {problem.detail}",
        )
        for problem in inspect_input_rejection_streams(
            ln_path=adir / "ln_input_contract_rejections.jsonl",
            analysis_path=adir / "analysis_input_contract_rejections.jsonl",
        )
    ]
