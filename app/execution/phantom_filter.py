"""Shared phantom-close detection (DS-20260529-V1).

A paper close whose implied per-trade return exceeds a sanity cap is the
signature of a price-source disagreement (entry and exit priced by different
providers) — e.g. BitMEX's delisted "MATIC" instrument at 0.40875 vs the real
~0.088, which booked +364% per cycle. The paper engine refuses to book such
closes going forward (close_price_sanity_rejected); this module lets read-side
aggregators (realized-by-asset, paper-quality) exclude the historical phantom
closes that were booked before the guard existed, so dashboards show the real
PnL instead of the phantom profit.

Die Schwelle ist hier KANONISCH. Bis 2026-08-18 stand sie doppelt im Code --
einmal hier (2.0) und einmal in ``paper_engine`` -- mit dem Kommentar, sie
"mirrors the engine's MAX_CLOSE_RETURN_PCT". Als #722 den Motor auf 0.20
kalibrierte, blieb diese Kopie auf 2.0 stehen. Genau die Lese-Seite, die die
Vergangenheit bereinigen soll, lief damit weiter mit der alten 200-%-Marke:
die beiden ETH-Closes bei +72 % blieben als realisierter Gewinn stehen und
hielten das Buch der Epoche bei +396,73 statt -1.853,45 USD.

Deshalb importiert ``paper_engine`` jetzt VON HIER. Ein Contract-Test
(``test_phantom_threshold_single_source``) laesst Schreib- und Lesepfad nicht
mehr auseinanderlaufen.
"""

from __future__ import annotations

import os

# KALIBRIERUNG 2026-08-18, gemessen ueber alle 617 Closes des Audit-Streams:
#
#     Median 1,52 %   p90 4,92 %   p95 7,70 %
#     groesster NICHT verdaechtiger Close:  17,16 %
#     ---------------------- Luecke ----------------------
#     naechster Wert:                       21,18 %
#
# Oberhalb von 20 % liegen 20 von 617 Closes (3,2 %), und jeder einzelne ist
# ein bekanntes oder vermutetes Artefakt (MATIC 9x, SOL, MKR, ETH 3x, CYS,
# SLX, VELVET). 20 % ist damit gemessen, nicht geraten.
#
# WICHTIG zur Wirkung auf diesem Pfad: die Lese-Seite LOESCHT nichts. Ein als
# phantom erkannter Close wandert nach ``quarantined_pnl_usd`` und bleibt dem
# Operator sichtbar -- offenlegen, nicht verschweigen. Ein Fehlalarm kostet
# hier also keine Information, nur eine Umbuchung in die Quarantaene-Spalte.
_DEFAULT_MAX_CLOSE_RETURN_PCT = 0.20


def phantom_return_threshold() -> float:
    """Implied per-trade return magnitude (fraction) above which a close is phantom."""
    raw = os.environ.get("MAX_CLOSE_RETURN_PCT")
    if raw is None:
        return _DEFAULT_MAX_CLOSE_RETURN_PCT
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_MAX_CLOSE_RETURN_PCT
    return value if value > 0 else _DEFAULT_MAX_CLOSE_RETURN_PCT


def implied_close_return(entry_price: float, exit_price: float, position_side: str) -> float | None:
    """Signed per-trade return of closing at ``exit_price``. None if prices non-positive."""
    if entry_price <= 0 or exit_price <= 0:
        return None
    if position_side == "short":
        return entry_price / exit_price - 1.0
    return exit_price / entry_price - 1.0


def is_phantom_close(
    entry_price: object,
    exit_price: object,
    position_side: object,
    *,
    threshold: float | None = None,
) -> bool:
    """True when a close's implied return magnitude exceeds the phantom threshold.

    Conservative: returns False when entry/exit are missing or non-numeric — an
    unverifiable close is never silently dropped from realized PnL.
    """
    if not isinstance(entry_price, (int, float)) or not isinstance(exit_price, (int, float)):
        return False
    side = position_side if isinstance(position_side, str) else "long"
    r = implied_close_return(float(entry_price), float(exit_price), side)
    if r is None:
        return False
    cap = threshold if threshold is not None else phantom_return_threshold()
    return abs(r) > cap
