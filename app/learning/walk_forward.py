"""Walk-Forward-Validation für Calibrator-Vorschläge.

Out-of-Sample-Härtung: bevor ein neuer ``LogitLinearCalibrator`` als
ParameterVersion vorgeschlagen wird, prüfen wir, dass er auf *zukünftigen*
Daten (im chronologischen Sinne) tatsächlich besser ist als die rohe
Identity — und nicht nur in-sample.

Methodik
--------

Anchored expanding window: Train-Set wächst, Test-Bucket wandert nach
hinten.  Standard im Finance-Kontext (Lookahead-frei, näher an realer
Inferenz als k-fold).

  Pairs (chronologisch):  [─────────────────── n ────────────────────]
                                              ↑ train_min_idx

  Fold 1:  train [0 .. train_min_idx)              test [train_min_idx     .. +Δ)
  Fold 2:  train [0 .. train_min_idx + Δ)          test [train_min_idx + Δ .. +2Δ)
  Fold 3:  train [0 .. train_min_idx + 2Δ)         test [train_min_idx + 2Δ.. +3Δ)
  …

Δ (`bucket_size`) = restliche Bars / `n_splits`.

Decision
--------

  - ``insufficient_data``: < ``min_train_size + min_test_size`` Paare.
  - ``approve``: mittlere OoS-Brier-Verbesserung ≥ Threshold UND
                 ≥ ``min_consistency``-Anteil der Splits zeigt Verbesserung.
  - ``reject``: alles andere — explizit mit Begründung.

Voraussetzungen
---------------

- Eingabe-Liste gilt als bereits chronologisch (caller-Verantwortung).
  Falls ``timestamp_utc`` gesetzt ist, sortiert der Validator danach;
  sonst respektiert er die Eingabe-Reihenfolge.
- Pure Python, keine sklearn/numpy-Dep — konsistent mit dem Rest von
  ``app/learning/``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.learning.calibration import OutcomePair, compute_calibration
from app.learning.recalibration import (
    DEFAULT_MIN_PAIRS_FOR_FIT,
    IdentityCalibrator,
    LogitLinearCalibrator,
    fit_calibrator,
)

DEFAULT_N_SPLITS: Final[int] = 5
DEFAULT_TRAIN_FRACTION: Final[float] = 0.50
DEFAULT_MIN_TRAIN_SIZE: Final[int] = DEFAULT_MIN_PAIRS_FOR_FIT  # 30
DEFAULT_MIN_TEST_SIZE: Final[int] = 10
DEFAULT_MIN_BRIER_IMPROVEMENT: Final[float] = 0.005
DEFAULT_MIN_CONSISTENCY: Final[float] = 0.60

DecisionLiteral = Literal["approve", "reject", "insufficient_data"]


class WalkForwardConfig(BaseModel):
    """Schwellen für die Walk-Forward-Entscheidung. Alle Werte explizit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_splits: int = Field(default=DEFAULT_N_SPLITS, ge=2, le=20)
    train_fraction: float = Field(default=DEFAULT_TRAIN_FRACTION, gt=0.0, lt=1.0)
    min_train_size: int = Field(default=DEFAULT_MIN_TRAIN_SIZE, ge=10)
    min_test_size: int = Field(default=DEFAULT_MIN_TEST_SIZE, ge=5)
    min_brier_improvement: float = Field(default=DEFAULT_MIN_BRIER_IMPROVEMENT, ge=0.0, le=1.0)
    min_consistency: float = Field(default=DEFAULT_MIN_CONSISTENCY, ge=0.0, le=1.0)


class WalkForwardSplit(BaseModel):
    """Ergebnis eines einzelnen Folds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fold: int
    n_train: int
    n_test: int
    brier_test_before: float
    brier_test_after: float
    ece_test_before: float
    ece_test_after: float
    brier_improvement: float  # before - after; positiv = besser
    improved: bool
    calibrator_intercept: float
    calibrator_slope: float
    calibrator_is_identity: bool


class WalkForwardReport(BaseModel):
    """Vollständiger Validation-Report — Decision + Splits + Diagnostik."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_pairs: int
    n_splits_run: int
    splits: tuple[WalkForwardSplit, ...]
    mean_oos_brier_improvement: float
    median_oos_brier_improvement: float
    consistency_ratio: float
    decision: DecisionLiteral
    decision_reasons: tuple[str, ...]
    config: WalkForwardConfig


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _ordered_pairs(pairs: Sequence[OutcomePair]) -> list[OutcomePair]:
    """Stable chronological order:
    - Wenn alle Pairs ein ``timestamp_utc`` haben → sortiere danach.
    - Sonst respektiere Eingabe-Reihenfolge (caller-Verantwortung).
    """
    if not pairs:
        return []
    if all(p.timestamp_utc is not None for p in pairs):
        # mypy/pyright safety: cast already-non-None
        return sorted(pairs, key=lambda p: p.timestamp_utc)  # type: ignore[arg-type, return-value]
    return list(pairs)


def _apply_calibrator(
    pairs: Sequence[OutcomePair],
    calibrator: LogitLinearCalibrator | IdentityCalibrator,
) -> list[OutcomePair]:
    return [
        OutcomePair(
            decision_id=p.decision_id,
            predicted_probability=calibrator.transform(p.predicted_probability),
            actual_outcome=p.actual_outcome,
            weight=p.weight,
            timestamp_utc=p.timestamp_utc,
        )
        for p in pairs
    ]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


# ─── Validator ────────────────────────────────────────────────────────────────


def walk_forward_validate(
    pairs: Sequence[OutcomePair],
    *,
    config: WalkForwardConfig | None = None,
) -> WalkForwardReport:
    """Anchored expanding-window walk-forward validation.

    Returns a :class:`WalkForwardReport` with a hard decision; never raises
    on degenerate input.
    """
    cfg = config or WalkForwardConfig()
    ordered = _ordered_pairs(pairs)
    n = len(ordered)

    if n < cfg.min_train_size + cfg.min_test_size:
        return WalkForwardReport(
            n_pairs=n,
            n_splits_run=0,
            splits=(),
            mean_oos_brier_improvement=0.0,
            median_oos_brier_improvement=0.0,
            consistency_ratio=0.0,
            decision="insufficient_data",
            decision_reasons=(f"have {n} pairs, need >= {cfg.min_train_size + cfg.min_test_size}",),
            config=cfg,
        )

    train_min_idx = max(int(round(cfg.train_fraction * n)), cfg.min_train_size)
    train_min_idx = min(train_min_idx, n - cfg.min_test_size)
    test_total = n - train_min_idx
    bucket_size = test_total // cfg.n_splits

    if bucket_size < cfg.min_test_size:
        # Reduziere Splits, bis Bucket-Size passt — sauber degradieren
        max_splits = max(1, test_total // cfg.min_test_size)
        bucket_size = test_total // max_splits
        n_splits_used = max_splits
    else:
        n_splits_used = cfg.n_splits

    splits: list[WalkForwardSplit] = []
    improvements: list[float] = []
    improved_count = 0

    for fold in range(n_splits_used):
        train_end = train_min_idx + fold * bucket_size
        test_start = train_end
        test_end = (
            test_start + bucket_size
            if fold < n_splits_used - 1
            else n  # last fold consumes any remainder
        )
        train = ordered[:train_end]
        test = ordered[test_start:test_end]

        if len(train) < cfg.min_train_size or len(test) < cfg.min_test_size:
            continue

        cal = fit_calibrator(train, min_pairs=cfg.min_train_size)
        before_report = compute_calibration(test)
        after_pairs = _apply_calibrator(test, cal)
        after_report = compute_calibration(after_pairs)

        brier_before = before_report.brier_score or 0.0
        brier_after = after_report.brier_score or 0.0
        ece_before = before_report.expected_calibration_error or 0.0
        ece_after = after_report.expected_calibration_error or 0.0

        improvement = brier_before - brier_after
        improved = improvement >= cfg.min_brier_improvement

        improvements.append(improvement)
        if improved:
            improved_count += 1

        splits.append(
            WalkForwardSplit(
                fold=fold,
                n_train=len(train),
                n_test=len(test),
                brier_test_before=round(brier_before, 6),
                brier_test_after=round(brier_after, 6),
                ece_test_before=round(ece_before, 6),
                ece_test_after=round(ece_after, 6),
                brier_improvement=round(improvement, 6),
                improved=improved,
                calibrator_intercept=cal.parameters.intercept,
                calibrator_slope=cal.parameters.slope,
                calibrator_is_identity=cal.parameters.is_identity,
            )
        )

    if not splits:
        return WalkForwardReport(
            n_pairs=n,
            n_splits_run=0,
            splits=(),
            mean_oos_brier_improvement=0.0,
            median_oos_brier_improvement=0.0,
            consistency_ratio=0.0,
            decision="insufficient_data",
            decision_reasons=("no fold met min_train_size and min_test_size simultaneously",),
            config=cfg,
        )

    mean_imp = sum(improvements) / len(improvements)
    median_imp = _median(improvements)
    consistency = improved_count / len(splits)

    decision_reasons: list[str] = []
    if mean_imp < cfg.min_brier_improvement:
        decision_reasons.append(
            f"mean OoS Brier improvement {mean_imp:.4f} < threshold {cfg.min_brier_improvement:.4f}"
        )
    if consistency < cfg.min_consistency:
        decision_reasons.append(
            f"consistency {consistency:.2f} < {cfg.min_consistency:.2f} "
            f"(only {improved_count}/{len(splits)} folds improved)"
        )
    decision: DecisionLiteral = "approve" if not decision_reasons else "reject"
    if decision == "approve":
        decision_reasons.append(
            f"mean Δbrier={mean_imp:.4f} ≥ {cfg.min_brier_improvement:.4f}, "
            f"consistency={consistency:.2f} ≥ {cfg.min_consistency:.2f} "
            f"({improved_count}/{len(splits)} folds)"
        )

    return WalkForwardReport(
        n_pairs=n,
        n_splits_run=len(splits),
        splits=tuple(splits),
        mean_oos_brier_improvement=round(mean_imp, 6),
        median_oos_brier_improvement=round(median_imp, 6),
        consistency_ratio=round(consistency, 4),
        decision=decision,
        decision_reasons=tuple(decision_reasons),
        config=cfg,
    )


__all__ = [
    "DEFAULT_MIN_BRIER_IMPROVEMENT",
    "DEFAULT_MIN_CONSISTENCY",
    "DEFAULT_MIN_TEST_SIZE",
    "DEFAULT_MIN_TRAIN_SIZE",
    "DEFAULT_N_SPLITS",
    "DEFAULT_TRAIN_FRACTION",
    "WalkForwardConfig",
    "WalkForwardReport",
    "WalkForwardSplit",
    "walk_forward_validate",
]
