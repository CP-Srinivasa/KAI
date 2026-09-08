"""Die Statussprache nach aussen — vier Worte, eine Abbildung (KAI PAY v0.1).

Der Kern kennt vierzehn Zustaende (``PaymentStatus``), weil er ueber Geld
Auskunft geben muss, das unterwegs sein KANN. Ein Kaeufer braucht das nicht: er
will wissen, ob er warten, aufhoeren oder liefern soll. Diese Datei ist die
einzige Stelle, an der aus der vollen Wahrheit die kurze Antwort wird — rein,
ohne Uhr aus dem Modul, ohne Netz, ohne Datei. Genau deshalb ist sie
vollstaendig pruefbar.

**Drei Regeln, und jede hat ihren Preis auf der Gegenseite:**

1. **``SETTLED`` ist final.** Sagt der Rail einmal "bezahlt", wird daraus nie
   wieder etwas anderes — auch nicht durch Ablauf und auch nicht durch einen
   spaeteren Rail-Fehler. Eine Zahlung, die kurz nach dem Eingang wieder als
   offen erscheint, waere eine doppelt gelieferte Leistung.
2. **``EXPIRED`` braucht eine Aussage des Rails.** Es ist eine Behauptung ueber
   Geld ("niemand hat gezahlt"), und die darf die Uhr allein nicht treffen.
   Antwortet der Rail nicht, bleibt es ``WAITING`` — spaet und richtig statt
   schnell und falsch.
3. **``FAILED`` nur bei endgueltiger Rail-Aussage.** Heute liefert kein Rail
   sie: ``InvoiceStatus`` kennt nur ``settled`` ja/nein. Die Abbildung steht
   trotzdem hier, damit ein Rail, der spaeter ein storniertes Invoice meldet,
   genau EINE Stelle hat, an der er landet — und nicht eine neue erfindet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PayStatus(StrEnum):
    """Was ein Aufrufer von ``/pay`` zu sehen bekommt."""

    WAITING = "WAITING"
    SETTLED = "SETTLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


#: Zustaende, aus denen heraus sich nichts mehr aendert. Der Poller fragt sie
#: nicht mehr nach, und ``refresh`` schreibt sie nicht mehr um.
TERMINAL: frozenset[PayStatus] = frozenset({PayStatus.SETTLED, PayStatus.EXPIRED, PayStatus.FAILED})


@dataclass(frozen=True)
class RailView:
    """Was der Rail ueber eine ausgestellte Forderung gesagt hat.

    ``settled=None`` heisst ausdruecklich **keine Aussage** — Timeout,
    Transportfehler, Node unerreichbar. Es ist nicht dasselbe wie ``False``,
    und der Unterschied entscheidet, ob eine Forderung ablaufen darf.
    """

    settled: bool | None
    #: Der Rail sagt: diese Forderung wird nie beglichen (storniert). Heute
    #: liefert das keiner; die Abbildung wartet auf ihn, statt spaeter erfunden
    #: zu werden.
    canceled: bool = False


def next_status(
    current: PayStatus,
    view: RailView,
    *,
    now: datetime,
    expires_at: datetime,
) -> PayStatus:
    """Der Zustand nach einer Rail-Befragung. Kennt keine Seiteneffekte.

    Args:
        current: Was die Produktschicht bisher gespeichert hat.
        view: Die Aussage des Rails (oder ihr Ausbleiben).
        now: Die Uhr des Aufrufers — als Argument, damit ein Test sie stellt.
        expires_at: Ablauf der ausgestellten Forderung.
    """
    if current in TERMINAL:
        return current
    if view.settled:
        return PayStatus.SETTLED
    if view.canceled:
        return PayStatus.FAILED
    if view.settled is None:
        # Fail-soft: ohne Aussage des Rails wird nichts terminal. Ein Ablauf
        # waere hier eine Behauptung ueber Geld auf Basis einer Stille.
        return PayStatus.WAITING
    return PayStatus.EXPIRED if now >= expires_at else PayStatus.WAITING


__all__ = ["TERMINAL", "PayStatus", "RailView", "next_status"]
