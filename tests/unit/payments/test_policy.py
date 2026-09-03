"""Die Regelkette (ADR 0017 §6).

Deterministisch, feste Reihenfolge, erste DENY gewinnt. Zwei Eigenschaften
sind wichtiger als jede Einzelregel:

* **Die Reihenfolge ist Teil der Zusage.** Sie entscheidet, welche Begruendung
  der Operator sieht. Ein Betrag ueber dem Tages-Cap, der als
  "unsupported_action" abgelehnt wird, schickt ihn in die falsche Richtung.
* **Ein Fehler IN einer Regel ist ein DENY**, kein Durchlauf. Eine Regelkette,
  die bei einer Exception weiterlaeuft, ist eine Regelkette mit Loch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.payment_settings import PaymentSettings
from app.payments.enums import PaymentMode, PaymentStatus, SettlementFinality, Verdict
from app.payments.models import Money, PaymentIntent
from app.payments.policy import (
    RULE_CHAIN,
    ActorLimits,
    PolicyContext,
    RuleResult,
    evaluate,
)
from app.payments.rail import (
    DecodedDestination,
    DedupGuarantee,
    RailAction,
    RailCapabilities,
    RailHealth,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
PAYEE = "a" * 64


def sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def an_intent(**overrides: object) -> PaymentIntent:
    base: dict[str, object] = {
        "intent_id": "pi_1",
        "idempotency_key": "idem-0123456789abcdef",
        "correlation_id": "corr-1",
        "actor": "operator",
        "purpose": "self_test",
        "rail": "lightning",
        "destination": "lnbc10u1pexample",
        "amount_requested": sat(500),
        "fee_limit": sat(5),
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "mode": PaymentMode.SIMULATION,
    }
    base.update(overrides)
    return PaymentIntent(**base)  # type: ignore[arg-type]


def caps(**overrides: object) -> RailCapabilities:
    base: dict[str, object] = {
        "name": "lightning",
        "settlement_finality": SettlementFinality.INSTANT,
        "dedup_guarantee": DedupGuarantee.BY_PAYMENT_HASH,
        "supported_actions": frozenset({RailAction.PAY_INVOICE, RailAction.CREATE_INVOICE}),
    }
    base.update(overrides)
    return RailCapabilities(**base)  # type: ignore[arg-type]


def health(**overrides: object) -> RailHealth:
    base: dict[str, object] = {
        "rail": "lightning",
        "reachable": True,
        "synced_to_chain": True,
        "synced_to_graph": True,
        "wallet_locked": False,
        "observed_at": NOW,
    }
    base.update(overrides)
    return RailHealth(**base)  # type: ignore[arg-type]


def settings(**overrides: object) -> PaymentSettings:
    base: dict[str, object] = {
        "mode": "simulation",
        "destination_allowlist": PAYEE,
        "purposes_allowed": "self_test,data_subscription",
        "per_payment_max_sat": 1000,
        "daily_hard_cap_sat": 2000,
        "approval_threshold_sat": 900,
    }
    base.update(overrides)
    return PaymentSettings(**base)  # type: ignore[arg-type]


def a_context(**overrides: object) -> PolicyContext:
    """Ein sauberer Kontext.

    Der Decode wird aus dem Intent abgeleitet, wenn der Test ihn nicht selbst
    setzt: eine Testhilfe, die ihn fest verdrahtet, wuerde jeden Test mit
    abweichendem Betrag heimlich in die Betragspruefung der Allowlist-Regel
    laufen lassen — und dort scheitern, statt die gemeinte Regel zu treffen.
    """
    intent = overrides.get("intent") or an_intent()
    assert isinstance(intent, PaymentIntent)
    base: dict[str, object] = {
        "intent": intent,
        "settings": settings(),
        "rail_caps": caps(),
        "rail_health": health(),
        "spent_today_sat": 0,
        "actor_limits": None,
        "decoded_destination": DecodedDestination(
            rail="lightning",
            kind="ln_invoice",
            payee_hash=PAYEE,
            rail_dedup_key="b" * 64,
            amount=intent.amount_requested,
        ),
        "app_env": "development",
        "evaluated_at": NOW,
    }
    base.update(overrides)
    return PolicyContext(**base)  # type: ignore[arg-type]


def agent_limits(**overrides: object) -> ActorLimits:
    base: dict[str, object] = {
        "actor": "agent:research",
        "max_amount_sat": 500,
        "daily_max_sat": 2000,
        "purposes": frozenset({"self_test"}),
        "rails": frozenset({"lightning"}),
        "approval_threshold_sat": 400,
    }
    base.update(overrides)
    return ActorLimits(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Kette als Ganzes
# --------------------------------------------------------------------------- #


def test_chain_order_matches_the_adr() -> None:
    assert [rule.rule_id for rule in RULE_CHAIN] == [
        "mode_and_environment",
        "rail_capability",
        "amount_limits",
        "fee_limit_required",
        "destination_allowlist",
        "actor_limits",
        "purpose_allowed",
        "node_health",
        "liquidity",
        "retry_policy",
        "approval_threshold",
    ]


def test_a_clean_intent_is_allowed() -> None:
    decision = evaluate(a_context())
    assert decision.verdict is Verdict.ALLOW
    assert decision.rule_ids == ()


def test_first_deny_wins_and_names_its_rule() -> None:
    """Ein Intent, der gegen ZWEI Regeln verstoesst, meldet die fruehere."""
    context = a_context(
        intent=an_intent(purpose="gambling", amount_requested=sat(5000)),
        rail_caps=caps(supported_actions=frozenset({RailAction.CREATE_INVOICE})),
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("rail_capability",)


def test_amount_deny_precedes_purpose_deny() -> None:
    context = a_context(intent=an_intent(purpose="gambling", amount_requested=sat(5000)))
    decision = evaluate(context)
    assert decision.rule_ids == ("amount_limits",)


def test_a_rule_that_raises_denies_with_its_rule_id() -> None:
    """Eine Regelkette, die bei einer Exception weiterlaeuft, hat ein Loch."""

    def exploding(context: PolicyContext) -> RuleResult:
        raise RuntimeError("boom")

    exploding.rule_id = "exploding_rule"  # type: ignore[attr-defined]
    decision = evaluate(a_context(), rules=[exploding])  # type: ignore[arg-type]
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("exploding_rule",)
    assert "boom" in " ".join(decision.reasons)


# --------------------------------------------------------------------------- #
# mode_and_environment
# --------------------------------------------------------------------------- #


def test_live_mode_outside_production_is_denied() -> None:
    context = a_context(
        intent=an_intent(mode=PaymentMode.LIVE), settings=settings(mode="live"), app_env="staging"
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("mode_and_environment",)


def test_intent_mode_must_match_the_configured_mode() -> None:
    """Ein Intent, der in einem anderen Modus gebaut wurde, ist veraltet."""
    context = a_context(intent=an_intent(mode=PaymentMode.LIVE), settings=settings(mode="shadow"))
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("mode_and_environment",)


def test_live_mode_in_production_passes_the_first_rule() -> None:
    context = a_context(
        intent=an_intent(mode=PaymentMode.LIVE),
        settings=settings(mode="live"),
        app_env="production",
    )
    assert evaluate(context).verdict is Verdict.ALLOW


# --------------------------------------------------------------------------- #
# rail_capability
# --------------------------------------------------------------------------- #


def test_unsupported_action_is_denied() -> None:
    context = a_context(rail_caps=caps(supported_actions=frozenset({RailAction.CREATE_INVOICE})))
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("rail_capability",)
    assert "unsupported_action" in " ".join(decision.reasons)


def test_a_rail_without_dedup_guarantee_is_denied() -> None:
    """ADR §1: v0.1 sendet nur dort, wo der Rail selbst dedupliziert.

    Ohne diese Garantie ist ein Retry nach einem Timeout ein zweiter Send —
    genau der keysend-Weg aus Red-Team D-01.
    """
    context = a_context(rail_caps=caps(dedup_guarantee=DedupGuarantee.NONE))
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("rail_capability",)


def test_rail_mismatch_between_intent_and_caps_is_denied() -> None:
    context = a_context(intent=an_intent(rail="sepa"))
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("rail_capability",)


# --------------------------------------------------------------------------- #
# amount_limits
# --------------------------------------------------------------------------- #


def test_amount_above_per_payment_max_is_denied() -> None:
    decision = evaluate(a_context(intent=an_intent(amount_requested=sat(1001))))
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("amount_limits",)


def test_amount_at_the_per_payment_max_is_allowed() -> None:
    context = a_context(
        intent=an_intent(amount_requested=sat(1000)), settings=settings(approval_threshold_sat=5000)
    )
    assert evaluate(context).verdict is Verdict.ALLOW


def test_daily_hard_cap_denies_it_does_not_ask_for_confirmation() -> None:
    """ADR §6: harter Tages-Cap = DENY, ausdruecklich kein ``needs_confirm``.

    Der Bestand schob ein ueberschrittenes Budget in eine Bestaetigung; damit
    ist der Cap kein Cap, sondern eine Rueckfrage.
    """
    context = a_context(intent=an_intent(amount_requested=sat(600)), spent_today_sat=1500)
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.verdict is not Verdict.REQUIRES_APPROVAL
    assert decision.rule_ids == ("amount_limits",)


def test_spending_exactly_up_to_the_cap_is_allowed() -> None:
    context = a_context(
        intent=an_intent(amount_requested=sat(500)),
        spent_today_sat=1500,
        settings=settings(approval_threshold_sat=5000),
    )
    assert evaluate(context).verdict is Verdict.ALLOW


# --------------------------------------------------------------------------- #
# fee_limit_required
# --------------------------------------------------------------------------- #


def test_zero_fee_limit_is_denied() -> None:
    """lnd laesst das Limit bei 0 WEG — das ist eine unbegrenzte Gebuehr."""
    decision = evaluate(a_context(intent=an_intent(fee_limit=sat(0))))
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("fee_limit_required",)


def test_fee_limit_above_the_configured_maximum_is_denied() -> None:
    context = a_context(
        intent=an_intent(fee_limit=sat(10_000)), settings=settings(fee_limit_max_sat=200)
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("fee_limit_required",)


# --------------------------------------------------------------------------- #
# destination_allowlist
# --------------------------------------------------------------------------- #


def test_destination_not_on_the_allowlist_is_denied() -> None:
    context = a_context(settings=settings(destination_allowlist="c" * 64))
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("destination_allowlist",)


def test_an_empty_allowlist_denies_everything() -> None:
    context = a_context(settings=settings(destination_allowlist=""))
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("destination_allowlist",)


def test_a_missing_decode_is_denied_never_waved_through() -> None:
    """ADR §6: der Payee kommt aus dem Decode und ist nie ``None``."""
    context = a_context(decoded_destination=None)
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("destination_allowlist",)


def test_decoded_amount_must_match_the_intent() -> None:
    """Sonst zahlt der Intent 500 und die Invoice fordert 50.000."""
    context = a_context(
        decoded_destination=DecodedDestination(
            rail="lightning",
            kind="ln_invoice",
            payee_hash=PAYEE,
            rail_dedup_key="b" * 64,
            amount=sat(50_000),
        )
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("destination_allowlist",)


# --------------------------------------------------------------------------- #
# actor_limits
# --------------------------------------------------------------------------- #


def test_an_agent_without_a_limits_entry_is_denied() -> None:
    """Die Agenten-Tabelle ist eine Allowlist, keine Ausnahmeliste."""
    context = a_context(intent=an_intent(actor="agent:unknown"), actor_limits=None)
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("actor_limits",)


def test_an_agent_above_its_per_payment_limit_is_denied() -> None:
    context = a_context(
        intent=an_intent(actor="agent:research", amount_requested=sat(600)),
        actor_limits=agent_limits(),
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("actor_limits",)


def test_an_agent_above_its_daily_limit_is_denied() -> None:
    context = a_context(
        intent=an_intent(actor="agent:research", amount_requested=sat(400)),
        actor_limits=agent_limits(daily_max_sat=500),
        spent_today_sat=200,
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("actor_limits",)


def test_an_agent_on_a_forbidden_rail_is_denied() -> None:
    context = a_context(
        intent=an_intent(actor="agent:research"),
        actor_limits=agent_limits(rails=frozenset({"sepa"})),
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("actor_limits",)


def test_an_agent_with_a_forbidden_purpose_is_denied() -> None:
    context = a_context(
        intent=an_intent(actor="agent:research"),
        actor_limits=agent_limits(purposes=frozenset({"data_subscription"})),
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("actor_limits",)


def test_an_agent_within_its_limits_may_still_need_approval() -> None:
    context = a_context(
        intent=an_intent(actor="agent:research", amount_requested=sat(450)),
        actor_limits=agent_limits(),
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.REQUIRES_APPROVAL
    assert decision.rule_ids == ("approval_threshold",)


def test_the_operator_needs_no_limits_entry() -> None:
    assert evaluate(a_context(intent=an_intent(actor="operator"))).verdict is Verdict.ALLOW


# --------------------------------------------------------------------------- #
# purpose_allowed / node_health / liquidity
# --------------------------------------------------------------------------- #


def test_unknown_purpose_is_denied() -> None:
    decision = evaluate(a_context(intent=an_intent(purpose="gambling")))
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("purpose_allowed",)


@pytest.mark.parametrize(
    "unhealthy",
    [
        {"reachable": False},
        {"synced_to_chain": False},
        {"synced_to_graph": False},
        {"wallet_locked": True},
    ],
)
def test_an_unhealthy_node_is_denied(unhealthy: dict[str, bool]) -> None:
    decision = evaluate(a_context(rail_health=health(**unhealthy)))
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("node_health",)


def test_a_missing_health_reading_is_denied() -> None:
    decision = evaluate(a_context(rail_health=None))
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("node_health",)


def test_insufficient_liquidity_is_denied() -> None:
    context = a_context(intent=an_intent(amount_requested=sat(900)), available_liquidity_sat=100)
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("liquidity",)


def test_unknown_liquidity_does_not_block() -> None:
    """SIMULATION und SHADOW kennen keine Kanalbilanz — das ist kein Defekt."""
    assert evaluate(a_context(available_liquidity_sat=None)).verdict is Verdict.ALLOW


# --------------------------------------------------------------------------- #
# retry_policy
# --------------------------------------------------------------------------- #


def test_a_retry_without_node_evidence_is_denied() -> None:
    context = a_context(attempt_no=2, previous_status=PaymentStatus.RECONCILIATION_REQUIRED)
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("retry_policy",)


def test_a_retry_out_of_failed_retryable_is_allowed() -> None:
    """Nur aus dem Zustand, den die State Machine mit Node-Evidenz vergibt."""
    context = a_context(attempt_no=2, previous_status=PaymentStatus.FAILED_RETRYABLE)
    assert evaluate(context).verdict is Verdict.ALLOW


def test_a_retry_of_a_settled_intent_is_denied() -> None:
    context = a_context(attempt_no=2, previous_status=PaymentStatus.SETTLED)
    decision = evaluate(context)
    assert decision.verdict is Verdict.DENY
    assert decision.rule_ids == ("retry_policy",)


# --------------------------------------------------------------------------- #
# approval_threshold
# --------------------------------------------------------------------------- #


def test_an_amount_at_or_above_the_threshold_requires_approval() -> None:
    context = a_context(
        intent=an_intent(amount_requested=sat(900)), settings=settings(approval_threshold_sat=900)
    )
    decision = evaluate(context)
    assert decision.verdict is Verdict.REQUIRES_APPROVAL
    assert decision.rule_ids == ("approval_threshold",)


def test_an_amount_below_the_threshold_is_allowed() -> None:
    context = a_context(
        intent=an_intent(amount_requested=sat(899)), settings=settings(approval_threshold_sat=900)
    )
    assert evaluate(context).verdict is Verdict.ALLOW


def test_the_stricter_agent_threshold_wins() -> None:
    context = a_context(
        intent=an_intent(actor="agent:research", amount_requested=sat(450)),
        settings=settings(approval_threshold_sat=900),
        actor_limits=agent_limits(approval_threshold_sat=400),
    )
    assert evaluate(context).verdict is Verdict.REQUIRES_APPROVAL


def test_the_decision_carries_a_timestamp() -> None:
    assert evaluate(a_context()).evaluated_at == NOW
