"""Reconciliation in beide Richtungen (ADR 0018 §8).

Der Reconciler ist die Stelle, an der aus *unbekannt* wieder *belegt* wird. Er
ist deshalb der einzige Weg zurueck aus ``RECONCILIATION_REQUIRED`` — und er
darf dabei nichts bewegen. Drei Zusagen tragen diese Datei:

1. **Er sendet nie.** Jede Abbildung entsteht aus ``lookup`` /
   ``list_payments`` / ``invoice_status``; ``pay`` wird nicht beruehrt.
2. **Ohne Statuswechsel kein Record.** Ein Timer, der alle 5 Minuten dieselbe
   Zeile anhaengt, macht das Journal unlesbar und die Kette teuer.
3. **Ein Uhr-Sprung setzt Ablaeufe aus, statt sie zu erfinden** (Red-Team
   D-06). Ein Intent, der wegen einer korrigierten Systemzeit ``EXPIRED``
   wird, ist verloren — der Zustand ist terminal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.payment_settings import PaymentSettings
from app.payments import reconcile
from app.payments.enums import PaymentStatus, ProofKind, RailOutcome
from app.payments.journal import PaymentJournal
from app.payments.models import Money, Proof
from app.payments.rail import InvoiceRequest, RailLookup
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentRequest, PaymentService

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
BOOT = "boot-ref-for-tests"


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def allowlisted(destination: str) -> str:
    import hashlib

    return hashlib.sha256(f"payee:{destination}".encode()).hexdigest()


def settings(destination: str, **overrides: object) -> PaymentSettings:
    base: dict[str, object] = {
        "mode": "simulation",
        "destination_allowlist": allowlisted(destination),
        "purposes_allowed": "self_test",
        "per_payment_max_sat": 5000,
        "daily_hard_cap_sat": 10_000,
        "approval_threshold_sat": 4000,
        "fee_limit_max_sat": 200,
    }
    base.update(overrides)
    return PaymentSettings(**base)  # type: ignore[arg-type]


class SpyRail(SimulationRail):
    """Ein Simulationsrail, das jeden Sendeversuch mitzaehlt."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pay_calls = 0
        self.lookup_answers: dict[str, RailLookup] = {}

    async def pay(self, intent: Any, attempt: Any) -> Any:
        self.pay_calls += 1
        return await super().pay(intent, attempt)

    async def lookup(self, rail_dedup_key: str) -> RailLookup:
        answer = self.lookup_answers.get(rail_dedup_key)
        if answer is not None:
            return answer
        return await super().lookup(rail_dedup_key)


def answer(
    key: str,
    outcome: RailOutcome,
    *,
    failure_reason: str = "",
    amount: int = 0,
    fee: int = 0,
    with_proof: bool = False,
) -> RailLookup:
    return RailLookup(
        rail="lightning",
        found=True,
        outcome=outcome,
        rail_dedup_key=key,
        observed_at=NOW,
        amount_sent=sat(amount) if amount else None,
        fee_actual=sat(fee) if fee else None,
        proof=Proof(kind=ProofKind.PREIMAGE, ref_hash="a" * 64) if with_proof else None,
        failure_reason=failure_reason,
    )


def a_service(
    tmp_path: Path, destination: str, **overrides: object
) -> tuple[PaymentJournal, SpyRail, PaymentService]:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    rail = SpyRail(now=NOW)
    service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=settings(destination, **overrides),
        clock=lambda: NOW,
    )
    return journal, rail, service


async def open_intent(
    tmp_path: Path, destination: str = "sim:inflight:alice"
) -> tuple[PaymentJournal, SpyRail, PaymentService, str]:
    """Ein Intent, der gesendet wurde und keine terminale Antwort hat."""
    journal, rail, service = a_service(tmp_path, destination)
    view = await service.create_intent(
        PaymentRequest(
            actor="operator",
            purpose="self_test",
            destination=destination,
            amount=sat(1000),
            fee_limit=sat(10),
        ),
        idempotency_key="idem-key-0000000001",
    )
    await service.execute(view.intent_id)
    return journal, rail, service, view.intent_id


async def awaiting_intent(tmp_path: Path, key: str) -> tuple[PaymentJournal, SpyRail, str]:
    """Ein Intent, der auf Freigabe wartet — der einzige Kandidat fuer EXPIRED."""
    journal, rail, service = a_service(tmp_path, "sim:settle:alice", approval_threshold_sat=1)
    view = await service.create_intent(
        PaymentRequest(
            actor="operator",
            purpose="self_test",
            destination="sim:settle:alice",
            amount=sat(1000),
            fee_limit=sat(10),
            ttl_seconds=60,
        ),
        idempotency_key=key,
    )
    assert view.status is PaymentStatus.AWAITING_APPROVAL
    return journal, rail, view.intent_id


async def run(journal: PaymentJournal, rail: Any, tmp_path: Path, **kwargs: Any) -> Any:
    return await reconcile.run(
        journal,
        rail,
        settings=settings("sim:inflight:alice"),
        clock=kwargs.pop("clock", lambda: NOW),
        monotonic=kwargs.pop("monotonic", lambda: 1000.0),
        boot_ref=kwargs.pop("boot_ref", BOOT),
        state_path=tmp_path / "reconcile_state.json",
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Vorwaerts: jede Abbildung
# --------------------------------------------------------------------------- #


async def test_succeeded_wird_settled_mit_evidenz(tmp_path: Path) -> None:
    journal, rail, _service, intent_id = await open_intent(tmp_path)
    key = journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = answer(key, RailOutcome.SETTLED, amount=1000, fee=3, with_proof=True)

    report = await run(journal, rail, tmp_path)

    assert journal.index.intent_status(intent_id) == PaymentStatus.SETTLED.value
    assert report.counts["SETTLED"] == 1
    settled = [e for e in journal.events(intent_id) if e.event_type == "settled"]
    assert settled[-1].payload["amount_settled_minor_units"] == 1000
    assert settled[-1].payload["fee_actual_minor_units"] == 3
    assert settled[-1].payload["proof_hash"] == "a" * 64


async def test_failed_ohne_bekannten_grund_ist_final_nicht_wiederholbar(tmp_path: Path) -> None:
    """Fail-closed: ein Grund, den dieser Code nicht kennt, gibt keinen Retry frei."""
    journal, rail, _service, intent_id = await open_intent(tmp_path)
    key = journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = answer(key, RailOutcome.FAILED, failure_reason="SOMETHING_NEW")

    report = await run(journal, rail, tmp_path)

    assert journal.index.intent_status(intent_id) == PaymentStatus.FAILED_FINAL.value
    assert report.counts["FAILED_FINAL"] == 1


async def test_failed_mit_route_grund_ist_wiederholbar(tmp_path: Path) -> None:
    """NO_ROUTE heisst: der Node hat nichts bewegt und koennte es spaeter tun.

    Nur aus ``IN_FLIGHT`` heraus — die Zustandstabelle laesst
    ``FAILED_RETRYABLE`` sonst nirgends zu. Der Send war hier gerade erst
    unterwegs; ein Lauf spaeter waere der Intent in der Klaerung, und von dort
    gibt es keinen Retry mehr (siehe naechster Test).
    """
    journal, rail, _service, intent_id = await open_intent(tmp_path)
    assert journal.index.intent_status(intent_id) == PaymentStatus.IN_FLIGHT.value
    key = journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = answer(key, RailOutcome.FAILED, failure_reason="NO_ROUTE")

    report = await run(journal, rail, tmp_path)

    assert journal.index.intent_status(intent_id) == PaymentStatus.FAILED_RETRYABLE.value
    assert report.counts["FAILED_RETRYABLE"] == 1


async def test_aus_der_klaerung_heraus_gibt_es_keinen_retry(tmp_path: Path) -> None:
    """Ein Retry aus ``RECONCILIATION_REQUIRED`` setzte auf ein Unbekanntes."""
    journal, rail, _service, intent_id = await open_intent(tmp_path)
    key = journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = answer(key, RailOutcome.IN_FLIGHT)
    await run(journal, rail, tmp_path)
    assert journal.index.intent_status(intent_id) == PaymentStatus.RECONCILIATION_REQUIRED.value

    rail.lookup_answers[key] = answer(key, RailOutcome.FAILED, failure_reason="NO_ROUTE")
    report = await run(journal, rail, tmp_path)

    assert journal.index.intent_status(intent_id) == PaymentStatus.FAILED_FINAL.value
    assert report.counts["FAILED_FINAL"] == 1


async def test_unknown_bleibt_in_der_klaerung(tmp_path: Path) -> None:
    journal, rail, _service, intent_id = await open_intent(tmp_path, "sim:unknown:alice")
    assert journal.index.intent_status(intent_id) == PaymentStatus.RECONCILIATION_REQUIRED.value

    report = await run(journal, rail, tmp_path)

    assert journal.index.intent_status(intent_id) == PaymentStatus.RECONCILIATION_REQUIRED.value
    assert report.status == "attention"
    # Kein neuer Record — aber der Report zaehlt den ungeklaerten Vorgang.
    assert report.unresolved == 1
    assert report.counts.get("RECONCILIATION_REQUIRED", 0) == 0


async def test_nicht_gefunden_nach_submit_bleibt_klaerungsbeduerftig(tmp_path: Path) -> None:
    """Der Node kennt ihn nicht — das ist kein Beweis, dass nichts floss."""
    journal, rail, _service, intent_id = await open_intent(tmp_path)
    key = journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = RailLookup(
        rail="lightning",
        found=False,
        outcome=RailOutcome.UNKNOWN,
        rail_dedup_key=key,
        observed_at=NOW,
    )

    await run(journal, rail, tmp_path)

    assert journal.index.intent_status(intent_id) == PaymentStatus.RECONCILIATION_REQUIRED.value


async def test_zweimaliger_lauf_schreibt_keinen_zweiten_record(tmp_path: Path) -> None:
    journal, rail, _service, intent_id = await open_intent(tmp_path)
    key = journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = answer(key, RailOutcome.SETTLED, amount=1000, with_proof=True)

    await run(journal, rail, tmp_path)
    after_first = len(journal.events())
    second = await run(journal, rail, tmp_path)

    assert len(journal.events()) == after_first
    assert second.counts.get("SETTLED", 0) == 0


async def test_der_reconciler_sendet_nie(tmp_path: Path) -> None:
    journal, rail, _service, intent_id = await open_intent(tmp_path)
    before = rail.pay_calls
    key = journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = answer(key, RailOutcome.FAILED, failure_reason="NO_ROUTE")

    await run(journal, rail, tmp_path)
    await run(journal, rail, tmp_path)

    assert rail.pay_calls == before, "der Reconciler haengt Outcomes an, er sendet nicht"


# --------------------------------------------------------------------------- #
# Rueckwaerts: Waisen
# --------------------------------------------------------------------------- #


async def test_zahlung_ohne_intent_wird_als_waise_gemeldet(tmp_path: Path) -> None:
    journal, rail, _service, _intent_id = await open_intent(tmp_path)
    rail.inject_payment("f" * 64, amount=sat(500))

    report = await run(journal, rail, tmp_path)

    assert "f" * 64 in report.orphans
    assert report.status == "attention"
    orphans = [e for e in journal.events() if e.event_type == "orphan_settlement"]
    assert len(orphans) == 1
    assert orphans[0].payload["rail_dedup_key"] == "f" * 64


async def test_eine_waise_wird_genau_einmal_gemeldet(tmp_path: Path) -> None:
    journal, rail, _service, _intent_id = await open_intent(tmp_path)
    rail.inject_payment("f" * 64, amount=sat(500))

    await run(journal, rail, tmp_path)
    await run(journal, rail, tmp_path)

    orphans = [e for e in journal.events() if e.event_type == "orphan_settlement"]
    assert len(orphans) == 1


async def test_eine_eigene_zahlung_ist_keine_waise(tmp_path: Path) -> None:
    journal, rail, _service, intent_id = await open_intent(tmp_path, "sim:settle:alice")
    assert journal.index.dedup_key(intent_id) is not None

    report = await run(journal, rail, tmp_path)

    assert report.orphans == ()


# --------------------------------------------------------------------------- #
# Receivables (Self-Use)
# --------------------------------------------------------------------------- #


async def test_beglichene_forderung_erzeugt_receivable_settled_mit_order_ref(
    tmp_path: Path,
) -> None:
    journal, rail, service = a_service(tmp_path, "sim:settle:alice")
    invoice = await service.create_invoice(
        InvoiceRequest(amount=sat(1000), purpose="self_test"), order_ref="order-42"
    )
    assert [r.ref_hash for r in journal.index.open_receivables()] == [invoice.ref_hash]

    unpaid = await run(journal, rail, tmp_path)
    assert unpaid.counts.get("RECEIVABLE_SETTLED", 0) == 0

    rail.settle(invoice.ref_hash)
    report = await run(journal, rail, tmp_path)

    assert report.counts["RECEIVABLE_SETTLED"] == 1
    events = [e for e in journal.events() if e.event_type == "receivable_settled"]
    assert len(events) == 1
    assert events[0].payload["order_ref"] == "order-42"
    assert events[0].payload["invoice_ref_hash"] == invoice.ref_hash
    assert journal.index.open_receivables() == []

    again = await run(journal, rail, tmp_path)
    assert again.counts.get("RECEIVABLE_SETTLED", 0) == 0


# --------------------------------------------------------------------------- #
# Uhr-Sprung (Red-Team D-06)
# --------------------------------------------------------------------------- #


async def test_abgelaufener_intent_wird_expired(tmp_path: Path) -> None:
    journal, rail, intent_id = await awaiting_intent(tmp_path, "idem-key-0000000002")

    later = NOW + timedelta(hours=2)
    # Erster Lauf setzt die Basislinie fuer den Uhr-Vergleich.
    await run(journal, rail, tmp_path)
    report = await run(
        journal, rail, tmp_path, clock=lambda: later, monotonic=lambda: 1000.0 + 7200.0
    )

    assert report.clock_anomaly is False
    assert journal.index.intent_status(intent_id) == PaymentStatus.EXPIRED.value
    assert report.counts["EXPIRED"] == 1


async def test_uhr_sprung_setzt_ablaeufe_aus(tmp_path: Path) -> None:
    """Wall-Clock springt zwei Stunden, die monotone Uhr nicht — kein EXPIRED."""
    journal, rail, intent_id = await awaiting_intent(tmp_path, "idem-key-0000000003")
    await run(journal, rail, tmp_path)

    jumped = NOW + timedelta(hours=2)
    report = await run(
        journal, rail, tmp_path, clock=lambda: jumped, monotonic=lambda: 1000.0 + 30.0
    )

    assert report.clock_anomaly is True
    assert report.status == "attention"
    assert journal.index.intent_status(intent_id) == PaymentStatus.AWAITING_APPROVAL.value
    assert report.counts.get("EXPIRED", 0) == 0
    anomalies = [e for e in journal.events() if e.event_type == "clock_anomaly"]
    assert len(anomalies) == 1


async def test_ohne_vergleichbare_basislinie_kein_ablauf(tmp_path: Path) -> None:
    """Erster Lauf nach einem Neustart: die monotone Uhr hat keinen Bezug."""
    journal, rail, intent_id = await awaiting_intent(tmp_path, "idem-key-0000000004")
    later = NOW + timedelta(hours=2)

    report = await run(journal, rail, tmp_path, clock=lambda: later)

    assert report.expiry_enabled is False
    assert journal.index.intent_status(intent_id) == PaymentStatus.AWAITING_APPROVAL.value


async def test_ein_anderer_boot_ist_keine_vergleichbare_basislinie(tmp_path: Path) -> None:
    journal, rail, intent_id = await awaiting_intent(tmp_path, "idem-key-0000000005")
    await run(journal, rail, tmp_path)
    later = NOW + timedelta(hours=2)

    report = await run(
        journal,
        rail,
        tmp_path,
        clock=lambda: later,
        monotonic=lambda: 12.0,
        boot_ref="a-different-boot",
    )

    assert report.expiry_enabled is False
    assert report.clock_anomaly is False
    assert journal.index.intent_status(intent_id) == PaymentStatus.AWAITING_APPROVAL.value


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


async def test_report_nennt_die_ehrlichkeit_des_fensters(tmp_path: Path) -> None:
    journal, rail, _service, _intent_id = await open_intent(tmp_path)
    report = await run(journal, rail, tmp_path)
    assert report.window_enforced is True
    assert report.complete is True


async def test_ein_journal_ohne_offene_vorgaenge_ist_ok(tmp_path: Path) -> None:
    journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    journal.open()
    rail = SpyRail(now=NOW)
    report = await run(journal, rail, tmp_path)
    assert report.status == "ok"
    assert report.orphans == ()


async def test_der_zustand_ueberlebt_den_prozess(tmp_path: Path) -> None:
    """Der Health-Check laeuft in einem ANDEREN Prozess und liest genau das."""
    journal, rail, _service, _intent_id = await open_intent(tmp_path, "sim:unknown:alice")
    report = await run(journal, rail, tmp_path)

    state_path = tmp_path / "reconcile_state.json"
    assert state_path.is_file()
    persisted = reconcile.load_state(state_path)
    assert persisted.last_status == report.status == "attention"
    assert persisted.last_run_utc == NOW.isoformat()


@pytest.mark.parametrize("event_type", ["receivable_settled", "clock_anomaly"])
def test_neue_ereignisse_sind_im_vokabular(event_type: str) -> None:
    from app.payments.enums import AUDIT_EVENT_TYPES

    assert event_type in AUDIT_EVENT_TYPES


# --------------------------------------------------------------------------- #
# Zwei Prozesse, ein Journal
# --------------------------------------------------------------------------- #


async def test_get_sees_what_another_process_reconciled_without_a_restart(
    tmp_path: Path,
) -> None:
    """Der Server muss sehen, was der Timer geschrieben hat — sofort.

    Befund aus dem LIVE-Fenster 2026-09-04: ``PaymentService.get()`` bevorzugte
    den Prozessspeicher. Der Reconcile-Timer laeuft als EIGENER Prozess; seine
    Aussage ueber Geld — ``SETTLED``, ``FAILED_FINAL`` — erschien im Server also
    erst nach einem Neustart. Bis dahin behauptete ``/payments/intents/{id}``
    einen Zustand, den das Journal laengst widerlegt hatte.
    """
    journal, rail, service, intent_id = await open_intent(tmp_path)
    assert service.get(intent_id).status is PaymentStatus.IN_FLIGHT

    # Der Timer: ein ZWEITER Journal-Griff auf dieselbe Datei, wie in der Unit.
    timer_journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    timer_journal.open()
    key = timer_journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = answer(key, RailOutcome.SETTLED, amount=1000, fee=2)
    report = await run(timer_journal, rail, tmp_path)
    assert report.counts.get("SETTLED") == 1

    assert service.get(intent_id).status is PaymentStatus.SETTLED, (
        "der sendende Prozess haelt am Speicher fest und widerspricht dem Journal"
    )


async def test_a_settled_intent_is_never_sent_again_after_the_timer_settled_it(
    tmp_path: Path,
) -> None:
    """Die gefaehrliche Haelfte desselben Befunds.

    Ein Zustand, den nur ``get()`` korrigiert, waehrend ``execute()`` weiter aus
    dem Speicher liest, waere schlimmer als der Fehler selbst: der Operator
    sieht ``SETTLED`` und der Sendepfad haelt den Intent fuer offen.
    """
    journal, rail, service, intent_id = await open_intent(tmp_path)
    timer_journal = PaymentJournal(tmp_path / "payment_journal.jsonl")
    timer_journal.open()
    key = timer_journal.index.dedup_key(intent_id)
    assert key is not None
    rail.lookup_answers[key] = answer(key, RailOutcome.SETTLED, amount=1000, fee=2)
    await run(timer_journal, rail, tmp_path)

    sends_before = rail.pay_calls
    view = await service.execute(intent_id)
    assert view.replayed is True
    assert view.status is PaymentStatus.SETTLED
    assert rail.pay_calls == sends_before, "kein zweiter Send auf einem erledigten Intent"
