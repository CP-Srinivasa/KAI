"""Payment-Fabric-Waechter fuer den Health-Check (ADR 0018 §5/§8/§10).

Ausgelagert aus ``app/alerts/health_check.py`` (God-File-Schwelle 1800 Zeilen);
die Funktionen werden dort namentlich importiert und in
``run_health_check_report`` aufgerufen — der Stream-Vertrag G4 verlangt genau
diese Aufrufstelle.
"""

from __future__ import annotations

import json
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


def check_payment_intent_vault(adir: Path) -> list[HealthIssue]:
    """Waechter des verschluesselten Sidecars (ADR 0018 §5, Stream-Vertrag G4).

    Der Vault traegt keine Wahrheit ueber Geld — er traegt das Material, mit dem
    ein freigegebener Vorgang nach einem Neustart noch ausfuehrbar ist. Sein
    Ausfall kostet deshalb kein Geld, aber er kostet genau das, was im
    LIVE-Fenster 2026-09-04 gefehlt hat: der Operator muesste jeden Intent
    mitten im scharfen Fenster neu anlegen.

    Zwei Fragen, beide ohne Schluessel beantwortbar:

    1. **Ist die Datei ueberhaupt lesbar?** Eine zerrissene Zeile laesst
       ``recover()`` beim naechsten Start fail-closed abbrechen — das faellt
       sonst erst beim Neustart auf, also genau dann, wenn es stoert.
    2. **Deckt sie die offenen Vorgaenge?** Ein Intent, der auf Freigabe wartet
       und keinen Vault-Eintrag hat, ueberlebt den naechsten Neustart nicht.

    ``warning``, nicht ``critical``: ein fehlender Eintrag verliert
    Bequemlichkeit, keine Wertbewegung. Die Kette des Journals ist die
    kritische Zusage, nicht dieser Strom.
    """
    from app.payments.intent_vault import INTENT_VAULT_FILENAME

    path = adir / "payments" / INTENT_VAULT_FILENAME
    if not path.is_file():
        return []
    sealed, problem = _sealed_intent_ids(path)
    if problem:
        return [_issue(severity="warning", component="payment_intent_vault", message=problem)]

    missing = sorted(_pre_send_intents(adir) - sealed)
    if not missing:
        return []
    return [
        _issue(
            severity="warning",
            component="payment_intent_vault",
            message=(
                f"{len(missing)} pre-send payment intent(s) have no vault entry and would "
                f"not survive a restart of kai-server: {', '.join(missing[:5])}"
            ),
        )
    ]


def _sealed_intent_ids(path: Path) -> tuple[set[str], str]:
    """Die versiegelten Vorgangsschluessel — ohne zu entschluesseln.

    Der Health-Check laeuft ohne ``APP_PAYMENT_VAULT_KEY`` im Zugriff und soll
    ihn auch nicht brauchen: die Form einer Zeile laesst sich pruefen, ohne ihren
    Inhalt zu oeffnen.
    """
    sealed: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return sealed, f"payment intent vault unreadable: {type(exc).__name__}: {exc}"
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            intent_id = str(record["intent_id"])
            if not record["nonce"] or not record["ciphertext"]:
                raise KeyError("empty")
        except (ValueError, KeyError, TypeError) as exc:
            return sealed, (
                f"payment intent vault line {number} is malformed ({type(exc).__name__}) — "
                "recover() will refuse to start the send path until it is repaired"
            )
        sealed.add(intent_id)
    return sealed, ""


def _pre_send_intents(adir: Path) -> set[str]:
    """Offene Vorgaenge VOR dem Send, laut Journal.

    Ein unlesbares Journal wird hier bewusst verschwiegen: dafuer gibt es
    :func:`check_payment_journal_chain`, und zwei Alarme fuer denselben Defekt
    machen aus einem Befund ein Rauschen.
    """
    from app.payments.intent_state import REHYDRATABLE
    from app.payments.journal import PAYMENT_JOURNAL_FILENAME, PaymentJournal

    path = adir / "payments" / PAYMENT_JOURNAL_FILENAME
    if not path.is_file():
        return set()
    journal = PaymentJournal(path)
    try:
        journal.open()
    except Exception:  # noqa: BLE001 - die Kette meldet der andere Waechter
        return set()
    open_states = {status.value for status in REHYDRATABLE}
    return {
        intent_id
        for intent_id in journal.index.open_intents()
        if journal.index.intent_status(intent_id) in open_states
    }


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
