"""Probabilistic Re-Calibration — lineare Korrektur im Wahrscheinlichkeitsraum.

Wenn die Calibration-Diagnose anzeigt, dass die Engine systematisch
overconfident (oder underconfident) ist, kann der Operator die rohen
Posteriors nachträglich korrigieren — ohne die Bayes-Profile selbst
anzufassen (das wäre eine destruktive Aktion mit Audit-Last).

Modell
------

  p_corr = clamp( α + β · p_raw ,  ε, 1−ε )

  α (intercept), β (slope) werden via gewichteter Least-Squares-Regression
  aus den (p_raw, y)-Paaren gefittet — pure Python, keine numpy-Dep.

  E[y | p_raw] ist die empirische Hit-Rate gegeben die Engine-Wahrscheinlichkeit;
  die OLS-Lösung ist die optimale lineare Approximation davon.

Ergebnis
--------

  - β ≈ 1, α ≈ 0 ⇒ Engine ist bereits gut kalibriert (Identity-Mapping).
  - β < 1, α > 0 ⇒ Engine ist overconfident (extreme Werte werden zur
    Mitte komprimiert).
  - α ≠ 0 mit β ≈ 1 ⇒ globaler Bias.

Vertrag
-------
  - Mindestens ``min_pairs`` (Default 30) Paare nötig — sonst wird
    ``IdentityCalibrator`` zurückgegeben (kein Schaden).
  - Outputs werden in (ε, 1−ε) geclamped — log(0)/log(1) ausgeschlossen.
  - Pure Python.  Bewusst KEINE iterative Maximum-Likelihood-Optimierung
    (Newton/L-BFGS) — die lineare Approximation reicht für die typische
    Drift, ist deterministisch und audit-bar.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from app.learning.calibration import OutcomePair, compute_calibration

EPS: Final[float] = 1e-9
DEFAULT_MIN_PAIRS_FOR_FIT: Final[int] = 30


class CalibratorParameters(BaseModel):
    """Slope + Intercept eines Logit-Linear-Calibrators."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intercept: float
    slope: float
    n_fitted: int
    is_identity: bool
    fit_notes: tuple[str, ...]


class IdentityCalibrator:
    """Fallback-Calibrator: gibt Eingabe unverändert zurück."""

    parameters: CalibratorParameters = CalibratorParameters(
        intercept=0.0,
        slope=1.0,
        n_fitted=0,
        is_identity=True,
        fit_notes=("Identity-Calibrator (keine Korrektur).",),
    )

    def transform(self, p: float) -> float:
        return _clamp_unit(p)


class LogitLinearCalibrator:
    """Lineare Korrektur im Wahrscheinlichkeitsraum.

    (Klassennamen aus Kompatibilitätsgründen behalten — der Calibrator
    arbeitet jetzt direkt im [0,1]-Raum, nicht im Logit-Raum.)
    """

    def __init__(self, parameters: CalibratorParameters) -> None:
        self.parameters = parameters

    def transform(self, p: float) -> float:
        if self.parameters.is_identity:
            return _clamp_unit(p)
        corrected = self.parameters.intercept + self.parameters.slope * _clamp_unit(p)
        return _clamp_unit(corrected)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _clamp_unit(p: float) -> float:
    return max(EPS, min(1.0 - EPS, p))


def _logit(p: float) -> float:
    p = _clamp_unit(p)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _weighted_lstsq(
    xs: Sequence[float], ys: Sequence[float], weights: Sequence[float]
) -> tuple[float, float]:
    """Gewichtete OLS: y = α + β·x.  Returns (intercept, slope)."""
    total_w = sum(weights)
    if total_w <= 0:
        return 0.0, 1.0
    mean_x = sum(w * x for w, x in zip(weights, xs, strict=True)) / total_w
    mean_y = sum(w * y for w, y in zip(weights, ys, strict=True)) / total_w
    cov_xy = sum(
        w * (x - mean_x) * (y - mean_y)
        for w, x, y in zip(weights, xs, ys, strict=True)
    )
    var_x = sum(w * (x - mean_x) * (x - mean_x) for w, x in zip(weights, xs, strict=True))
    if var_x <= EPS:
        return 0.0, 1.0
    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x
    return intercept, slope


# ─── Fit ──────────────────────────────────────────────────────────────────────


def fit_calibrator(
    pairs: Sequence[OutcomePair],
    *,
    min_pairs: int = DEFAULT_MIN_PAIRS_FOR_FIT,
) -> LogitLinearCalibrator | IdentityCalibrator:
    """Fitte Logit-lineare Korrektur aus Outcome-Paaren.

    Methode: gewichtete OLS auf (logit(p_raw), y).  Das ist eine
    Platt-Scaling-Approximation 1. Ordnung — nicht so genau wie iterative
    ML-Optimierung, aber deterministisch + transparent.  Für typische
    Engine-Drifts (intercept ~ ±0.5, slope ~ 0.5..1.5) reicht das.

    Wenn pairs < min_pairs oder degenerate (alle p gleich, oder alle y
    gleich) ⇒ ``IdentityCalibrator``, damit die Pipeline keinen Schaden
    nimmt.
    """
    if len(pairs) < min_pairs:
        return IdentityCalibrator()

    xs = [_clamp_unit(p.predicted_probability) for p in pairs]
    ys_raw = [float(p.actual_outcome) for p in pairs]
    ws = [p.weight for p in pairs]

    if len({round(x, 6) for x in xs}) < 2:
        return IdentityCalibrator()
    if len(set(ys_raw)) < 2:
        return IdentityCalibrator()

    intercept, slope = _weighted_lstsq(xs, ys_raw, ws)
    is_identity = abs(intercept) < 1e-6 and abs(slope - 1.0) < 1e-6

    notes: list[str] = []
    if slope < 0.5:
        notes.append(f"Steile Overconfidence-Korrektur (slope={slope:.3f}).")
    elif slope > 1.5:
        notes.append(f"Underconfidence-Korrektur (slope={slope:.3f}).")
    if abs(intercept) > 0.5:
        direction = "downwards" if intercept < 0 else "upwards"
        notes.append(f"Globaler Bias ({direction}, intercept={intercept:.3f}).")
    if not notes:
        notes.append("Drift im akzeptablen Bereich.")

    parameters = CalibratorParameters(
        intercept=round(intercept, 6),
        slope=round(slope, 6),
        n_fitted=len(pairs),
        is_identity=is_identity,
        fit_notes=tuple(notes),
    )
    return LogitLinearCalibrator(parameters)


def fit_and_score(
    pairs: Sequence[OutcomePair],
    *,
    min_pairs: int = DEFAULT_MIN_PAIRS_FOR_FIT,
) -> dict[str, object]:
    """Convenience: Fit + Vorher/Nachher-Brier zur Validierung."""
    if not pairs:
        return {
            "calibrator": IdentityCalibrator().parameters.model_dump(),
            "brier_before": None,
            "brier_after": None,
            "improvement": None,
        }
    cal = fit_calibrator(pairs, min_pairs=min_pairs)
    before = compute_calibration(pairs)
    corrected_pairs = [
        OutcomePair(
            decision_id=p.decision_id,
            predicted_probability=cal.transform(p.predicted_probability),
            actual_outcome=p.actual_outcome,
            weight=p.weight,
            timestamp_utc=p.timestamp_utc,
        )
        for p in pairs
    ]
    after = compute_calibration(corrected_pairs)
    improvement = None
    if before.brier_score is not None and after.brier_score is not None:
        improvement = round(before.brier_score - after.brier_score, 6)
    return {
        "calibrator": cal.parameters.model_dump(),
        "brier_before": before.brier_score,
        "brier_after": after.brier_score,
        "ece_before": before.expected_calibration_error,
        "ece_after": after.expected_calibration_error,
        "improvement": improvement,
    }


__all__ = [
    "CalibratorParameters",
    "IdentityCalibrator",
    "LogitLinearCalibrator",
    "fit_and_score",
    "fit_calibrator",
]
