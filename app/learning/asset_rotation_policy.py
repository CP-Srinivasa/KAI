"""Asset-rotation policy (pure logic, no I/O).

G1 of the Momentum-Universe goal. Decides, per asset, the target lifecycle
status from its single-shot performance verdict (``asset_performance_score``)
plus a persisted hysteresis counter. The apply-step wraps DB/audit/state around
this; here is only the deterministic decision.

Mittelweg + churn-safety (the project's churn doctrine showed cutting too eagerly
bleeds fees):

* **pinned** is never auto-rotated.
* **promote** — a CANDIDATE/PROBATION/ROTATION_FLAGGED asset with a *healthy*
  verdict (at least one arm positive) → ``ACTIVE``; resets the counter.
* **sustained weak** — only a *weak* verdict (BOTH arms fail: net PnL <= 0 AND
  Wilson hit-rate below floor) increments the counter. An ACTIVE/PROBATION asset
  is flagged (``ROTATION_FLAGGED``) only after ``flag_after_runs`` consecutive
  weak windows; an already-flagged asset is archived only after
  ``archive_after_runs``. A single bad window never rotates anything.
* **insufficient** — not enough closes (min-hold): hold, counter unchanged.

Reversible + audited: every decision carries a ``reason``; the FSM legality is
checked separately by the caller (``asset_lifecycle.can_transition``).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.learning.asset_lifecycle import AssetStatus
from app.learning.asset_performance_score import AssetVerdict

FLAG_AFTER_RUNS = 2
ARCHIVE_AFTER_RUNS = 3


@dataclass(frozen=True)
class AssetRotationDecision:
    """Result for one asset. ``target`` is ``None`` when the status is unchanged;
    ``flagged_runs`` is the carried-forward consecutive-weak counter (0 = reset)."""

    target: AssetStatus | None
    reason: str
    flagged_runs: int


def decide_asset_rotation(
    current: AssetStatus,
    verdict: AssetVerdict,
    *,
    pinned: bool,
    prior_flagged_runs: int,
    flag_after_runs: int = FLAG_AFTER_RUNS,
    archive_after_runs: int = ARCHIVE_AFTER_RUNS,
    has_new_evidence: bool = True,
) -> AssetRotationDecision:
    """Decide the target lifecycle status for one asset (pure, deterministic).

    ``has_new_evidence`` (B3, Plan 08-08): Der Shadow-Lauf rechnet pro Tick
    über DIESELBEN letzten N Closes — ohne neue Trades kletterte der
    weak-Zähler pro Kalendertag und archivierte Symbole nach 3 ruhigen Tagen
    auf Basis von null neuer Evidenz. Ohne neue Evidenz friert der Zähler ein;
    Default ``True`` erhält das Verhalten aller Alt-Aufrufer.
    """
    if pinned or current == AssetStatus.PINNED:
        return AssetRotationDecision(None, "protected_pinned", 0)

    # Promotion / recovery on a healthy verdict (resets the weak counter).
    if verdict.healthy:
        if current in (
            AssetStatus.CANDIDATE,
            AssetStatus.PROBATION,
            AssetStatus.ROTATION_FLAGGED,
        ):
            return AssetRotationDecision(AssetStatus.ACTIVE, "promote_healthy", 0)
        # B3b: archived war eine Einbahnstraße — ein wieder gesundes Symbol
        # bekommt den FSM-legalen Rückweg ARCHIVED→CANDIDATE (Re-Evaluation),
        # NICHT direkt ACTIVE (erst wieder beweisen).
        if current == AssetStatus.ARCHIVED:
            return AssetRotationDecision(AssetStatus.CANDIDATE, "rearm_healthy", 0)
        return AssetRotationDecision(None, "healthy", 0)

    # Sustained weakness (both arms fail) with hysteresis.
    if verdict.weak:
        if not has_new_evidence:
            # Kein neuer Close seit dem letzten Lauf: dasselbe Fenster darf den
            # Zähler nicht erneut belasten (Evidenz zählt, nicht Kalender).
            return AssetRotationDecision(
                None, "weak_no_new_evidence_hold", max(0, prior_flagged_runs)
            )
        runs = max(0, prior_flagged_runs) + 1
        if current == AssetStatus.ROTATION_FLAGGED and runs >= archive_after_runs:
            return AssetRotationDecision(
                AssetStatus.ARCHIVED, "rotate_archive_sustained_weak", runs
            )
        if current in (AssetStatus.ACTIVE, AssetStatus.PROBATION) and runs >= flag_after_runs:
            return AssetRotationDecision(AssetStatus.ROTATION_FLAGGED, "flag_sustained_weak", runs)
        return AssetRotationDecision(None, f"weak_{runs}/{flag_after_runs}", runs)

    # Insufficient / borderline (neither healthy nor weak): hold, counter unchanged.
    return AssetRotationDecision(None, "insufficient_hold", max(0, prior_flagged_runs))
