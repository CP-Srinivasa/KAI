"""Der deterministische Rail (ADR 0018 §1).

Zwei Teile: die gemeinsame Contract-Suite (:mod:`rail_contract`) und die
Faelle, die NUR dieser Rail erzeugen kann — vor allem der wichtigste von allen:
ein Send, der ohne Antwort endet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.payments.enums import PaymentMode, RailOutcome
from app.payments.models import Money, PaymentAttempt, PaymentIntent
from app.payments.rail import InvoiceRequest, RailError
from app.payments.rails.simulation import SimulationRail

from .rail_contract import RailContractTests

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def an_intent(destination: str) -> PaymentIntent:
    return PaymentIntent(
        intent_id="pi_1",
        idempotency_key="idem-0123456789abcdef",
        correlation_id="corr-1",
        actor="operator",
        purpose="self_test",
        rail="lightning",
        destination=destination,
        amount_requested=sat(1000),
        fee_limit=sat(10),
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        mode=PaymentMode.SIMULATION,
    )


def an_attempt(key: str = "b" * 64) -> PaymentAttempt:
    return PaymentAttempt(attempt_no=1, intent_id="pi_1", rail_dedup_key=key, submitted_at=NOW)


class TestSimulationRailContract(RailContractTests):
    """Die gemeinsame Suite, angewandt auf den Simulationsrail."""

    @pytest.fixture
    def rail(self) -> SimulationRail:
        return SimulationRail(now=NOW)

    @pytest.fixture
    def valid_destination(self) -> str:
        return "sim:settle:alice"

    @pytest.fixture
    def invalid_destination(self) -> str:
        return "   "


# --------------------------------------------------------------------------- #
# Ausgaenge
# --------------------------------------------------------------------------- #


async def test_default_destination_settles() -> None:
    rail = SimulationRail(now=NOW)
    result = await rail.pay(an_intent("anything"), an_attempt())
    assert result.outcome is RailOutcome.SETTLED
    assert result.proof is not None
    assert result.amount_sent == sat(1000)


async def test_fail_prefix_reports_failure_with_evidence() -> None:
    """FAILED ist eine AUSSAGE des Rails — mit Grund, ohne Proof."""
    rail = SimulationRail(now=NOW)
    result = await rail.pay(an_intent("sim:fail:norotue"), an_attempt())
    assert result.outcome is RailOutcome.FAILED
    assert result.failure_reason == "NO_ROUTE"
    assert result.proof is None


async def test_unknown_prefix_carries_no_claim_at_all() -> None:
    """Der 25k-Fall: kein Proof, kein Betrag, kein Grund — nur Ratlosigkeit."""
    rail = SimulationRail(now=NOW)
    result = await rail.pay(an_intent("sim:unknown:timeout"), an_attempt())
    assert result.outcome is RailOutcome.UNKNOWN
    assert result.proof is None
    assert result.amount_sent is None
    assert result.fee_actual is None
    assert result.failure_reason == ""


async def test_inflight_then_settles_on_lookup() -> None:
    rail = SimulationRail(now=NOW)
    key = "c" * 64
    first = await rail.pay(an_intent("sim:inflight:slow"), an_attempt(key))
    assert first.outcome is RailOutcome.IN_FLIGHT
    assert first.proof is None

    lookup = await rail.lookup(key)
    assert lookup.found is True
    assert lookup.outcome is RailOutcome.SETTLED
    assert lookup.proof is not None


async def test_outcomes_are_reproducible() -> None:
    """Zweimal derselbe Aufruf, zweimal dasselbe Ergebnis — inklusive Proof."""
    rail = SimulationRail(now=NOW)
    first = await rail.pay(an_intent("sim:settle:alice"), an_attempt())
    second = await rail.pay(an_intent("sim:settle:alice"), an_attempt())
    assert first == second


async def test_two_destinations_produce_two_payees() -> None:
    rail = SimulationRail(now=NOW)
    alice = await rail.decode("sim:settle:alice")
    bob = await rail.decode("sim:settle:bob")
    assert alice.payee_hash != bob.payee_hash


async def test_quote_names_its_source() -> None:
    rail = SimulationRail(now=NOW)
    quote = await rail.quote(an_intent("sim:settle:alice"))
    assert quote.estimate_source == "simulation"
    assert quote.fee_estimate.minor_units >= 1


# --------------------------------------------------------------------------- #
# Empfangsseite
# --------------------------------------------------------------------------- #


async def test_invoice_settles_only_via_the_test_hook() -> None:
    rail = SimulationRail(now=NOW)
    invoice = await rail.create_invoice(InvoiceRequest(amount=sat(2500), purpose="self_test"))
    assert (await rail.invoice_status(invoice.ref_hash)).settled is False

    rail.settle(invoice.ref_hash)
    status = await rail.invoice_status(invoice.ref_hash)
    assert status.settled is True
    assert status.amount_paid == sat(2500)
    assert status.settled_at == NOW


async def test_settling_an_unknown_invoice_is_refused() -> None:
    rail = SimulationRail(now=NOW)
    with pytest.raises(RailError):
        rail.settle("d" * 64)


async def test_two_invoices_get_two_references() -> None:
    rail = SimulationRail(now=NOW)
    first = await rail.create_invoice(InvoiceRequest(amount=sat(100), purpose="self_test"))
    second = await rail.create_invoice(InvoiceRequest(amount=sat(100), purpose="self_test"))
    assert first.ref_hash != second.ref_hash


async def test_a_simulated_invoice_is_payable_on_this_rail() -> None:
    """Auch simuliert muss die Aufforderung etwas sein, womit man zahlen KANN.

    Ein leerer Platzhalter wuerde den Rueckweg im Simulationsmodus gruen
    aussehen lassen und erst am echten Node auffallen.
    """
    rail = SimulationRail(now=NOW)
    invoice = await rail.create_invoice(InvoiceRequest(amount=sat(100), purpose="self_test"))
    assert invoice.payment_request != ""
    decoded = await rail.decode(invoice.payment_request)
    assert decoded.rail == rail.name
