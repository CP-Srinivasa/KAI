"""Domaenenmodell des Payment Control Plane (ADR 0018 §3).

Was hier geprueft wird, ist nicht "Pydantic funktioniert", sondern drei
Zusagen, an denen im Bestand Geld verloren ging:

* **Vier Betraege bleiben getrennt** — ``amount_requested``, ``amount_sent``,
  ``amount_settled``, ``fee_actual``. Ein Modell, das sie zusammenfasst, kann
  eine Teilzahlung nicht von einer vollen unterscheiden.
* **Kein Rohwert ohne Absicht** — ein BOLT11 gehoert in genau ein Feld, und
  dieses Feld traegt seine Redaktionspflicht sichtbar.
* **extra="forbid"** — ein unbekanntes Feld ist im Geldpfad kein Zusatz,
  sondern eine falsche Annahme des Aufrufers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.payments.models import (
    Asset,
    Counterparty,
    ExchangeRateReference,
    Fee,
    Invoice,
    Money,
    PaymentAttempt,
    PaymentAuditEvent,
    PaymentIntent,
    PaymentMode,
    PaymentPolicyDecision,
    PaymentStatus,
    Proof,
    ProofKind,
    Quote,
    Settlement,
    SettlementFinality,
    Verdict,
)

SATS = Asset(symbol="BTC", currency="SAT", scale=0, network="lightning")


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def an_intent(**overrides: object) -> PaymentIntent:
    base: dict[str, object] = {
        "intent_id": "pi_0001",
        "idempotency_key": "idem-0123456789abcdef",
        "correlation_id": "corr-1",
        "actor": "agent:research",
        "purpose": "data_subscription",
        "rail": "lightning",
        "destination": "lnbc10u1pexampledestination",
        "amount_requested": sat(1000),
        "fee_limit": sat(10),
        "created_at": datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        "expires_at": datetime(2026, 9, 3, 13, 0, tzinfo=UTC),
        "mode": PaymentMode.SIMULATION,
    }
    base.update(overrides)
    return PaymentIntent(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #


def test_money_roundtrips_through_json() -> None:
    money = sat(2500)
    assert Money.model_validate_json(money.model_dump_json()) == money


def test_money_rejects_negative_minor_units() -> None:
    with pytest.raises(ValidationError):
        Money(minor_units=-1, currency="SAT", scale=0)


def test_money_allows_zero_because_a_zero_fee_is_real() -> None:
    """Eine Routing-Gebuehr von 0 ueber einen direkten Kanal ist kein Fehler."""
    assert sat(0).minor_units == 0


def test_money_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Money(minor_units=1, currency="SAT", scale=0, note="hi")  # type: ignore[call-arg]


def test_money_is_frozen() -> None:
    money = sat(10)
    with pytest.raises(ValidationError):
        money.minor_units = 11  # type: ignore[misc]


def test_money_normalises_currency_case() -> None:
    assert Money(minor_units=1, currency="sat", scale=0).currency == "SAT"


def test_money_rejects_empty_currency() -> None:
    with pytest.raises(ValidationError):
        Money(minor_units=1, currency="  ", scale=0)


def test_money_rejects_negative_scale() -> None:
    with pytest.raises(ValidationError):
        Money(minor_units=1, currency="EUR", scale=-1)


def test_money_addition_requires_the_same_unit() -> None:
    assert (sat(10) + sat(5)).minor_units == 15
    with pytest.raises(ValueError, match="unit mismatch"):
        _ = sat(10) + Money(minor_units=5, currency="EUR", scale=2)


# --------------------------------------------------------------------------- #
# Asset / Counterparty / Fee / Rate
# --------------------------------------------------------------------------- #


def test_asset_mints_money_in_its_own_unit() -> None:
    assert SATS.money(700) == sat(700)


def test_counterparty_requires_a_reference_hash() -> None:
    party = Counterparty(kind="ln_node", ref_hash="a" * 64, display="peer")
    assert party.ref_hash == "a" * 64
    with pytest.raises(ValidationError):
        Counterparty(kind="ln_node", ref_hash="", display="peer")


def test_counterparty_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        Counterparty(kind="carrier_pigeon", ref_hash="a" * 64)  # type: ignore[arg-type]


def test_fee_keeps_limit_and_actual_apart() -> None:
    fee = Fee(limit=sat(10), actual=sat(3))
    assert fee.limit is not None
    assert fee.limit.minor_units == 10
    assert fee.actual is not None
    assert fee.actual.minor_units == 3
    assert Fee().limit is None


def test_exchange_rate_reference_uses_integer_ppm_not_float() -> None:
    ref = ExchangeRateReference(
        source="coingecko",
        base="BTC",
        quote="EUR",
        rate_ppm=58_000_000_000,
        observed_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    assert isinstance(ref.rate_ppm, int)
    with pytest.raises(ValidationError):
        ExchangeRateReference(
            source="coingecko",
            base="BTC",
            quote="EUR",
            rate_ppm=0,
            observed_at=datetime(2026, 9, 3, tzinfo=UTC),
        )


# --------------------------------------------------------------------------- #
# PaymentIntent
# --------------------------------------------------------------------------- #


def test_intent_roundtrips_through_json() -> None:
    intent = an_intent()
    assert PaymentIntent.model_validate_json(intent.model_dump_json()) == intent


def test_intent_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        an_intent(lightning_payment_hash="ff" * 32)


def test_intent_rejects_a_zero_amount() -> None:
    with pytest.raises(ValidationError, match="amount_requested"):
        an_intent(amount_requested=sat(0))


def test_intent_rejects_a_negative_amount() -> None:
    with pytest.raises(ValidationError):
        an_intent(amount_requested=Money(minor_units=-5, currency="SAT", scale=0))


def test_intent_allows_a_zero_fee_limit_so_policy_can_deny_it() -> None:
    """``fee_limit <= 0`` ist eine POLICY-Ablehnung (§6), keine Formfehler.

    Wuerde das Modell sie verbieten, waere die Policy-Regel unerreichbar und
    ihr Test eine Attrappe.
    """
    assert an_intent(fee_limit=sat(0)).fee_limit.minor_units == 0


def test_intent_requires_matching_currencies_for_amount_and_fee() -> None:
    with pytest.raises(ValidationError, match="unit"):
        an_intent(fee_limit=Money(minor_units=1, currency="EUR", scale=2))


def test_intent_requires_a_timezone_aware_expiry() -> None:
    with pytest.raises(ValidationError):
        an_intent(expires_at=datetime(2026, 9, 3, 13, 0))


def test_intent_expiry_must_follow_creation() -> None:
    with pytest.raises(ValidationError, match="expires_at"):
        an_intent(expires_at=datetime(2026, 9, 3, 11, 0, tzinfo=UTC))


def test_intent_rejects_a_short_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        an_intent(idempotency_key="short")


def test_intent_destination_is_kept_out_of_repr() -> None:
    """Der Rohwert existiert genau einmal — und nie beiläufig im Log."""
    assert "lnbc10u1pexampledestination" not in repr(an_intent())


def test_intent_carries_no_rail_specific_field() -> None:
    """ADR §3: Rail-Spezifisches lebt im Adapter, nicht im Intent."""
    forbidden = {"payment_hash", "preimage", "bolt11", "pubkey", "txid", "payment_request"}
    assert not forbidden & set(PaymentIntent.model_fields)


def test_intent_starts_in_requested() -> None:
    assert an_intent().status is PaymentStatus.REQUESTED


# --------------------------------------------------------------------------- #
# Attempt / Settlement / Proof
# --------------------------------------------------------------------------- #


def test_attempt_and_settlement_keep_four_amounts_apart() -> None:
    attempt = PaymentAttempt(
        attempt_no=1,
        intent_id="pi_0001",
        rail_dedup_key="b" * 64,
        submitted_at=datetime(2026, 9, 3, 12, 1, tzinfo=UTC),
        amount_sent=sat(1000),
    )
    settlement = Settlement(
        intent_id="pi_0001",
        attempt_no=1,
        amount_settled=sat(990),
        fee_actual=sat(2),
        proof=Proof(kind=ProofKind.PREIMAGE, ref_hash="c" * 64),
        finality=SettlementFinality.INSTANT,
        settled_at=datetime(2026, 9, 3, 12, 2, tzinfo=UTC),
    )
    intent = an_intent()
    assert intent.amount_requested.minor_units == 1000
    assert attempt.amount_sent is not None
    assert attempt.amount_sent.minor_units == 1000
    assert settlement.amount_settled.minor_units == 990
    assert settlement.fee_actual.minor_units == 2


def test_attempt_numbering_starts_at_one() -> None:
    with pytest.raises(ValidationError):
        PaymentAttempt(
            attempt_no=0,
            intent_id="pi_0001",
            rail_dedup_key="b" * 64,
            submitted_at=datetime(2026, 9, 3, 12, 1, tzinfo=UTC),
        )


def test_proof_never_carries_a_preimage_in_the_clear() -> None:
    """Ein 64-hex-Wert im ``ref_hash`` ist ein Hash — der Feldname ist Vertrag."""
    proof = Proof(kind=ProofKind.PREIMAGE, ref_hash="d" * 64)
    assert set(Proof.model_fields) == {"kind", "ref_hash"}
    assert proof.model_dump()["ref_hash"] == "d" * 64
    with pytest.raises(ValidationError):
        Proof(kind=ProofKind.PREIMAGE, ref_hash="", preimage="e" * 64)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Quote / Invoice / Decision / AuditEvent
# --------------------------------------------------------------------------- #


def test_quote_roundtrips_and_forbids_extras() -> None:
    quote = Quote(
        rail="lightning",
        amount=sat(1000),
        fee_estimate=sat(4),
        route_hint_hash="f" * 64,
        valid_until=datetime(2026, 9, 3, 12, 5, tzinfo=UTC),
        estimate_source="settings_ppm",
    )
    assert Quote.model_validate_json(quote.model_dump_json()) == quote
    with pytest.raises(ValidationError):
        Quote(
            rail="lightning",
            amount=sat(1000),
            fee_estimate=sat(4),
            valid_until=datetime(2026, 9, 3, 12, 5, tzinfo=UTC),
            estimate_source="settings_ppm",
            hops=3,  # type: ignore[call-arg]
        )


def test_invoice_carries_hashes_not_the_encoded_request() -> None:
    invoice = Invoice(
        rail="lightning",
        ref_hash="1" * 64,
        amount=sat(1000),
        payee_hash="2" * 64,
        expires_at=datetime(2026, 9, 3, 13, 0, tzinfo=UTC),
        memo_hash="3" * 64,
    )
    assert "payment_request" not in Invoice.model_fields
    assert Invoice.model_validate_json(invoice.model_dump_json()) == invoice


def test_policy_decision_requires_a_rule_id_for_a_deny() -> None:
    allowed = PaymentPolicyDecision(
        verdict=Verdict.ALLOW,
        evaluated_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )
    assert allowed.rule_ids == ()
    with pytest.raises(ValidationError, match="rule_ids"):
        PaymentPolicyDecision(
            verdict=Verdict.DENY,
            reasons=("over cap",),
            evaluated_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        )


def test_audit_event_roundtrips_and_keeps_chain_fields() -> None:
    event = PaymentAuditEvent(
        seq=1,
        ts=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        intent_id="pi_0001",
        event_type="intent_created",
        payload={"amount_minor_units": 1000},
        prev_hash="0" * 64,
        record_hash="9" * 64,
    )
    assert PaymentAuditEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError):
        PaymentAuditEvent(
            seq=0,
            ts=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
            intent_id="pi_0001",
            event_type="intent_created",
            payload={},
            prev_hash="0" * 64,
            record_hash="9" * 64,
        )


def test_audit_event_rejects_an_unknown_event_type() -> None:
    with pytest.raises(ValidationError):
        PaymentAuditEvent(
            seq=1,
            ts=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
            intent_id="pi_0001",
            event_type="wire_transfer_authorised",
            payload={},
            prev_hash="0" * 64,
            record_hash="9" * 64,
        )


def test_every_adr_event_type_is_declared() -> None:
    """ADR §9 nennt die Ereignisse abschliessend — die Liste ist der Vertrag."""
    from app.payments.models import AUDIT_EVENT_TYPES

    assert AUDIT_EVENT_TYPES >= {
        "intent_created",
        "policy_decided",
        "approval_granted",
        "approval_denied",
        "submitted",
        "rail_requested",
        "rail_responded",
        "settled",
        "settlement_reversible",
        "reversed",
        "failed",
        "retry_scheduled",
        "reconciled",
        "orphan_settlement",
        "expired",
        "cancelled",
        "final",
    }


def test_mode_and_verdict_enums_match_the_adr() -> None:
    assert {m.value for m in PaymentMode} == {"simulation", "shadow", "live"}
    assert {v.value for v in Verdict} == {
        "ALLOW",
        "DENY",
        "REQUIRES_APPROVAL",
        "RETRY_ALLOWED",
        "RETRY_DENIED",
    }
