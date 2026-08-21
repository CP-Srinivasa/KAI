"""Reine Rechenregeln fuer Provenienz-Angaben eines Fills.

Bewusst ohne Engine-Bezug und ohne Zustand: der Verifier wird diese Felder als
Wahrheit lesen, also muessen die Regeln einzeln pruefbar sein. Erster Schritt der
Zerlegung von ``paper_engine.py`` — dort gehoert kuenftig weniger hinein, nicht
mehr.
"""

from __future__ import annotations

__all__ = ["_age_ms_at_fill", "_finite_or_none"]


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
