"""Loader: verbindet Bayes-Audit-Stream + Outcome-Map zu OutcomePair-Stream.

Bewusst orthogonal: dieses Modul *liest* aus dem Bayes-Audit, *kennt* aber
keine konkrete Outcome-Quelle.  Caller liefert ein ``Mapping[decision_id,
0|1]``.  So bleibt der Calibration-Pfad unabhängig von der Frage, ob
Outcome-Linking aus dem Paper-Audit, aus dem Decision-Journal oder aus
einem späteren D-XYZ Outcome-Service kommt.

Outcome-Konvention:
  - 1 = Trade hat sich in Signal-Richtung bewegt (Win-Realisierung).
  - 0 = nicht in Signal-Richtung (Loss-Realisierung oder Stop).
  - Fehlende ``decision_id`` in der Map → Pair wird übersprungen.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from app.learning.calibration import OutcomePair
from app.signals.bayes_journal import DEFAULT_BAYES_AUDIT_PATH, load_bayes_reports


def pairs_from_bayes_audit(
    *,
    bayes_audit_path: Path | str = DEFAULT_BAYES_AUDIT_PATH,
    outcomes: Mapping[str, int],
    side_aware: bool = True,
) -> list[OutcomePair]:
    """Lade alle Bayes-Audit-Zeilen + matche gegen Outcome-Map.

    ``side_aware`` (Default True): Posterior wird so gespiegelt, dass die
    "win_probability" *für die Signal-Richtung* gerechnet wird.  Bei
    ``direction == "long"`` ist das die Posterior selbst, bei
    ``direction == "short"`` ist es ``1 − posterior`` — Calibration zählt
    immer "wie oft hat das Signal Recht behalten", unabhängig von long/short.
    """
    entries = load_bayes_reports(bayes_audit_path)
    pairs: list[OutcomePair] = []
    for e in entries:
        actual = outcomes.get(e.decision_id)
        if actual is None:
            continue
        if actual not in (0, 1):
            continue
        posterior = e.report.get("posterior_probability")
        if not isinstance(posterior, (int, float)):
            continue
        p = float(posterior)
        if side_aware and e.direction == "short":
            p = 1.0 - p
        # Sicherheits-Clamp gegen Float-Drift in der JSONL.
        p = max(0.0, min(1.0, p))
        pairs.append(
            OutcomePair(
                decision_id=e.decision_id,
                predicted_probability=p,
                actual_outcome=int(actual),
                timestamp_utc=None,
                weight=1.0,
            )
        )
    return pairs


__all__ = ["pairs_from_bayes_audit"]
