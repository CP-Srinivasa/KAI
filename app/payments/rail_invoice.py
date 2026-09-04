"""Die Empfangsseite eines Rails (ADR 0018 §1/§7).

Getrennt von :mod:`app.payments.rail`, weil Senden und Empfangen verschiedene
Fragen stellen. Ein Send fragt *"kam es an?"* und wird von einem Aufrufer
begleitet, der auf die Antwort wartet. Eine Forderung fragt *"hat jemand
gezahlt?"*, und die Antwort trifft ein, wenn niemand hinsieht — der einzige
Beobachter ist der Reconciler.

Die Trennung haelt ``rail.py`` unter der 350-Zeilen-Grenze (ADR §2) und legt
die eine Stelle frei, an der oeffentlicher TEXT den Rail erreicht: das
``memo``. Alles andere in diesem Paket geht als Hash hinaus.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.payments.models import Money
from app.payments.money import FROZEN, HASH_LENGTH, require_aware
from app.payments.redaction import contains_raw_rail_material

#: Praefix jeder Forderung, die KAI ausstellt. Die Einnahmenbuchung
#: (``app/lightning/earnings_ledger.py``) erkennt eigene Invoices AM MEMO —
#: ein Praefix ist deshalb keine Kosmetik, sondern die Zuordnung.
MEMO_PREFIX = "kai-pay: "

#: lnd nimmt laengere Memos an; 200 Zeichen sind genug fuer eine
#: Bestellreferenz und kurz genug, dass niemand auf die Idee kommt, Nutzdaten
#: in einer oeffentlichen Invoice zu transportieren.
MAX_MEMO_LENGTH = 200


class InvoiceRequest(BaseModel):
    """Eine Forderung, die KAI ausstellen will (Self-Use-Receivable, ADR §1).

    **Warum hier ein ``memo`` steht und kein ``memo_hash``.** Bis zum
    LIVE-Fenster 2026-09-04 trug dieses Modell einen vom Aufrufer
    mitgegebenen ``memo_hash`` — einen Hash ohne Urbild. Er belegte nichts
    (niemand konnte pruefen, wovon er stammte) und erreichte den Node nie, weil
    ``LightningRail.create_invoice`` gar kein ``memo`` durchreichte. Die Folge
    stand am Geraet: Forderungen ohne KAI-Praefix, und
    ``app/lightning/earnings_ledger.py`` — das auf genau diesem Praefix matcht —
    hat sie nicht gebucht.

    Jetzt traegt die Anfrage den TEXT, der Rail sendet ihn, und der Hash wird
    aus dem gesendeten Text abgeleitet. Damit belegt der ``memo_hash`` im
    Journal etwas, das wirklich passiert ist.
    """

    model_config = FROZEN

    amount: Money
    #: Der Text, den der Node in die Invoice schreibt. Er ist OEFFENTLICH —
    #: jeder, der die Forderung sieht, liest ihn mit. Deshalb nie ein
    #: Geheimnis, und mechanisch kein Rail-Rohmaterial (Validator unten).
    memo: str = Field(default="", max_length=MAX_MEMO_LENGTH)
    #: Eine Stunde — dieselbe Frist wie an der HTTP-Grenze. Zwei Defaults fuer
    #: dieselbe Frist waeren eine Falle: der interne Aufrufer bekaeme still
    #: eine andere Invoice als der Operator ueber die API.
    expiry_seconds: int = Field(default=3600, gt=0, le=86_400)
    purpose: str = Field(min_length=1, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def _default_memo(cls, data: Any) -> Any:
        """Ohne eigenen Text traegt die Forderung ``kai-pay: <purpose>``.

        Der Default steht hier und nicht im Rail: sonst haetten SimulationRail
        und LightningRail zwei Defaults fuer denselben Text, und der
        ``memo_hash`` im Journal haette je nach Modus ein anderes Urbild.
        """
        if not isinstance(data, dict):  # pragma: no cover - pydantic reicht Modelle durch
            return data
        memo = str(data.get("memo") or "").strip()
        if memo:
            return data
        purpose = str(data.get("purpose") or "").strip()
        return {**data, "memo": f"{MEMO_PREFIX}{purpose}"} if purpose else data

    @field_validator("memo")
    @classmethod
    def _memo_is_public(cls, value: str) -> str:
        if contains_raw_rail_material(value):
            raise ValueError(
                "memo looks like raw rail material (BOLT11 prefix or long hex) — "
                "an invoice memo is public text, not a place for a payment request "
                "or a preimage"
            )
        return value


class InvoiceStatus(BaseModel):
    """Ob eine ausgestellte Forderung beglichen wurde."""

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    ref_hash: str = Field(min_length=HASH_LENGTH, max_length=HASH_LENGTH)
    settled: bool
    observed_at: datetime
    amount_paid: Money | None = None
    settled_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "observed_at")


__all__ = [
    "MAX_MEMO_LENGTH",
    "MEMO_PREFIX",
    "InvoiceRequest",
    "InvoiceStatus",
]
