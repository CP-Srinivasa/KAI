"""Was VOR dem Journal-Lock passiert (ADR 0017 §5).

Decode und Node-Health sind Netzaufrufe. Sie laufen ausdruecklich ausserhalb
des Serialisierungspunkts und sind damit **Vorschau**: was sie liefern, kann in
der Zeit bis zum Verdikt veraltet sein. Ein Node-Aufruf unter dem Lock waere
das Gegenteil eines Serialisierungspunkts — er hielte jeden anderen Schreiber
fuer die Dauer eines Netzaufrufs an.

Beide Funktionen geben ``None`` zurueck, wenn der Rail nicht antworten konnte.
Das ist kein Absturz, sondern ein Eingabewert fuer die Policy — und die lehnt
sowohl einen fehlenden Decode (``destination_allowlist``) als auch eine
fehlende Gesundheitsmessung (``node_health``) ab. Fail-closed entsteht hier
also nicht durch eine Ausnahme, sondern durch eine Regel, die den fehlenden
Wert sieht.
"""

from __future__ import annotations

from app.payments.idempotency import hash_destination
from app.payments.rail import DecodedDestination, PaymentRail, RailError, RailHealth
from app.payments.service_types import Tracked


async def decode_or_none(rail: PaymentRail, destination: str) -> DecodedDestination | None:
    """``None`` heisst: der Rail konnte das Ziel nicht binden."""
    try:
        return await rail.decode(destination)
    except RailError:
        return None


async def health_or_none(rail: PaymentRail) -> RailHealth | None:
    """``None`` heisst: keine Messung — nicht "gesund"."""
    try:
        return await rail.health()
    except RailError:
        return None


def dedup_key_for(tracked: Tracked) -> str:
    """Der Schluessel, unter dem der RAIL dedupliziert (ADR §5).

    Vorzugsweise der aus dem Decode (Lightning: ``payment_hash``). Ohne Decode
    ein deterministischer Ersatz aus der Destination — deterministisch, damit
    ein Retry denselben Schluessel traegt und nicht als neue Zahlung durchgeht.
    """
    if tracked.decoded is not None:
        return tracked.decoded.rail_dedup_key
    return hash_destination(tracked.intent.destination)


__all__ = ["decode_or_none", "dedup_key_for", "health_or_none"]
