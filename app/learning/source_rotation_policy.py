"""Autonome Quellen-Rotations-Policy (pure Logik, kein I/O).

Entscheidet pro Quelle, ob ihr DB-Lifecycle-Status geändert werden soll, auf
Basis des vom ``source_lifecycle_recalc`` geschriebenen Rankings. Der Apply-Step
(``scripts/source_lifecycle_apply.py``) wickelt DB + Audit + State-Persistenz um
diese Funktion herum; hier steht nur die Entscheidung — testbar, deterministisch.

Leitlinien (bewusst konservativ, damit Input-Breite nicht vor einem Ersatz
verloren geht — KAI Directive §3):

* **silent** (auto-detektiert, keine Signale mehr) → ``SILENT``: gefahrlos, die
  Quelle liefert ohnehin nichts; kein Input-Verlust.
* **sustained low** — NUR der echte ``low``-Tier (Wilson-Untergrenze < 30 %)
  über ``disable_after_runs`` aufeinanderfolgende Läufe → ``DISABLED``. Der
  breitere ``rotation_flagged``-Pool (inkl. ``watch``, z. B. eine 60-%-Quelle
  auf Rang 2) wird **nie** allein deshalb stillgelegt — ``watch`` bleibt aktiv.
* **recovery** — eine zuvor auto-stillgelegte (``SILENT``) Quelle, die wieder
  liefert und nicht ``low`` ist → ``ACTIVE``. ``DISABLED`` bleibt (kann manuell
  vom Operator gesetzt sein) → nur über expliziten Re-Enable/Probation zurück.
* **pinned** wird nie automatisch rotiert.

Reversibel + auditiert: jede Entscheidung trägt einen ``reason``; der Apply-Step
schreibt jeden tatsächlichen Wechsel in den Lifecycle-Audit-Trail.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import SourceStatus

# Aufeinanderfolgende low-Tier-Läufe, bevor eine aktive Quelle auto-disabled wird.
# Ein einzelnes schlechtes Fenster legt nichts still; erst anhaltende Schwäche.
DISABLE_AFTER_RUNS = 3

_LOW_TIER = "low"


@dataclass(frozen=True)
class RotationDecision:
    """Ergebnis der Rotations-Entscheidung für eine Quelle.

    ``target`` ist ``None``, wenn der Status unverändert bleibt. ``flagged_runs``
    ist der fortgeschriebene Zähler aufeinanderfolgender low-Läufe, den der
    Apply-Step persistiert (0 = zurückgesetzt).
    """

    target: SourceStatus | None
    reason: str
    flagged_runs: int


def decide_rotation(
    current: SourceStatus,
    *,
    silent: bool,
    reliability_tier: str,
    pinned: bool,
    prior_flagged_runs: int,
    disable_after_runs: int = DISABLE_AFTER_RUNS,
) -> RotationDecision:
    """Entscheide den Ziel-Lifecycle-Status einer Quelle (pure).

    Args:
        current: aktueller DB-Status der Quelle (Source of Truth).
        silent: Ranking-Flag — seit dem Stille-Fenster kein Signal mehr.
        reliability_tier: ``trusted|neutral|watch|low|insufficient`` aus dem Ranking.
        pinned: Ranking-Flag — bewährter Top-Performer, nie auto-rotieren.
        prior_flagged_runs: bisheriger Zähler aufeinanderfolgender low-Läufe.
        disable_after_runs: Schwelle, ab der sustained-low zu ``DISABLED`` führt.

    Returns:
        ``RotationDecision`` mit Ziel-Status (oder ``None``), Begründung und
        fortgeschriebenem Zähler. Der Aufrufer prüft die FSM-Legalität separat.
    """
    # Geschützt: pinned wird nie automatisch rotiert.
    if pinned or current == SourceStatus.PINNED:
        return RotationDecision(None, "protected_pinned", 0)

    is_low = reliability_tier == _LOW_TIER

    # 1) Stille: aktive Quelle liefert nicht mehr → SILENT (kein Input-Verlust).
    if current == SourceStatus.ACTIVE and silent:
        return RotationDecision(SourceStatus.SILENT, "auto_silence", 0)

    # 2) Anhaltend low: NUR echter low-Tier (nie bloß 'watch'), über N Läufe.
    if current == SourceStatus.ACTIVE and is_low and not silent:
        runs = prior_flagged_runs + 1
        if runs >= disable_after_runs:
            return RotationDecision(
                SourceStatus.DISABLED, "auto_rotate_disable_sustained_low", runs
            )
        return RotationDecision(None, f"flagged_low_{runs}/{disable_after_runs}", runs)

    # 3) Recovery: auto-stillgelegte Quelle liefert wieder & ist nicht low → ACTIVE.
    #    DISABLED bleibt absichtlich aussen vor (kann Operator-gesetzt sein).
    if current == SourceStatus.SILENT and not silent and not is_low:
        return RotationDecision(SourceStatus.ACTIVE, "auto_recover", 0)

    # 4) Kein Wechsel; low-Zähler zurücksetzen.
    return RotationDecision(None, "no_change", 0)
