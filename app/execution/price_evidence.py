"""Reine Rechenregeln fuer Provenienz-Angaben eines Fills.

Bewusst ohne Engine-Bezug und ohne Zustand: der Verifier wird diese Felder als
Wahrheit lesen, also muessen die Regeln einzeln pruefbar sein. Erster Schritt der
Zerlegung von ``paper_engine.py`` — dort gehoert kuenftig weniger hinein, nicht
mehr.
"""

from __future__ import annotations

import math

from app.execution.models import PriceEvidence

__all__ = ["_age_ms_at_fill", "_finite_or_none", "quote_evidence"]


def quote_evidence(point: object) -> PriceEvidence:
    """Beleg einer Kursquote (``MarketDataPoint``) fuer den Fill, der gegen sie laeuft.

    EINE Regel fuer Einstieg und Monitor-Close. Vorher gab nur der Positionsmonitor
    einen Beleg mit; Einstiege standen mit leerer ``price_source`` im Audit. So
    blieb der MATIC-Einstieg vom 23.09.2026 zu einem eingefrorenen BitMEX-Kurs
    (0.40875) ohne Spur seiner Quelle.

    ``freshness_seconds`` ist das Alter der Quote beim Abruf. Fehlt es, bleibt
    ``age_ms`` None — fehlende Evidenz wird ausgewiesen, nicht geraten.
    """
    age_s = getattr(point, "freshness_seconds", None)
    return PriceEvidence(
        source=str(getattr(point, "source", "") or ""),
        observed_at_utc=str(getattr(point, "timestamp_utc", "") or ""),
        observed_price=_price_or_none(getattr(point, "price", None)),
        age_ms=(float(age_s) * 1000.0 if isinstance(age_s, (int, float)) else None),
        is_stale=bool(getattr(point, "is_stale", False)),
        # Zweitanbieter-Bestaetigung (2026-09-23): nur sie laesst einen Close
        # ueber dem Phantom-Cap zu (app/execution/close_guard.py).
        corroborated_by=str(getattr(point, "corroborated_by", "") or ""),
        corroborating_price=_price_or_none(getattr(point, "corroborating_price", None)),
    )


def _price_or_none(value: object) -> float | None:
    """Ein Kurs ist endlich und groesser 0 — sonst keiner."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) and out > 0 else None


def _finite_or_none(value: object) -> float | None:
    """Nur endliche, nicht-negative Zahlen. NaN/Inf/negativ gelangen nie ins Audit.

    Ein nicht-endlicher Wert in einem Provenienz-Feld waere schlimmer als ein
    fehlender: er sieht aus wie eine Messung.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")) or out < 0:
        return None
    return out


def _age_ms_at_fill(observed_at_utc: str, filled_at_utc: str) -> float | None:
    """Abstand Beobachtung -> Fuellen in ms. None, wenn nicht sauber bestimmbar.

    Bewusst NICHT der vom Adapter beim Abruf gemeldete Wert: zwischen Abruf und
    Fuellen vergeht Zeit. Beides steht getrennt im Fill.
    """
    from datetime import datetime

    if not observed_at_utc or not filled_at_utc:
        return None
    try:
        obs = datetime.fromisoformat(str(observed_at_utc).replace("Z", "+00:00"))
        fil = datetime.fromisoformat(str(filled_at_utc).replace("Z", "+00:00"))
    except ValueError:
        return None
    if obs.tzinfo is None or fil.tzinfo is None:
        return None
    return _finite_or_none((fil - obs).total_seconds() * 1000.0)
