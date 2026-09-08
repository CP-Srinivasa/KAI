"""Der eine Durchgang der Empfangsseite (ADR 0018 §8, D-CORE-006).

``settle_receivable`` wurde aus dem Rumpf der Reconciler-Schleife
herausgezogen, damit die Produktschicht denselben Durchgang faehrt statt einen
zweiten zu bauen. Die Extraktion darf am Verhalten des Reconcilers NICHTS
aendern — und der neue Einstieg muss dieselben Zusagen tragen:

* ein Rail-Fehler ist keine Aussage (``settled=None``), nie ein Ablauf;
* eine bereits gebuchte Forderung wird nie ein zweites Mal gebucht;
* ein ``ref_hash``, den KAI nie ausgestellt hat, erreicht den Rail gar nicht.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.payment_settings import PaymentSettings
from app.payments.journal import PaymentJournal
from app.payments.models import Money
from app.payments.rail import InvoiceRequest, RailError
from app.payments.rails.simulation import SimulationRail
from app.payments.receivables import (
    SETTLED_EVENT,
    expiry_of,
    receivable_intent_id,
    settle_receivable,
    settlement_of,
)
from app.payments.reconcile_passes import receivables
from app.payments.service import PaymentService

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def core(tmp_path: Path) -> tuple[PaymentService, PaymentJournal, SimulationRail]:
    rail = SimulationRail(now=NOW)
    journal = PaymentJournal(tmp_path / "payments" / "payment_journal.jsonl")
    journal.open()
    service = PaymentService(
        journal=journal,
        rails={"simulation": rail, "lightning": rail},
        settings=PaymentSettings(mode="simulation", purposes_allowed="self_test"),
        clock=lambda: NOW,
    )
    return service, journal, rail


async def _invoice(service: PaymentService, order_ref: str = "ORDER-1") -> str:
    invoice = await service.create_invoice(
        InvoiceRequest(
            amount=Money(minor_units=1500, currency="SAT", scale=0),
            purpose="self_test",
            expiry_seconds=900,
        ),
        order_ref=order_ref,
    )
    return invoice.ref_hash


def _settled_records(journal: PaymentJournal, ref_hash: str) -> list[object]:
    return [
        event
        for event in journal.events(receivable_intent_id(ref_hash))
        if event.event_type == SETTLED_EVENT
    ]


async def test_eine_unbezahlte_forderung_wird_nicht_gebucht(core: tuple) -> None:  # type: ignore[type-arg]
    service, journal, _rail = core
    ref_hash = await _invoice(service)

    outcome = await settle_receivable(journal, service.rail, ref_hash, now=NOW)

    assert outcome.settled is False
    assert outcome.recorded is False
    assert _settled_records(journal, ref_hash) == []


async def test_eine_bezahlte_forderung_wird_genau_einmal_gebucht(core: tuple) -> None:  # type: ignore[type-arg]
    service, journal, rail = core
    ref_hash = await _invoice(service)
    rail.settle(ref_hash)

    first = await settle_receivable(journal, service.rail, ref_hash, now=NOW)
    second = await settle_receivable(journal, service.rail, ref_hash, now=NOW)

    assert first.recorded is True
    assert second.recorded is False
    assert second.settled is True
    assert len(_settled_records(journal, ref_hash)) == 1
    assert first.settlement is not None and second.settlement is not None
    assert first.settlement.record_hash == second.settlement.record_hash


async def test_der_record_traegt_betrag_und_bestellreferenz(core: tuple) -> None:  # type: ignore[type-arg]
    service, journal, rail = core
    ref_hash = await _invoice(service, order_ref="ORDER-9")
    rail.settle(ref_hash)
    await settle_receivable(journal, service.rail, ref_hash, now=NOW)

    settlement = settlement_of(journal, ref_hash)
    assert settlement is not None
    assert settlement.amount_settled_minor_units == 1500
    assert settlement.order_ref == "ORDER-9"
    assert settlement.rail == "lightning"
    assert settlement.journal_seq > 0
    assert len(settlement.record_hash) == 64


async def test_ein_rail_fehler_ist_keine_aussage(
    core: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[type-arg]
    service, journal, rail = core
    ref_hash = await _invoice(service)

    async def _boom(_ref_hash: str) -> None:
        raise RailError("node unreachable")

    monkeypatch.setattr(rail, "invoice_status", _boom)
    outcome = await settle_receivable(journal, service.rail, ref_hash, now=NOW)

    assert outcome.settled is None
    assert "node unreachable" in outcome.error
    assert _settled_records(journal, ref_hash) == []


async def test_ein_unbekannter_ref_hash_erreicht_den_rail_nicht(
    core: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[type-arg]
    service, journal, rail = core
    asked: list[str] = []

    async def _spy(ref_hash: str) -> None:  # pragma: no cover - darf nie laufen
        asked.append(ref_hash)
        raise AssertionError("the rail must not be asked about a hash KAI never issued")

    monkeypatch.setattr(rail, "invoice_status", _spy)
    outcome = await settle_receivable(journal, service.rail, "f" * 64, now=NOW)

    assert outcome.settled is None
    assert outcome.error == "unknown receivable"
    assert asked == []


async def test_der_reconciler_verhaelt_sich_unveraendert(core: tuple) -> None:  # type: ignore[type-arg]
    """Die Extraktion aendert am Timer nichts: er zaehlt weiterhin jede Buchung."""
    service, journal, rail = core
    paid = await _invoice(service, order_ref="ORDER-A")
    await _invoice(service, order_ref="ORDER-B")
    rail.settle(paid)

    counts: dict[str, int] = {}
    checked = await receivables(journal, service.rail, counts=counts, now=NOW)

    assert checked == 2
    assert counts == {"RECEIVABLE_SETTLED": 1}
    # Ein zweiter Lauf findet nur noch die offene Forderung und bucht nichts.
    counts_again: dict[str, int] = {}
    assert await receivables(journal, service.rail, counts=counts_again, now=NOW) == 1
    assert counts_again == {}


async def test_der_ablauf_kommt_aus_dem_journal(core: tuple) -> None:  # type: ignore[type-arg]
    service, journal, _rail = core
    ref_hash = await _invoice(service)

    assert expiry_of(journal, ref_hash) == NOW.replace(microsecond=0) + __import__(
        "datetime"
    ).timedelta(seconds=900)
    assert expiry_of(journal, "f" * 64) is None


async def test_ohne_buchung_gibt_es_keine_geldwahrheit(core: tuple) -> None:  # type: ignore[type-arg]
    service, journal, _rail = core
    ref_hash = await _invoice(service)
    assert settlement_of(journal, ref_hash) is None
