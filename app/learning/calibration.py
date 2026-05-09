"""Probabilistic Calibration — Brier, Log-Loss, ECE, Reliability-Diagramm.

Pflicht aus dem KAI-Leitsatz "keine Vorhersage, sondern kalibrierte
Wahrscheinlichkeiten": jede Engine, die Wahrscheinlichkeiten liefert
(Bayes-Confidence, Regime-Posterior, Sentiment-Score), muss aus realen
Outcomes lernen — sonst ist die "Wahrscheinlichkeit" Theater.

Metriken
--------

  Brier-Score:    BS = (1/N) · Σ (p_i − y_i)²        ∈ [0, 1] — niedriger besser
  Log-Loss:       LL = −(1/N) · Σ [y·log(p) + (1−y)·log(1−p)]
  ECE (Expected Calibration Error):
                  ECE = Σ_b (n_b/N) · |mean_p_b − mean_y_b|
  Reliability:    pro Bin (mean_predicted, mean_observed, n)

Reliability-Diagramm: Bins über [0,1] mit gleicher Breite.  Eine perfekt
kalibrierte Engine liegt mit allen Bins auf der Diagonalen
(mean_predicted == mean_observed).  Overconfident = Bin-Punkte unter der
Diagonalen, underconfident = darüber.

Vertrag
-------
  - Pure Python, keine sklearn/numpy-Dep.
  - ``OutcomePair.actual_outcome`` ist striktes ``{0, 1}`` (kein Float).
  - ``predicted_probability`` ∈ [0, 1] — Werte werden in (ε, 1−ε)
    geclamped, damit ``log(0)`` nicht knallt.
  - Leerer Input liefert einen Report mit ``n=0`` + ``sample_sufficient=False``,
    keine Exception (Audit-Pfad darf nie blocken).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

EPS: Final[float] = 1e-9
DEFAULT_BIN_COUNT: Final[int] = 10
DEFAULT_MIN_SAMPLE_FOR_JUDGMENT: Final[int] = 30


class OutcomePair(BaseModel):
    """Eine einzelne (Vorhersage, Wirklichkeit)-Paarung."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    predicted_probability: float = Field(ge=0.0, le=1.0)
    actual_outcome: int = Field(ge=0, le=1)
    timestamp_utc: datetime | None = None
    weight: float = Field(default=1.0, ge=0.0)

    @field_validator("actual_outcome")
    @classmethod
    def _strict_binary(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("actual_outcome must be 0 or 1")
        return v


class CalibrationBin(BaseModel):
    """Ein Bin im Reliability-Diagramm."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lower: float
    upper: float
    n: int
    mean_predicted: float
    mean_observed: float
    weight: float


class CalibrationReport(BaseModel):
    """Vollständiger Calibration-Report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_pairs: int
    total_weight: float
    brier_score: float | None  # None bei n_pairs == 0
    log_loss: float | None
    expected_calibration_error: float | None  # ECE
    mean_predicted: float | None
    mean_observed: float | None
    bins: tuple[CalibrationBin, ...]
    sample_sufficient: bool
    notes: tuple[str, ...]


# ─── Kern-Berechnung ──────────────────────────────────────────────────────────


def _clamp_prob(p: float) -> float:
    return max(EPS, min(1.0 - EPS, p))


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total_w = sum(weights)
    if total_w <= 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total_w


def _brier(pairs: Sequence[OutcomePair]) -> float:
    weights = [p.weight for p in pairs]
    sq_err = [(p.predicted_probability - p.actual_outcome) ** 2 for p in pairs]
    return _weighted_mean(sq_err, weights)


def _log_loss(pairs: Sequence[OutcomePair]) -> float:
    """Binary cross-entropy in nats, gewichtet."""
    losses: list[float] = []
    weights: list[float] = []
    for pair in pairs:
        p = _clamp_prob(pair.predicted_probability)
        y = pair.actual_outcome
        ll = -(y * math.log(p) + (1 - y) * math.log(1 - p))
        losses.append(ll)
        weights.append(pair.weight)
    return _weighted_mean(losses, weights)


def _build_bins(
    pairs: Sequence[OutcomePair], *, n_bins: int
) -> tuple[list[CalibrationBin], float]:
    """Bin pairs über [0,1] in n_bins gleicher Breite + ECE."""
    if n_bins <= 0:
        raise ValueError("n_bins must be positive")
    width = 1.0 / n_bins
    buckets: list[list[OutcomePair]] = [[] for _ in range(n_bins)]
    for pair in pairs:
        # 1.0 fällt sonst in Bin n_bins → clamp auf letzten Bin
        idx = min(int(pair.predicted_probability / width), n_bins - 1)
        buckets[idx].append(pair)

    out_bins: list[CalibrationBin] = []
    total_weight = sum(p.weight for p in pairs) or 1.0
    ece = 0.0
    for i, bucket in enumerate(buckets):
        lower = i * width
        upper = (i + 1) * width
        if not bucket:
            out_bins.append(
                CalibrationBin(
                    lower=lower,
                    upper=upper,
                    n=0,
                    mean_predicted=0.0,
                    mean_observed=0.0,
                    weight=0.0,
                )
            )
            continue
        weights = [p.weight for p in bucket]
        mean_p = _weighted_mean([p.predicted_probability for p in bucket], weights)
        mean_y = _weighted_mean([float(p.actual_outcome) for p in bucket], weights)
        bin_weight = sum(weights)
        out_bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                n=len(bucket),
                mean_predicted=mean_p,
                mean_observed=mean_y,
                weight=bin_weight,
            )
        )
        ece += (bin_weight / total_weight) * abs(mean_p - mean_y)
    return out_bins, ece


def compute_calibration(
    pairs: Sequence[OutcomePair],
    *,
    n_bins: int = DEFAULT_BIN_COUNT,
    min_sample_for_judgment: int = DEFAULT_MIN_SAMPLE_FOR_JUDGMENT,
) -> CalibrationReport:
    """Aggregiere alle Calibration-Metriken aus einer Liste von Outcome-Paaren.

    Liefert einen Report mit ``n_pairs == 0``, wenn keine Paare gegeben sind
    (statt Exception) — Audit-/Dashboard-Pfad bleibt grün.
    """
    notes: list[str] = []
    if not pairs:
        return CalibrationReport(
            n_pairs=0,
            total_weight=0.0,
            brier_score=None,
            log_loss=None,
            expected_calibration_error=None,
            mean_predicted=None,
            mean_observed=None,
            bins=(),
            sample_sufficient=False,
            notes=("Keine Outcome-Paare vorhanden — keine Calibration berechenbar.",),
        )

    total_weight = sum(p.weight for p in pairs)
    weights = [p.weight for p in pairs]
    mean_pred = _weighted_mean([p.predicted_probability for p in pairs], weights)
    mean_obs = _weighted_mean([float(p.actual_outcome) for p in pairs], weights)
    brier = _brier(pairs)
    log_loss = _log_loss(pairs)
    bins, ece = _build_bins(pairs, n_bins=n_bins)

    sample_sufficient = len(pairs) >= min_sample_for_judgment
    if not sample_sufficient:
        notes.append(
            f"Stichprobe {len(pairs)} < {min_sample_for_judgment} — "
            "Metriken statistisch nicht belastbar."
        )

    overall_drift = mean_pred - mean_obs
    if abs(overall_drift) > 0.10 and sample_sufficient:
        direction = "overconfident" if overall_drift > 0 else "underconfident"
        notes.append(
            f"Globaler Drift: mean_predicted={mean_pred:.3f} vs. "
            f"mean_observed={mean_obs:.3f} → {direction} um {overall_drift:+.3f}."
        )

    if ece > 0.10 and sample_sufficient:
        notes.append(
            f"ECE={ece:.3f} > 0.10 — Re-Calibration der Profile/Priors empfohlen."
        )

    return CalibrationReport(
        n_pairs=len(pairs),
        total_weight=round(total_weight, 6),
        brier_score=round(brier, 6),
        log_loss=round(log_loss, 6),
        expected_calibration_error=round(ece, 6),
        mean_predicted=round(mean_pred, 6),
        mean_observed=round(mean_obs, 6),
        bins=tuple(bins),
        sample_sufficient=sample_sufficient,
        notes=tuple(notes),
    )


__all__ = [
    "DEFAULT_BIN_COUNT",
    "DEFAULT_MIN_SAMPLE_FOR_JUDGMENT",
    "CalibrationBin",
    "CalibrationReport",
    "OutcomePair",
    "compute_calibration",
]
