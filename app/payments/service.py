"""Der Payment Control Plane — die eine Orchestrierung (ADR 0018 §5/§9).

``PaymentIntent -> Policy -> Authorization -> Rail -> Settlement``. Es gibt
genau einen Aufrufer von ``rail.pay()``, und er steht in
:meth:`PaymentService.execute`.

**Was unter dem Lock passiert und was davor.** Decode und Health sind
Node-Aufrufe; sie laufen VOR dem Journal-Lock und sind ausdruecklich Vorschau
(ADR §5). Unter dem Lock passieren dann in einem Zug: Idempotenz-Konsum,
Cap-Lesung aus dem Index, Policy-Verdikt und die Records. Nur so kann der
Tages-Cap nicht zwischen Pruefung und Buchung ueberholt werden (Red-Team
D-08). Ein Node-Aufruf unter dem Lock waere das Gegenteil: er haelt jeden
anderen Schreiber fuer die Dauer eines Netzaufrufs an.

**Der Sendepfad liegt nebenan** (:mod:`app.payments.execution`) — das ist die
Grenze, ab der Geld unwiderruflich wird.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.payment_settings import PaymentSettings
from app.payments.approval import grant
from app.payments.enums import PaymentMode, PaymentStatus
from app.payments.execution import apply_rail_result, recover_open_intents, write_ahead
from app.payments.idempotency import consume
from app.payments.intent_state import REHYDRATABLE, rehydrate, synced, view_of
from app.payments.intent_vault import IntentVault
from app.payments.journal import PaymentJournal
from app.payments.models import Invoice, PaymentAttempt, PaymentAuditEvent, Quote
from app.payments.policy import ActorLimits, PolicyContext, evaluate
from app.payments.preview import decode_or_none, dedup_key_for, health_or_none
from app.payments.rail import (
    InvoiceRequest,
    InvoiceStatus,
    PaymentRail,
    RailError,
)
from app.payments.receivables import record_invoice
from app.payments.service_types import (
    IntentView,
    PaymentRequest,
    PaymentServiceError,
    SimulationView,
    Tracked,
)
from app.payments.status import TransitionEvidence, status_for_verdict, transition

#: Welcher Rail in welchem Modus arbeitet (ADR §1). SHADOW benutzt denselben
#: Adapter wie LIVE — read-only; der Unterschied ist nicht der Rail, sondern
#: dass ``execute`` ihn nie erreicht.
_RAIL_FOR_MODE = {"simulation": "simulation", "shadow": "lightning", "live": "lightning"}

#: Zustaende, aus denen heraus ein ``execute`` ueberhaupt sinnvoll ist.
_EXECUTABLE = frozenset({PaymentStatus.AUTHORIZED})


class PaymentService:
    """Die eine Orchestrierung. Einziger Aufrufer von ``rail.pay()``."""

    def __init__(
        self,
        *,
        journal: PaymentJournal,
        rails: Mapping[str, PaymentRail],
        settings: PaymentSettings,
        clock: Callable[[], datetime] | None = None,
        app_env: str = "development",
        hotp_verifier: Any = None,
        actor_limits: Mapping[str, ActorLimits] | None = None,
        vault: IntentVault | None = None,
    ) -> None:
        self._journal = journal
        self._rails = dict(rails)
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._app_env = app_env
        self._hotp = hotp_verifier
        self._actor_limits = dict(actor_limits or {})
        #: Der verschluesselte Sidecar. ``None`` heisst: dieser Dienst ueberlebt
        #: keinen Neustart — zulaessig fuer Tests und Werkzeuge, aber nie fuer
        #: SHADOW oder LIVE (``validate_payment_boot`` verlangt dort den
        #: Schluessel, ``wiring`` baut den Vault dann immer).
        self._vault = vault
        self._tracked: dict[str, Tracked] = {}

    # -- Lesbare Innereien --------------------------------------------------- #
    #
    # Der Health-Pfad braucht Journal, Rail und Settings, ohne sie ein zweites
    # Mal zu bauen — zwei Journal-Objekte auf derselben Datei waeren zwei
    # Indizes mit eigener Meinung.

    @property
    def journal(self) -> PaymentJournal:
        return self._journal

    @property
    def settings(self) -> PaymentSettings:
        return self._settings

    @property
    def rail(self) -> PaymentRail:
        """Der Rail des aktuellen Modus (ADR §1)."""
        return self._active_rail()

    # -- Start -------------------------------------------------------------- #

    def recover(self) -> list[str]:
        """Erst klaeren (``execution``), dann zurueckholen (``intent_state``).

        Die Reihenfolge ist die Aussage: ein ``submitted`` ohne Antwort geht in
        die Klaerung, BEVOR der Vault etwas wieder ausfuehrbar macht.

        Returns:
            Nur die in die Klaerung gehobenen Vorgaenge — eine gelungene
            Wiederherstellung ist kein Alarm.
        """
        recovered = recover_open_intents(self._journal, clock=self._clock)
        rehydrate(self._journal, self._vault, self._tracked)
        return recovered

    # -- Erzeugen ----------------------------------------------------------- #

    async def create_intent(self, request: PaymentRequest, idempotency_key: str) -> IntentView:
        """Idempotenz, Policy und beide Records unter EINEM Lock (ADR §5)."""
        rail = self._active_rail()
        moment = self._clock()
        intent = request.to_intent(
            intent_id=f"pi_{uuid.uuid4().hex[:16]}",
            idempotency_key=idempotency_key,
            moment=moment,
            mode=PaymentMode(self._settings.mode),
        )

        # Vorschau: Node-Aufrufe gehoeren NICHT unter den Lock.
        decoded = await decode_or_none(rail, request.destination)
        health = await health_or_none(rail)

        with self._journal.transaction() as tx:
            outcome = consume(self._journal, idempotency_key, intent)
            if outcome.replayed:
                return self._replayed_view(outcome.intent_id, outcome.status)

            decision = evaluate(
                PolicyContext(
                    intent=intent,
                    settings=self._settings,
                    rail_caps=rail.capabilities(),
                    rail_health=health,
                    spent_today_sat=self._journal.index.totals_for_day(moment).amount_sent,
                    actor_limits=self._actor_limits.get(intent.actor),
                    decoded_destination=decoded,
                    app_env=self._app_env,
                    evaluated_at=moment,
                )
            )
            status = transition(
                PaymentStatus.REQUESTED,
                status_for_verdict(decision.verdict),
                evidence=TransitionEvidence(
                    actor="policy",
                    reason=f"verdict={decision.verdict.value}",
                    occurred_at=moment,
                ),
            )
            tx.append(
                intent.intent_id,
                "policy_decided",
                {
                    "verdict": decision.verdict.value,
                    "rule_ids": list(decision.rule_ids),
                    "reasons": list(decision.reasons),
                    "status": status.value,
                },
                ts=moment,
            )

        self._tracked[intent.intent_id] = Tracked(
            intent=intent, status=status, decision=decision, decoded=decoded
        )
        # Erst NACH dem Verdikt versiegeln, und nur was noch gesendet werden
        # kann. Ein abgelehnter Vorgang wird nie ausgefuehrt — sein Ziel hat
        # auch verschluesselt nichts auf der Platte zu suchen.
        if self._vault is not None and status in REHYDRATABLE:
            self._vault.seal(intent, decoded=decoded, moment=moment)
        return IntentView(intent_id=intent.intent_id, status=status, decision=decision)

    async def simulate(self, intent_id: str) -> SimulationView:
        """Quote und Policy-Vorschau — ohne jeden Send (ADR §1)."""
        tracked = self._require(intent_id)
        quote: Quote | None
        try:
            quote = await self._active_rail().quote(tracked.intent)
        except RailError:
            quote = None
        self._journal.append(
            intent_id,
            "rail_requested",
            {
                "status": tracked.status.value,
                "estimate_source": quote.estimate_source if quote else "unavailable",
                "fee_estimate_minor_units": quote.fee_estimate.minor_units if quote else 0,
            },
            ts=self._clock(),
        )
        return SimulationView(
            intent_id=intent_id, status=tracked.status, quote=quote, decision=tracked.decision
        )

    # -- Freigeben ---------------------------------------------------------- #

    def authorize(self, intent_id: str, approval_code: str) -> IntentView:
        """HOTP-Freigabe (ADR §4). Die Zeremonie steht in ``approval``."""
        tracked = self._require(intent_id)
        grant(
            self._journal,
            tracked,
            hotp_verifier=self._hotp,
            approval_code=approval_code,
            moment=self._clock(),
        )
        return IntentView(intent_id=intent_id, status=tracked.status, decision=tracked.decision)

    # -- Senden ------------------------------------------------------------- #

    async def execute(self, intent_id: str) -> IntentView:
        """Der einzige Aufruf von ``rail.pay()`` im ganzen Code."""
        if self._settings.mode == "shadow":
            raise PaymentServiceError(
                "execute refused: payment mode is shadow — it reads and computes, it never sends"
            )
        tracked = self._require(intent_id)
        if tracked.attempts > 0:
            # Kein zweiter Send. Der Aufrufer bekommt den Zustand, den er schon
            # hat — nicht einen zweiten Versuch.
            return IntentView(
                intent_id=intent_id,
                status=tracked.status,
                replayed=True,
                decision=tracked.decision,
                detail="already executed",
            )
        if tracked.status not in _EXECUTABLE:
            raise PaymentServiceError(
                f"execute refused: intent is {tracked.status.value}, expected AUTHORIZED"
            )

        rail = self._active_rail()
        moment = self._clock()
        attempt = PaymentAttempt(
            attempt_no=tracked.attempts + 1,
            intent_id=intent_id,
            rail_dedup_key=dedup_key_for(tracked),
            submitted_at=moment,
            amount_sent=tracked.intent.amount_requested,
        )
        write_ahead(self._journal, tracked, attempt=attempt, moment=moment)
        result = await rail.pay(tracked.intent, attempt)
        return apply_rail_result(
            self._journal,
            tracked,
            result,
            moment=self._clock(),
            reversal_supported=rail.capabilities().reversal_supported,
        )

    # -- Empfangen ---------------------------------------------------------- #

    async def create_invoice(self, request: InvoiceRequest, *, order_ref: str = "") -> Invoice:
        """Eine eigene Forderung ausstellen UND sie journallieren (ADR §1/§8).

        Ohne den Record haette der Reconciler nichts, wogegen er den Node
        halten koennte: eine Invoice lebt am Node, und ein Settlement daran ist
        eine Zustandsaenderung, die niemand beobachtet.
        """
        invoice = await self._active_rail().create_invoice(request)
        record_invoice(
            self._journal,
            invoice,
            purpose=request.purpose,
            order_ref=order_ref,
            moment=self._clock(),
        )
        return invoice

    async def invoice_status(self, ref_hash: str) -> InvoiceStatus:
        return await self._active_rail().invoice_status(ref_hash)

    # -- Lesen -------------------------------------------------------------- #

    def get(self, intent_id: str) -> IntentView:
        """Zustand JOURNAL-FIRST (siehe :mod:`app.payments.intent_state`)."""
        return view_of(self._journal, self._tracked.get(intent_id), intent_id)

    def audit(self, intent_id: str) -> list[PaymentAuditEvent]:
        return self._journal.events(intent_id)

    # -- Intern ------------------------------------------------------------- #

    def _active_rail(self) -> PaymentRail:
        name = _RAIL_FOR_MODE.get(self._settings.mode)
        rail = self._rails.get(name or "")
        if rail is None:
            raise PaymentServiceError(
                f"no rail available for payment mode {self._settings.mode!r} "
                f"(expected {name!r}, have {sorted(self._rails)})"
            )
        return rail

    def _replayed_view(self, intent_id: str, status: str | None) -> IntentView:
        known = PaymentStatus(status) if status else PaymentStatus.REQUESTED
        tracked = self._tracked.get(intent_id)
        return IntentView(
            intent_id=intent_id,
            status=tracked.status if tracked else known,
            replayed=True,
            decision=tracked.decision if tracked else None,
            detail="idempotency key already consumed",
        )

    def _require(self, intent_id: str) -> Tracked:
        """Der Vorgang aus dem Speicher — mit dem Journal abgeglichen."""
        tracked = self._tracked.get(intent_id)
        if tracked is None:
            raise PaymentServiceError(
                f"unknown intent: {intent_id} — no open pre-send intent under that id. "
                "Either it never existed, or it was already submitted; a submitted "
                "intent never returns through the vault, its way back is reconciliation"
            )
        return synced(self._journal, tracked)


__all__ = [
    "IntentView",
    "PaymentRequest",
    "PaymentService",
    "PaymentServiceError",
    "SimulationView",
]
