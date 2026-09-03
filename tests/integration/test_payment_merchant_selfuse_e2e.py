"""Self-Use-Receivable von Ende zu Ende (ADR 0016 Self-Use-Test, ADR 0017 §1/§8).

KAI stellt eine Forderung fuer eine EIGENE Leistung aus, erkennt ihre
Begleichung und bucht die eigene Bestellung. Kein Dritt-Merchant, kein
Onboarding, kein Preis-Artefakt — genau der Ausschnitt, den ADR 0016 als
Tier-1 freigibt.

Der eigentliche Pruefpunkt ist der **Neustart**. Der Empfangspfad hat keinen
wartenden Aufrufer: eine Invoice wird von aussen beglichen, und der einzige
Beobachter ist der Reconciler in einem anderen Prozess. Ein Zustand, der nur
im Speicher lebt, waere damit nach jedem Deploy weg — und ein zweiter
Reconcile-Lauf wuerde denselben Geldeingang ein zweites Mal buchen.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app.core.payment_settings import PaymentSettings
from app.payments import reconcile
from app.payments.journal import PaymentJournal
from app.payments.models import Money
from app.payments.rail import InvoiceRequest
from app.payments.rails.simulation import SimulationRail
from app.payments.service import PaymentService

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
BOOT = "selfuse-boot"
ORDER_REF = "order-selfuse-1"


def _settings() -> PaymentSettings:
    return PaymentSettings(
        mode="simulation",
        purposes_allowed="self_test",
        destination_allowlist=hashlib.sha256(b"payee:unused").hexdigest(),
    )


def _service(journal: PaymentJournal, rail: SimulationRail) -> PaymentService:
    return PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=_settings(),
        clock=lambda: NOW,
    )


async def _reconcile(journal: PaymentJournal, rail: SimulationRail, tmp_path: Path) -> object:
    return await reconcile.run(
        journal,
        rail,
        settings=_settings(),
        clock=lambda: NOW,
        monotonic=lambda: 1000.0,
        boot_ref=BOOT,
        state_path=tmp_path / "reconcile_state.json",
    )


async def test_self_use_receivable_ueberlebt_einen_neustart(tmp_path: Path) -> None:
    journal_path = tmp_path / "payments" / "payment_journal.jsonl"
    rail = SimulationRail(now=NOW)

    # 1. Forderung ausstellen — mit der eigenen Bestellreferenz.
    journal = PaymentJournal(journal_path)
    journal.open()
    invoice = await _service(journal, rail).create_invoice(
        InvoiceRequest(
            amount=Money(minor_units=2500, currency="SAT", scale=0), purpose="self_test"
        ),
        order_ref=ORDER_REF,
    )
    assert [r.ref_hash for r in journal.index.open_receivables()] == [invoice.ref_hash]

    # 2. Jemand bezahlt sie am Rail. KAI erfaehrt davon NICHT durch einen Aufruf.
    rail.settle(invoice.ref_hash)

    # 3. Der Reconciler bemerkt es — und nennt die Bestellung beim Namen.
    report = await _reconcile(journal, rail, tmp_path)
    assert report.counts["RECEIVABLE_SETTLED"] == 1  # type: ignore[attr-defined]
    settled = [e for e in journal.events() if e.event_type == "receivable_settled"]
    assert len(settled) == 1
    assert settled[0].payload["order_ref"] == ORDER_REF
    assert settled[0].payload["invoice_ref_hash"] == invoice.ref_hash
    assert settled[0].payload["amount_settled_minor_units"] == 2500

    # 4. Die Forderung gilt als beglichen.
    status = await _service(journal, rail).invoice_status(invoice.ref_hash)
    assert status.settled is True
    assert journal.index.open_receivables() == []

    # 5. NEUSTART: ein frischer Dienst auf demselben Journal.
    restarted = PaymentJournal(journal_path)
    restarted.open()
    assert restarted.index.snapshot() == journal.index.snapshot()
    assert restarted.index.open_receivables() == []

    # 6. Ein zweiter Lauf bucht denselben Eingang NICHT noch einmal.
    before = len(restarted.events())
    again = await _reconcile(restarted, rail, tmp_path)
    assert again.counts.get("RECEIVABLE_SETTLED", 0) == 0  # type: ignore[attr-defined]
    assert len(restarted.events()) == before
    assert again.status == "ok"  # type: ignore[attr-defined]


async def test_eine_offene_forderung_wird_nicht_vorschnell_gebucht(tmp_path: Path) -> None:
    """Solange niemand zahlt, gibt es keinen Record — und keine Buchung."""
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    rail = SimulationRail(now=NOW)
    invoice = await _service(journal, rail).create_invoice(
        InvoiceRequest(
            amount=Money(minor_units=1000, currency="SAT", scale=0), purpose="self_test"
        ),
        order_ref=ORDER_REF,
    )

    report = await _reconcile(journal, rail, tmp_path)

    assert report.counts.get("RECEIVABLE_SETTLED", 0) == 0  # type: ignore[attr-defined]
    assert [r.ref_hash for r in journal.index.open_receivables()] == [invoice.ref_hash]
    assert not [e for e in journal.events() if e.event_type == "receivable_settled"]


async def test_der_empfangspfad_bewegt_kein_geld(tmp_path: Path) -> None:
    """Eine Forderung ist ein Eingang. ``pay`` hat hier nichts zu suchen."""
    calls: list[str] = []

    class NeverPays(SimulationRail):
        async def pay(self, intent: object, attempt: object) -> object:  # pragma: no cover
            calls.append("pay")
            raise AssertionError("the receivable path must never send")

    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    rail = NeverPays(now=NOW)
    invoice = await _service(journal, rail).create_invoice(
        InvoiceRequest(
            amount=Money(minor_units=1000, currency="SAT", scale=0), purpose="self_test"
        ),
        order_ref=ORDER_REF,
    )
    rail.settle(invoice.ref_hash)
    await _reconcile(journal, rail, tmp_path)

    assert calls == []
