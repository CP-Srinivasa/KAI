"""Lightning-Empfangspfad (L4) — Invoice-Mint, hart gegatet.

Herausgeloest aus ``app/lightning/value_layer.py``, das mit dem Rueckbau des
alten Sendewegs faellt (ADR 0018 §12). Was hier steht, ist die **einzige echte
Einnahmequelle** der Plattform: ``truth_oracle`` praegt fuer jede unbezahlte
L402-Anfrage eine Invoice ueber :func:`create_invoice`.

**Warum der Empfang NICHT auf den Payment Control Plane migriert.**
``PaymentService.create_invoice`` journalliert die Forderung — und damit laege
der anonyme, unauthentifizierte Mint hinter dem exklusiven Interprozess-Lock
des einen Geldjournals, mit ``PaymentJournal.open()`` fail-closed davor. Ein
zerrissener Tail des SPEND-Journals antwortete der Oeffentlichkeit dann mit
503 (BL-2), und genau davor existiert die Asymmetrie:

  * **SPEND** — Journal ist Vorbedingung, kein Journal, kein Send.
  * **RECEIVE** — der Audit-Trail (``receive_ledger``) ist Best-Effort in einer
    EIGENEN Datei. Der Mint darf nie an seinem Protokoll scheitern; Geld, das
    wirklich ankommt, wird ohnehin aus der Invoice-DB des Nodes gebucht
    (``earnings_booking``), nicht aus diesem Trail.

**Die Memo-Semantik ist Vertrag, nicht Kosmetik.** ``kai-oracle:{scope}`` ist
das Feld, an dem ``app/lightning/earnings_ledger.py`` eine Einnahme ihrer
Quelle zuordnet. Sie bleibt unveraendert — auch deshalb ist der Mint hier
geblieben und nicht auf ``MEMO_PREFIX = "kai-pay: "`` umgestellt worden.

Der Mint bleibt trotzdem gegatet: ``receive_enabled`` (``APP_LN_RECEIVE_ENABLED``)
ist der Kill-Switch, ``dry_run`` ist Default, und die Credential ist
scope-minimal (``invoice``) — nie readonly, nie admin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.receive_ledger import append_receive_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValueLayerResult:
    """Outcome of a gated value-layer action. ``state`` is the honest disposition."""

    action: str  # "create_invoice"
    state: str  # "disabled" | "planned" | "executed" | "error"
    detail: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    # Korrelations-Id. Nicht-leer genau dann, wenn der Aufrufer eine mitgibt.
    intent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "state": self.state,
            "detail": self.detail,
            "plan": self.plan,
            "response": self.response,
            "intent_id": self.intent_id,
        }


def _settings(cfg: LightningSettings | None) -> LightningSettings:
    if cfg is not None:
        return cfg
    from app.core.settings import get_settings

    return get_settings().lightning


# U1 fail-closed allowlist: the ONLY actions that may be classified receive-side
# (capital-free, gated by ``receive_enabled``). EVERYTHING else is a spend and gates
# on ``pay_enabled``. Keeping this a 1-element set of a spend-free action name means a
# future misclassification falls to the SAFE side (receive breaks, no spend opens).
RECEIVE_ACTIONS = frozenset({"create_invoice"})


def _assert_send_allowed(
    action: str,
    *,
    cfg: LightningSettings,
    dry_run: bool,
    confirm: bool,
    irreversible: bool,
    plan: dict[str, Any],
    direction: str = "send",
) -> ValueLayerResult | None:
    """B-002 — der EINE Chokepoint, den jeder Schreibzugriff passiert, BEVOR der
    Node beruehrt wird. Liefert ein terminales ``ValueLayerResult``
    (disabled/planned) zum Abkuerzen, oder ``None``, wenn die Aktion darf.

    **Warum der Send-Zweig hier bleibt, obwohl dieses Modul nur empfaengt.** Er
    ist der fail-closed Boden: ``direction`` wird von jeder Aufrufstelle
    AUSDRUECKLICH deklariert, eine nicht-allowlistete Aktion mit
    ``direction="receive"`` ist ein Programmierfehler und WIRFT, und jede
    unbekannte Richtung faellt auf das strengere ``pay_enabled``-Gate. Wer hier
    kuenftig eine schreibende Methode ergaenzt, kann sie nicht versehentlich
    zum Empfang erklaeren — sie waere sonst mit ``receive_enabled`` allein
    scharf.
    """
    if direction == "receive":
        if action not in RECEIVE_ACTIONS:
            raise ValueError(
                f"action {action!r} declared direction='receive' but is not in RECEIVE_ACTIONS"
            )
        if not cfg.receive_enabled:
            return ValueLayerResult(action, "disabled", "receive_enabled is False", plan)
    else:
        # send (default) — also the fail-closed branch for any unrecognised direction.
        if not cfg.pay_enabled:
            return ValueLayerResult(action, "disabled", "pay_enabled is False", plan)
    if dry_run:
        return ValueLayerResult(action, "planned", "dry_run", plan)
    if irreversible and not confirm:
        return ValueLayerResult(action, "planned", "confirm=False", plan)
    return None


async def create_invoice(
    *,
    value_sat: int,
    memo: str = "",
    dry_run: bool = True,
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Create a BOLT11 invoice (receive-side, no spend) — gated + dry-run-default.

    RECEIVE asymmetry: this method NEVER fails because of its audit trail. The
    mint is the only real revenue path and is reached by anonymous callers; a
    journal problem may cost a log line, never an invoice.
    """
    cfg = _settings(cfg)
    plan = {"value_sat": int(value_sat), "memo": memo}
    blocked = _assert_send_allowed(
        "create_invoice",
        cfg=cfg,
        dry_run=dry_run,
        confirm=True,
        irreversible=False,
        plan=plan,
        direction="receive",
    )
    if blocked is not None:
        return blocked
    if value_sat <= 0:
        return ValueLayerResult("create_invoice", "error", "value_sat must be > 0", plan)
    correlation = str(intent_id or "")
    try:
        resp = await _build_client(cfg, credential_scope="invoice").add_invoice(
            value_sat=value_sat, memo=memo
        )
    except LightningUnavailableError as exc:
        return _audit_receive(
            ValueLayerResult("create_invoice", "error", str(exc), plan, intent_id=correlation),
            authorization=authorization,
        )
    return _audit_receive(
        ValueLayerResult("create_invoice", "executed", "", plan, resp, intent_id=correlation),
        authorization=authorization,
    )


def _audit_receive(
    result: ValueLayerResult, *, authorization: dict[str, Any] | None
) -> ValueLayerResult:
    """Record a node-touching RECEIVE outcome in the separate receive journal.

    Returns the result UNCHANGED in every case — this is an audit, not a gate.
    """
    append_receive_event(
        result.action,
        result.state,
        plan=result.plan,
        response=result.response,
        intent_id=result.intent_id,
        authorization=authorization,
    )
    return result


__all__ = [
    "RECEIVE_ACTIONS",
    "ValueLayerResult",
    "create_invoice",
]
