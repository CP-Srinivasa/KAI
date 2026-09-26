"""Der gemeinsame Vorschauvertrag fuer Dashboard und Telegram (D-288, Befund 6).

Vorher sahen die beiden Oberflaechen vor derselben Freigabe Verschiedenes:
``/pay`` im Telegram lief durch die Regelkette und bekam seit #1082 eine
Node-Schaetzung; das Cockpit zeigte nur Betrag und Gebuehrenobergrenze und
erfuhr von einer Ablehnung (fremder Empfaenger, Zweck, Cap) erst beim Senden.

Diese Vorschau **schreibt nichts**: kein Intent, kein Journal-Record. Sie
benutzt dieselbe Regelkette wie ``PaymentService.create_intent`` und dieselbe
Quote wie ``simulate`` — inklusive Node-Probe, aber nur, wenn die Regelkette
nicht ablehnt (ein fremder Empfaenger wird nie geprobt, D-288 P3). Ausgefuehrt
wird weiterhin ausschliesslich im ``PaymentService``.

Liegt neben :mod:`app.payments.preview` statt darin, weil ``service`` jenes
Modul importiert und dieses den Service braucht.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.payments.enums import PaymentMode, Verdict
from app.payments.models import Quote
from app.payments.policy import PolicyContext, evaluate
from app.payments.preview import decode_or_none, health_or_none
from app.payments.rail import RailError
from app.payments.service_types import PaymentRequest

if TYPE_CHECKING:  # pragma: no cover - nur fuer die Typpruefung
    from app.payments.service import PaymentService

#: Platzhalter fuer den Wegwerf-Intent der Vorschau. Er erreicht nie das Journal.
_PREVIEW_INTENT_ID = "pi_preview"
_PREVIEW_KEY = "preview-not-a-real-intent"


@dataclass(frozen=True)
class FeeAssessment:
    """Die Gebuehrenaussage vor der Freigabe — fuer beide Oberflaechen gleich.

    ``warning``: ``""`` (nichts), ``over_limit`` (Node-Schaetzung > Limit),
    ``no_route`` (Probe fand keinen Weg), ``probe_failed`` (Probe ohne
    Ergebnis), ``unavailable`` (keine Quote). Eine Settings-Zahl warnt nie: sie
    ist eine Obergrenze aus der Konfiguration, keine Messung.
    """

    estimate_sat: int | None
    source: str
    limit_sat: int
    warning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimate_sat": self.estimate_sat,
            "source": self.source,
            "limit_sat": self.limit_sat,
            "warning": self.warning,
        }


def assess_fee(quote: Quote | None, fee_limit_sat: int) -> FeeAssessment:
    """Aus einer Quote die Gebuehrenaussage machen."""
    if quote is None:
        return FeeAssessment(None, "unavailable", fee_limit_sat, "unavailable")
    source = quote.estimate_source
    if source == "node_probe_no_route":
        return FeeAssessment(None, source, fee_limit_sat, "no_route")
    estimate = quote.fee_estimate.minor_units
    if source == "node_probe_failed":
        return FeeAssessment(estimate, source, fee_limit_sat, "probe_failed")
    over = source.startswith("node_") and estimate > fee_limit_sat
    return FeeAssessment(estimate, source, fee_limit_sat, "over_limit" if over else "")


@dataclass(frozen=True)
class PaymentPreview:
    """Regelkette + Gebuehr vor der Freigabe."""

    verdict: str
    rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    destination_known: bool
    fee: FeeAssessment

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "rule_ids": list(self.rule_ids),
            "reasons": list(self.reasons),
            "destination_known": self.destination_known,
            "fee": self.fee.to_dict(),
        }


async def preview_payment(service: PaymentService, request: PaymentRequest) -> PaymentPreview:
    """Dieselbe Pruefung wie ``create_intent`` — ohne Intent, ohne Record."""
    rail = service.rail
    moment = service._clock()  # noqa: SLF001 - dieselbe Uhr wie der Service
    intent = request.to_intent(
        intent_id=_PREVIEW_INTENT_ID,
        idempotency_key=_PREVIEW_KEY,
        moment=moment,
        mode=PaymentMode(service.settings.mode),
    )
    decoded = await decode_or_none(rail, request.destination)
    health = await health_or_none(rail)
    decision = evaluate(
        PolicyContext(
            intent=intent,
            settings=service.settings,
            rail_caps=rail.capabilities(),
            rail_health=health,
            available_liquidity_sat=health.available_balance_sat if health is not None else None,
            spent_today_sat=service.journal.index.totals_for_day(moment).amount_sent,
            actor_limits=service._actor_limits.get(intent.actor),  # noqa: SLF001
            decoded_destination=decoded,
            app_env=service._app_env,  # noqa: SLF001
            evaluated_at=moment,
        )
    )
    quote: Quote | None = None
    if decision.verdict is not Verdict.DENY:
        try:
            quote = await rail.quote(intent)
        except RailError:
            quote = None
    return PaymentPreview(
        verdict=decision.verdict.value,
        rule_ids=tuple(decision.rule_ids),
        reasons=tuple(decision.reasons),
        destination_known=decoded is not None,
        fee=assess_fee(quote, request.fee_limit.minor_units),
    )


__all__ = ["FeeAssessment", "PaymentPreview", "assess_fee", "preview_payment"]
