"""Audit-Loader für ThresholdObservation — produziert Optimizer-Input aus Real-Audits.

Step 4 (production wiring of ThresholdOptimizer): die existierende Optimizer-Pipeline
(siehe ``app/learning/threshold_optimizer.py``) erwartet ``ThresholdObservation(
observation_id, score, realized_pnl_usd)``-Listen. Bisher wurde der Loader nur in
synthetischen Tests aufgerufen.

Dieses Modul produziert die Liste aus den drei produktiven JSONL-Audits:

    bayes_confidence_audit.jsonl   →  score (per decision_id)
    trading_loop_audit.jsonl        →  decision_id ↔ order_id
    paper_execution_audit.jsonl     →  trade_pnl_usd (close-Events)

Score-Quelle
------------

Konfigurierbar über ``score_field`` (default: ``"confidence_score"``).
Erlaubte Felder pro Bayes-Report:

  • ``confidence_score`` — *empfohlen*, normalisiert auf [0,1], direkter
    Vergleich zum ``signal.thresholds.min_bayes_confidence``-Gate.
  • ``posterior_probability`` — die Roh-Posterior; nützlich für direkte
    P&L-vs-Wahrscheinlichkeit-Analyse.

Andere Floats aus dem report-dict sind theoretisch möglich, werden aber
nicht garantiert.

PnL-Quelle
----------

Wiederverwendet die Join-Logik aus ``outcome_linking._build_order_to_decision``
+ ``_build_pnl_per_order``, aber emittiert ``float`` statt ``0|1``.

Selektions-Filter
-----------------

Nur Entscheidungen mit (a) Bayes-Audit-Eintrag UND (b) realisiertem Close-Event
landen in der Liste. Offene Positionen / nicht-gefillte Decisions werden
übersprungen (no bias).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from app.learning.outcome_linking import _build_order_to_decision, _build_pnl_per_order
from app.learning.threshold_optimizer import ThresholdObservation
from app.signals.bayes_journal import DEFAULT_BAYES_AUDIT_PATH, load_bayes_reports

logger = logging.getLogger(__name__)

DEFAULT_SCORE_FIELD: Final[str] = "confidence_score"
ALLOWED_SCORE_FIELDS: Final[frozenset[str]] = frozenset(
    {"confidence_score", "posterior_probability"}
)


def observations_from_audit(
    *,
    loop_audit_path: Path | str,
    exec_audit_path: Path | str,
    bayes_audit_path: Path | str = DEFAULT_BAYES_AUDIT_PATH,
    score_field: str = DEFAULT_SCORE_FIELD,
) -> list[ThresholdObservation]:
    """Lade alle realisierten Trades als ``ThresholdObservation``-Liste.

    Reihenfolge im Ergebnis entspricht der Bayes-Audit-Reihenfolge.

    Bei ungültigem ``score_field`` → ValueError (defensiv, weil falscher
    Field-Name still zu leeren Ergebnissen führen würde).
    """
    if score_field not in ALLOWED_SCORE_FIELDS:
        raise ValueError(
            f"score_field must be one of {sorted(ALLOWED_SCORE_FIELDS)}, "
            f"got {score_field!r}"
        )

    order_to_decision = _build_order_to_decision(Path(loop_audit_path))
    pnl_per_order = _build_pnl_per_order(Path(exec_audit_path))

    # decision_id → realisierte cumulative PnL (nur geschlossene Orders).
    decision_to_pnl: dict[str, float] = {}
    for order_id, pnl in pnl_per_order.items():
        decision_id = order_to_decision.get(order_id)
        if decision_id is None:
            continue
        # Falls eine decision_id mehrere orders generiert (sollte nicht), addieren.
        decision_to_pnl[decision_id] = decision_to_pnl.get(decision_id, 0.0) + pnl

    entries = load_bayes_reports(bayes_audit_path)
    observations: list[ThresholdObservation] = []
    skipped_no_outcome = 0
    skipped_bad_score = 0

    for entry in entries:
        pnl = decision_to_pnl.get(entry.decision_id)
        if pnl is None:
            skipped_no_outcome += 1
            continue
        raw_score = entry.report.get(score_field)
        if not isinstance(raw_score, (int, float)):
            skipped_bad_score += 1
            continue
        observations.append(
            ThresholdObservation(
                observation_id=entry.decision_id,
                score=float(raw_score),
                realized_pnl_usd=float(pnl),
            )
        )

    if skipped_no_outcome:
        logger.info(
            "[threshold-obs] %d bayes-entries ohne realisierte Outcome übersprungen",
            skipped_no_outcome,
        )
    if skipped_bad_score:
        logger.warning(
            "[threshold-obs] %d bayes-entries mit fehlendem/falschem %s übersprungen",
            skipped_bad_score,
            score_field,
        )

    return observations


__all__ = [
    "ALLOWED_SCORE_FIELDS",
    "DEFAULT_SCORE_FIELD",
    "observations_from_audit",
]
