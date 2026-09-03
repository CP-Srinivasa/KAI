"""Die Regelkette (ADR 0017 §6) — fail-closed und deterministisch.

Elf Regeln in fester Reihenfolge, jede eine Funktion ``(ctx) -> RuleResult``,
die erste DENY gewinnt. Das Ergebnis nennt immer die Regel, die es getragen
hat — der Bestand gab einen Freitext-``reason`` direkt in einen HTTP-403-Body
(``ln_control.py:275``), und damit war die Begruendung weder auswertbar noch
stabil.

**Warum die Reihenfolge Teil der Zusage ist.** Sie entscheidet, welche
Begruendung der Operator sieht. Ein Betrag ueber dem Tages-Cap, der als
``unsupported_action`` abgelehnt wird, schickt ihn in die falsche Richtung —
er wuerde die Rail-Konfiguration pruefen statt das Budget.

**Ein Fehler IN einer Regel ist ein DENY.** Nicht "Regel uebersprungen": eine
Kette, die bei einer Exception weiterlaeuft, hat genau dort ein Loch, wo etwas
Unerwartetes passiert ist.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.payments.enums import PaymentStatus, Verdict
from app.payments.models import PaymentPolicyDecision
from app.payments.policy_context import ActorLimits, PolicyContext, Rule, RuleResult, rule_id
from app.payments.rail import DedupGuarantee, RailAction

logger = logging.getLogger(__name__)


def _is_agent(actor: str) -> bool:
    return actor.startswith("agent:")


# --------------------------------------------------------------------------- #
# Die elf Regeln, in der Reihenfolge des ADR
# --------------------------------------------------------------------------- #


@rule_id("mode_and_environment")
def mode_and_environment(ctx: PolicyContext) -> RuleResult:
    """Modus und Umgebung sind nie implizit (ADR §1)."""
    if ctx.intent.mode.value != ctx.settings.mode:
        return RuleResult.deny(
            f"mode_mismatch: intent was built in {ctx.intent.mode.value!r}, "
            f"the process runs in {ctx.settings.mode!r}"
        )
    if ctx.settings.mode == "live" and ctx.app_env != "production":
        return RuleResult.deny(f"live_outside_production: APP_ENV={ctx.app_env!r}")
    return RuleResult.allow()


@rule_id("rail_capability")
def rail_capability(ctx: PolicyContext) -> RuleResult:
    """Der Rail muss die Aktion koennen — und sie deduplizieren koennen."""
    caps = ctx.rail_caps
    if caps is None:
        return RuleResult.deny("unsupported_action: no capabilities reported for this rail")
    if caps.name != ctx.intent.rail:
        return RuleResult.deny(
            f"unsupported_action: intent targets rail {ctx.intent.rail!r}, "
            f"capabilities describe {caps.name!r}"
        )
    if not caps.supports(ctx.action):
        return RuleResult.deny(f"unsupported_action: {ctx.action.value} on rail {caps.name}")
    if ctx.action is RailAction.PAY_INVOICE and caps.dedup_guarantee is DedupGuarantee.NONE:
        # Ohne Rail-Dedup ist ein Retry nach einem Timeout ein zweiter Send.
        return RuleResult.deny(
            f"unsupported_action: rail {caps.name} offers no dedup guarantee; "
            "a retry after a timeout could not be distinguished from a second payment"
        )
    return RuleResult.allow()


@rule_id("amount_limits")
def amount_limits(ctx: PolicyContext) -> RuleResult:
    """Pro Zahlung und pro Tag. Der Tages-Cap ist ein DENY, keine Rueckfrage."""
    amount = ctx.intent.amount_requested.minor_units
    if amount > ctx.settings.per_payment_max_sat:
        return RuleResult.deny(
            f"per_payment_max exceeded: {amount} > {ctx.settings.per_payment_max_sat}"
        )
    if ctx.spent_today_sat + amount > ctx.settings.daily_hard_cap_sat:
        return RuleResult.deny(
            f"daily_hard_cap exceeded: {ctx.spent_today_sat} + {amount} > "
            f"{ctx.settings.daily_hard_cap_sat} (hard cap — deliberately not a confirmation)"
        )
    return RuleResult.allow()


@rule_id("fee_limit_required")
def fee_limit_required(ctx: PolicyContext) -> RuleResult:
    """Ein Fee-Limit <= 0 ist eine UNBEGRENZTE Gebuehr.

    ``client.py:431`` sendet ``fee_limit`` nur, wenn es > 0 ist — bei 0 laesst
    lnd das Feld weg und routet ohne Obergrenze.
    """
    fee_limit = ctx.intent.fee_limit.minor_units
    if fee_limit <= 0:
        return RuleResult.deny(
            "fee_limit_required: a limit of 0 makes lnd omit the field entirely, "
            "which is an unbounded routing fee"
        )
    if fee_limit > ctx.settings.fee_limit_max_sat:
        return RuleResult.deny(
            f"fee_limit above configured maximum: {fee_limit} > {ctx.settings.fee_limit_max_sat}"
        )
    return RuleResult.allow()


@rule_id("destination_allowlist")
def destination_allowlist(ctx: PolicyContext) -> RuleResult:
    """Der Empfaenger kommt aus dem Decode — und ist nie ``None``."""
    decoded = ctx.decoded_destination
    if decoded is None:
        return RuleResult.deny(
            "destination not decoded: an allowlist check against an unknown payee is not a check"
        )
    if decoded.payee_hash not in ctx.settings.destination_allowlist_hashes:
        return RuleResult.deny(f"payee not allowlisted: {decoded.payee_hash[:12]}…")
    if (
        decoded.amount is not None
        and decoded.amount.minor_units != ctx.intent.amount_requested.minor_units
    ):
        return RuleResult.deny(
            f"amount mismatch: intent says {ctx.intent.amount_requested.minor_units}, "
            f"destination demands {decoded.amount.minor_units}"
        )
    return RuleResult.allow()


@rule_id("actor_limits")
def actor_limits(ctx: PolicyContext) -> RuleResult:
    """Agenten-Tabelle. Fehlt der Eintrag, darf der Agent nichts."""
    if not _is_agent(ctx.intent.actor):
        return RuleResult.allow()
    limits = ctx.actor_limits
    if limits is None:
        return RuleResult.deny(f"no agent limits configured for {ctx.intent.actor}")
    amount = ctx.intent.amount_requested.minor_units
    if amount > limits.max_amount_sat:
        return RuleResult.deny(
            f"agent per-payment limit exceeded: {amount} > {limits.max_amount_sat}"
        )
    if ctx.spent_today_sat + amount > limits.daily_max_sat:
        return RuleResult.deny(
            f"agent daily limit exceeded: {ctx.spent_today_sat} + {amount} > {limits.daily_max_sat}"
        )
    if ctx.intent.rail not in limits.rails:
        return RuleResult.deny(f"agent may not use rail {ctx.intent.rail}")
    if ctx.intent.purpose not in limits.purposes:
        return RuleResult.deny(f"agent may not spend for purpose {ctx.intent.purpose}")
    return RuleResult.allow()


@rule_id("purpose_allowed")
def purpose_allowed(ctx: PolicyContext) -> RuleResult:
    if ctx.intent.purpose not in ctx.settings.purposes_allowed_set:
        return RuleResult.deny(f"purpose not allowed: {ctx.intent.purpose}")
    return RuleResult.allow()


@rule_id("node_health")
def node_health(ctx: PolicyContext) -> RuleResult:
    """Unsynchron, gesperrt oder offline = DENY (SENTR P0).

    Ein nicht synchroner Node bewertet Routen auf veralteten Daten; ein
    gesperrtes Wallet kann nicht signieren. Beides sieht von aussen aus wie
    "der Node antwortet".
    """
    health = ctx.rail_health
    if health is None:
        return RuleResult.deny("no node health reading — refusing to spend blind")
    if not health.healthy:
        return RuleResult.deny(
            f"node unhealthy (reachable={health.reachable}, chain={health.synced_to_chain}, "
            f"graph={health.synced_to_graph}, locked={health.wallet_locked})"
        )
    return RuleResult.allow()


@rule_id("liquidity")
def liquidity(ctx: PolicyContext) -> RuleResult:
    """Nur pruefbar, wenn der Rail eine Zahl liefert.

    ``None`` blockiert NICHT: in SIMULATION und SHADOW gibt es keine
    Kanalbilanz, und eine erfundene Null waere ein Dauer-DENY ohne Aussage.
    """
    available = ctx.available_liquidity_sat
    if available is None:
        return RuleResult.allow()
    needed = ctx.intent.amount_requested.minor_units + ctx.intent.fee_limit.minor_units
    if available < needed:
        return RuleResult.deny(f"insufficient liquidity: {available} < {needed}")
    return RuleResult.allow()


@rule_id("retry_policy")
def retry_policy(ctx: PolicyContext) -> RuleResult:
    """Ein Retry braucht den Beweis, dass nichts bewegt wurde (ADR §4).

    ``FAILED_RETRYABLE`` ist der einzige Zustand, den die State Machine nur
    mit Node-Evidenz vergibt. Aus ``RECONCILIATION_REQUIRED`` heraus zu
    wiederholen hiesse, auf ein Unbekanntes zu setzen.
    """
    if ctx.attempt_no <= 1:
        return RuleResult.allow()
    if ctx.previous_status is PaymentStatus.FAILED_RETRYABLE:
        return RuleResult.allow()
    previous = ctx.previous_status.value if ctx.previous_status else "unknown"
    return RuleResult.deny(
        f"retry refused from status {previous}: only a rail-proven FAILED_RETRYABLE may be retried"
    )


@rule_id("approval_threshold")
def approval_threshold(ctx: PolicyContext) -> RuleResult:
    """Ab der Schwelle entscheidet ein Mensch per HOTP. Die STRENGERE gilt."""
    thresholds = [ctx.settings.approval_threshold_sat]
    if ctx.actor_limits is not None and ctx.actor_limits.approval_threshold_sat is not None:
        thresholds.append(ctx.actor_limits.approval_threshold_sat)
    threshold = min(thresholds)
    if ctx.intent.amount_requested.minor_units >= threshold:
        return RuleResult.approval(
            f"amount {ctx.intent.amount_requested.minor_units} >= approval threshold {threshold}"
        )
    return RuleResult.allow()


RULE_CHAIN: tuple[Rule, ...] = (
    mode_and_environment,
    rail_capability,
    amount_limits,
    fee_limit_required,
    destination_allowlist,
    actor_limits,
    purpose_allowed,
    node_health,
    liquidity,
    retry_policy,
    approval_threshold,
)


def evaluate(
    ctx: PolicyContext,
    *,
    rules: Sequence[Rule] | None = None,
) -> PaymentPolicyDecision:
    """Laufe die Kette. Erste DENY gewinnt, sonst erste REQUIRES_APPROVAL.

    ``REQUIRES_APPROVAL`` beendet die Kette NICHT — eine spaetere Regel koennte
    noch ein DENY liefern, und ein DENY schlaegt jede Freigabe. Erst am Ende
    wird aus einer gemerkten Freigabepflicht das Verdikt.
    """
    chain = tuple(rules) if rules is not None else RULE_CHAIN
    pending_approval: tuple[str, str] | None = None

    for rule in chain:
        rule_id = getattr(rule, "rule_id", rule.__name__)
        try:
            result = rule(ctx)
        except Exception as exc:  # noqa: BLE001 - jede Ueberraschung ist ein DENY
            logger.error(
                "payment_policy_rule_failed",
                extra={"rule_id": rule_id, "error_type": type(exc).__name__},
            )
            return PaymentPolicyDecision(
                verdict=Verdict.DENY,
                reasons=(f"rule {rule_id} raised {type(exc).__name__}: {exc}",),
                rule_ids=(rule_id,),
                evaluated_at=ctx.evaluated_at,
            )
        if result.verdict is Verdict.DENY:
            return PaymentPolicyDecision(
                verdict=Verdict.DENY,
                reasons=(result.reason,),
                rule_ids=(rule_id,),
                evaluated_at=ctx.evaluated_at,
            )
        if result.verdict is Verdict.REQUIRES_APPROVAL and pending_approval is None:
            pending_approval = (rule_id, result.reason)

    if pending_approval is not None:
        rule_id, reason = pending_approval
        return PaymentPolicyDecision(
            verdict=Verdict.REQUIRES_APPROVAL,
            reasons=(reason,),
            rule_ids=(rule_id,),
            evaluated_at=ctx.evaluated_at,
        )
    return PaymentPolicyDecision(verdict=Verdict.ALLOW, evaluated_at=ctx.evaluated_at)


#: Re-Export, damit ein Aufrufer nur ein Modul kennen muss.
__all__ = [
    "RULE_CHAIN",
    "ActorLimits",
    "PolicyContext",
    "Rule",
    "RuleResult",
    "evaluate",
]
