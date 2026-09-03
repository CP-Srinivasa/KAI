"""Eingaben und Ergebnisform der Regelkette (ADR 0017 §6).

Getrennt von :mod:`app.payments.policy`, weil hier steht, WORUEBER entschieden
wird, und dort, WIE. Die Trennung haelt beide Module unter der
350-Zeilen-Grenze und macht sichtbar, dass eine Regel nichts anderes kennt als
ihren Kontext: kein Env, keine Uhr, keine Datei. Nur so ergibt dieselbe Eingabe
immer dasselbe Verdikt — und nur dann ist ein Verdikt im Journal spaeter
nachvollziehbar.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.core.payment_settings import PaymentSettings
from app.payments.enums import PaymentStatus, Verdict
from app.payments.models import PaymentIntent
from app.payments.rail import DecodedDestination, RailAction, RailCapabilities, RailHealth


@dataclass(frozen=True)
class ActorLimits:
    """Was ein Agent darf (ADR §1 Agent-Flow).

    Eine Zeile in dieser Tabelle ist eine Erlaubnis, kein Vermerk: fehlt sie,
    darf der Agent nichts. Ein Agent bekommt nie ein Macaroon — er erzeugt
    Intents und bekommt Status zurueck.
    """

    actor: str
    max_amount_sat: int
    daily_max_sat: int
    purposes: frozenset[str]
    rails: frozenset[str]
    approval_threshold_sat: int | None = None


@dataclass(frozen=True)
class PolicyContext:
    """Alles, was die Kette braucht — und nichts, was sie selbst holen muesste.

    Die Regeln greifen bewusst auf keine Umgebung zu: sie sind rein, damit
    dieselbe Eingabe immer dasselbe Verdikt ergibt und ein Verdikt im Journal
    spaeter nachvollziehbar bleibt.
    """

    intent: PaymentIntent
    settings: PaymentSettings
    rail_caps: RailCapabilities | None
    rail_health: RailHealth | None
    spent_today_sat: int
    actor_limits: ActorLimits | None
    decoded_destination: DecodedDestination | None
    app_env: str
    evaluated_at: datetime
    available_liquidity_sat: int | None = None
    attempt_no: int = 1
    previous_status: PaymentStatus | None = None
    action: RailAction = RailAction.PAY_INVOICE


@dataclass(frozen=True)
class RuleResult:
    """Das Urteil EINER Regel."""

    verdict: Verdict
    reason: str = ""

    @classmethod
    def allow(cls) -> RuleResult:
        return cls(verdict=Verdict.ALLOW)

    @classmethod
    def deny(cls, reason: str) -> RuleResult:
        return cls(verdict=Verdict.DENY, reason=reason)

    @classmethod
    def approval(cls, reason: str) -> RuleResult:
        return cls(verdict=Verdict.REQUIRES_APPROVAL, reason=reason)


Rule = Callable[[PolicyContext], RuleResult]


def rule_id(name: str) -> Callable[[Rule], Rule]:
    """Haefte einer Regel ihre ID an.

    Die ID steht damit AN der Regel, nicht in einer zweiten Liste daneben —
    zwei Listen, die auseinanderlaufen, waeren genau die Sorte Defekt, die eine
    falsche Begruendung ins Journal schreibt.
    """

    def decorate(func: Rule) -> Rule:
        func.rule_id = name  # type: ignore[attr-defined]
        return func

    return decorate


__all__ = [
    "ActorLimits",
    "PolicyContext",
    "Rule",
    "RuleResult",
    "rule_id",
]
