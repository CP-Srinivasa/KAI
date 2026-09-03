"""Geldwerte des Payment Control Plane (ADR 0018 §3).

Getrennt von :mod:`app.payments.models`, weil hier die einzige Arithmetik des
Pakets liegt: was ein Betrag ist, wann zwei Betraege zusammenpassen und warum
kein float vorkommt. Die Records daneben sind reine Strukturen.

Hier liegen ausserdem die drei Bausteine, die jeder Record teilt: die
``FROZEN``-Konfiguration und die beiden Validatoren ``require_aware`` (keine
naive Zeit im Geldpfad) und ``require_hash`` (keine Referenz ohne Form).

**Kein float.** Betraege sind ganzzahlige minor units, Kurse ganzzahliges ppm.
Ein float-Kurs ist ein Rundungsfehler mit Zeitstempel.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

FROZEN = ConfigDict(frozen=True, extra="forbid")

#: 64 Hex-Zeichen — jede Referenz auf Rail-Material ist ein SHA-256-Hash.
HASH_LENGTH = 64


def require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware (UTC)")
    return value.astimezone(UTC)


def require_hash(value: str, field: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) != HASH_LENGTH or any(c not in "0123456789abcdef" for c in cleaned):
        raise ValueError(f"{field} must be a {HASH_LENGTH}-char hex hash")
    return cleaned


class Money(BaseModel):
    """Ein Betrag in ganzzahligen minor units einer benannten Einheit.

    ``minor_units >= 0``: eine Routing-Gebuehr von 0 ueber einen direkten Kanal
    ist real, sie auf ``None`` abzubilden waere Informationsverlust. Die
    Forderung "> 0" gehoert an den Betrag, der bewegt werden soll
    (``PaymentIntent.amount_requested``), nicht an den Typ. Ein NEGATIVER
    Betrag ist dagegen nirgends gueltig — die Richtung einer Zahlung steht im
    Intent, nie im Vorzeichen.
    """

    model_config = FROZEN

    minor_units: int = Field(ge=0)
    currency: str = Field(min_length=1, max_length=8)
    scale: int = Field(ge=0, le=12)

    @field_validator("currency")
    @classmethod
    def _normalise_currency(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("currency must not be blank")
        return cleaned

    @property
    def unit(self) -> tuple[str, int]:
        return self.currency, self.scale

    def __add__(self, other: Money) -> Money:
        if self.unit != other.unit:
            raise ValueError(f"unit mismatch: {self.unit} + {other.unit}")
        return Money(
            minor_units=self.minor_units + other.minor_units,
            currency=self.currency,
            scale=self.scale,
        )


class Asset(BaseModel):
    """Die Recheneinheit eines Rails (ADR §3)."""

    model_config = FROZEN

    symbol: str = Field(min_length=1, max_length=16)
    currency: str = Field(min_length=1, max_length=8)
    scale: int = Field(ge=0, le=12)
    network: str = Field(min_length=1, max_length=32)

    def money(self, minor_units: int) -> Money:
        return Money(minor_units=minor_units, currency=self.currency, scale=self.scale)


class Fee(BaseModel):
    """Grenze und tatsaechliche Gebuehr bleiben getrennte Groessen."""

    model_config = FROZEN

    limit: Money | None = None
    actual: Money | None = None


class ExchangeRateReference(BaseModel):
    """Der Kurs, unter dem ein Intent gebildet wurde — als ppm, nicht als float."""

    model_config = FROZEN

    source: str = Field(min_length=1, max_length=32)
    base: str = Field(min_length=1, max_length=8)
    quote: str = Field(min_length=1, max_length=8)
    rate_ppm: int = Field(gt=0)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "observed_at")


__all__ = [
    "FROZEN",
    "HASH_LENGTH",
    "Asset",
    "ExchangeRateReference",
    "Fee",
    "Money",
    "require_aware",
    "require_hash",
]
