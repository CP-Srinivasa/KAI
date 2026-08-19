"""Ein Urteil über einen Close — mit vier Zuständen statt „korrupt ja/nein".

**Warum die Umstellung (2026-08-19).** Der generische Return-Cap wurde bisher als
*Klassifikator für Korruption* verwendet: `|implied return| > 20 %` hieß
„phantom, ab in die Quarantäne". Die Messung vom 18.08. hat gezeigt, dass er das
nicht leisten kann — er fing über den gesamten Audit-Stream hinweg **null**
Artefakte und **drei echte Trades** (CYS, SLX, VELVET; Register §5c). Jede
tatsächliche Artefakt-Klasse wird inzwischen von einer *exakten Signatur*
erfasst.

Eine Größenordnung ist eben kein Korruptionsmerkmal. Ein Micro-Cap, der über
Nacht 38 % läuft, sieht genauso aus wie ein Feed-Fehler — unterscheiden lässt sich
das nur an der Evidenz, nicht an der Zahl.

Der Cap bleibt trotzdem, weil er die einzige Verteidigung gegen *noch unbekannte*
Klassen ist. Aber er **löst eine Prüfung aus, statt ein Urteil zu fällen**:

    exakte Signatur?  ── ja ──►  QUARANTINE
            │ nein
            ▼
    belegt echt?      ── ja ──►  VERIFIED_MARKET_PLAUSIBLE
            │ nein
            ▼
    |return| > Cap?   ── nein ─►  CLEAN
            │ ja
            ▼
              REQUIRES_VERIFICATION

``REQUIRES_VERIFICATION`` ist ausdrücklich **kein** Freispruch und **kein**
Schuldspruch — es ist offene Prüf-Schuld. Solange der automatische Close-Verifier
fehlt, behandeln die Lese-Aggregatoren diesen Zustand weiterhin wie Quarantäne
(``corruption_reason`` gibt einen Grund zurück), damit sich die Buch-Zahlen nicht
schlagartig verschieben. Neu ist, dass er ein **eigenes Label** trägt: damit ist
jederzeit messbar, wie groß die Prüf-Schuld ist, statt sie als „Artefakt"
mitzuzählen.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Label des Trigger-Zustands. Bewusst NICHT "phantom_..." — der Cap behauptet
# nichts mehr über die Ursache, er verlangt eine Prüfung.
EXTREME_MOVE_REASON = "extreme_move_requires_verification"


class CloseVerdict(StrEnum):
    """Zustand eines Closes im Urteilsprozess."""

    CLEAN = "clean"
    """Unauffällig — weder Signatur noch Trigger."""

    QUARANTINE = "quarantine"
    """Bekanntes Artefakt (exakte Signatur) oder gescheiterte Verifikation."""

    VERIFIED_MARKET_PLAUSIBLE = "verified_market_plausible"
    """Geprüft und belegt: der Preis existierte real am Markt."""

    REQUIRES_VERIFICATION = "requires_verification"
    """Der Cap hat ausgelöst. Offene Prüf-Schuld, kein Urteil."""


@dataclass(frozen=True)
class CloseClassification:
    """Das Urteil samt Begründung."""

    verdict: CloseVerdict
    reason: str
    """Maschinenlesbares Label; leer bei CLEAN."""

    detail: str = ""

    @property
    def is_quarantined(self) -> bool:
        return self.verdict is CloseVerdict.QUARANTINE

    @property
    def needs_verification(self) -> bool:
        return self.verdict is CloseVerdict.REQUIRES_VERIFICATION


_CLEAN = CloseClassification(verdict=CloseVerdict.CLEAN, reason="")


def classify_close(close_row: dict[str, object]) -> CloseClassification:
    """Urteil über eine ``position_closed``/``position_partial_closed``-Zeile.

    Die Reihenfolge ist bindend: eine exakte Signatur schlägt alles, ein
    Freispruch schlägt nur den Cap, und der Cap selbst urteilt nicht mehr.
    """
    # Lazy imports: bricht den Zyklus app.execution ↔ app.learning (siehe
    # Modul-Kommentar in bayes_quarantine).
    from app.learning.bayes_quarantine import quarantine_reason
    from app.learning.verified_real_closes import verified_real_close

    signature = quarantine_reason(close_row)
    if signature is not None:
        return CloseClassification(
            verdict=CloseVerdict.QUARANTINE,
            reason=signature,
            detail="exakte forensische Signatur",
        )

    # Remediation-gestempelte Flat-Closes: entry == exit, also nie ein Cap-Treffer.
    if close_row.get("reason") == "quarantine_off_venue_unpriceable":
        return CloseClassification(
            verdict=CloseVerdict.QUARANTINE,
            reason="quarantine_off_venue_unpriceable",
            detail="Remediations-Stempel: Position war auf der kanonischen Venue nicht bepreisbar",
        )

    record = verified_real_close(close_row)
    if record is not None:
        return CloseClassification(
            verdict=CloseVerdict.VERIFIED_MARKET_PLAUSIBLE,
            reason="verified_market_plausible",
            detail=record.evidence,
        )

    from app.execution.phantom_filter import implied_close_return, is_phantom_close

    if is_phantom_close(
        close_row.get("entry_price"),
        close_row.get("exit_price"),
        close_row.get("position_side"),
    ):
        entry = close_row.get("entry_price")
        exit_ = close_row.get("exit_price")
        ret = (
            implied_close_return(
                float(entry),
                float(exit_),
                str(close_row.get("position_side") or "long"),
            )
            if isinstance(entry, (int, float)) and isinstance(exit_, (int, float))
            else None
        )
        return CloseClassification(
            verdict=CloseVerdict.REQUIRES_VERIFICATION,
            reason=EXTREME_MOVE_REASON,
            detail=(
                f"implied return {ret:+.2%} ueber der Kappe — Evidenz noch nicht geprueft"
                if ret is not None
                else "implied return ueber der Kappe — Evidenz noch nicht geprueft"
            ),
        )

    return _CLEAN


__all__ = [
    "EXTREME_MOVE_REASON",
    "CloseClassification",
    "CloseVerdict",
    "classify_close",
]
