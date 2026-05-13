"""Active calibrator loader — bridges YAML snapshot to runtime apply.

Used by signal/risk modules at boot or on demand:

    from app.learning.active_calibrator import ActiveCalibrator
    cal = ActiveCalibrator.load(
        parameter_path="bayes.calibrator.regime_bundle",
        snapshot_dir="config/learning",
    )
    if cal.is_active:
        report = cal.apply_to_report(report, direction="long", regime="high_vol")

Design contract
---------------

- **Boot-safe.**  No snapshot ⇒ Identity (the original report passes through).
- **Schema-tolerant.**  Accepts both shapes:
    - `RegimeCalibratorBundle` payload (regimes + global)
    - Single calibrator payload (intercept + slope + n_fitted)
- **Side-aware.**  Long signals apply transform on raw posterior; short
  signals flip via `1 − p` so the *signal-direction* probability is what
  gets calibrated (matches `app/learning/calibration_loader.py`).
- **Recomputed only what depends on posterior.**  `confidence_score` is
  re-derived from the new posterior * unchanged certainty.  Everything
  else (uncertainty, evidence, contributions) carries through verbatim.
- **Active calibrator never mutates audit rows.**  Callers that audit
  the *raw* Bayes report continue to do so — `apply_to_report` returns a
  new immutable report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from app.learning.config_snapshot import (
    DEFAULT_SNAPSHOT_DIR,
    ConfigSnapshot,
    read_snapshot,
)
from app.learning.regime_calibration import (
    GLOBAL_BUCKET,
    RegimeCalibratorBundle,
    RegimeCalibratorEntry,
)

if TYPE_CHECKING:
    from app.signals.bayesian_confidence import ConfidenceReport

logger = logging.getLogger(__name__)

DEFAULT_BAYES_CALIBRATOR_PATH: Final[str] = "bayes.calibrator.regime_bundle"


# ─── Loader ────────────────────────────────────────────────────────────────────


def _bundle_from_payload(payload: dict[str, Any]) -> RegimeCalibratorBundle:
    """Translate either calibrator payload shape into a `RegimeCalibratorBundle`."""
    if "regimes" in payload and "global" in payload:
        return RegimeCalibratorBundle.from_parameter_set(payload)

    # Single-calibrator payload — wrap as a degenerate bundle (only global).
    required = {"intercept", "slope", "n_fitted"}
    if not required.issubset(payload):
        raise ValueError(
            f"unrecognized calibrator payload — needs either 'regimes'+'global' "
            f"or {sorted(required)}; got keys={sorted(payload)}"
        )
    intercept = float(payload["intercept"])
    slope = float(payload["slope"])
    n_fitted = int(payload["n_fitted"])
    is_identity = bool(
        payload.get(
            "is_identity",
            abs(intercept) < 1e-6 and abs(slope - 1.0) < 1e-6,
        )
    )
    fit_notes = tuple(payload.get("fit_notes", ()))
    global_entry = RegimeCalibratorEntry(
        regime=GLOBAL_BUCKET,
        intercept=intercept,
        slope=slope,
        n_fitted=n_fitted,
        is_identity=is_identity,
        is_fallback=False,
        fit_notes=fit_notes,
    )
    return RegimeCalibratorBundle(
        regimes={},
        global_calibrator=global_entry,
        min_pairs_per_regime=int(payload.get("min_pairs_per_regime", 0)),
    )


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


@dataclass(frozen=True)
class ActiveCalibratorState:
    """Loaded view of a parameter_path's currently-active calibrator."""

    parameter_path: str
    snapshot: ConfigSnapshot | None
    bundle: RegimeCalibratorBundle | None  # None when no/invalid snapshot

    @property
    def is_active(self) -> bool:
        return self.bundle is not None

    @property
    def version_id(self) -> str | None:
        return self.snapshot.version_id if self.snapshot else None

    @property
    def activated_at_utc(self) -> str | None:
        return self.snapshot.activated_at_utc if self.snapshot else None


class ActiveCalibrator:
    """Apply the currently-active calibrator to probabilities/reports."""

    def __init__(self, state: ActiveCalibratorState) -> None:
        self._state = state

    @classmethod
    def load(
        cls,
        *,
        parameter_path: str = DEFAULT_BAYES_CALIBRATOR_PATH,
        snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
    ) -> ActiveCalibrator:
        snapshot = read_snapshot(parameter_path, snapshot_dir)
        bundle: RegimeCalibratorBundle | None = None
        if snapshot is not None:
            try:
                bundle = _bundle_from_payload(snapshot.parameter_set)
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning(
                    "[active-calibrator] %s payload is unusable, falling back to identity: %s",
                    parameter_path,
                    exc,
                )
                bundle = None
        state = ActiveCalibratorState(
            parameter_path=parameter_path,
            snapshot=snapshot,
            bundle=bundle,
        )
        return cls(state)

    @classmethod
    def identity(cls, parameter_path: str = DEFAULT_BAYES_CALIBRATOR_PATH) -> ActiveCalibrator:
        return cls(ActiveCalibratorState(parameter_path=parameter_path, snapshot=None, bundle=None))

    # ----- properties ----------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._state.is_active

    @property
    def version_id(self) -> str | None:
        return self._state.version_id

    @property
    def parameter_path(self) -> str:
        return self._state.parameter_path

    @property
    def state(self) -> ActiveCalibratorState:
        return self._state

    # ----- apply ---------------------------------------------------------

    def apply(self, p: float, *, regime: str | None = None) -> float:
        """Apply the calibrator. Identity if no active snapshot."""
        if self._state.bundle is None:
            return _clamp01(p)
        return _clamp01(self._state.bundle.transform(p, regime=regime))

    def apply_side_aware(
        self,
        posterior: float,
        *,
        direction: str,
        regime: str | None = None,
    ) -> float:
        """Apply calibration on the *signal-direction* probability.

        Bayes posterior is a long-direction probability; for short signals
        we flip via 1 − p so the calibrator is always evaluated on
        "win-probability for the signal direction" — and we flip back
        afterwards so the returned value remains a long-direction posterior.
        """
        normalized = direction.strip().lower()
        if normalized == "short":
            p_signal = 1.0 - posterior
            p_calibrated = self.apply(p_signal, regime=regime)
            return _clamp01(1.0 - p_calibrated)
        # long / unknown → treat as long-direction
        return self.apply(posterior, regime=regime)

    def apply_to_report(
        self,
        report: ConfidenceReport,
        *,
        direction: str,
        regime: str | None = None,
    ) -> ConfidenceReport:
        """Return a new ConfidenceReport with calibrated posterior + recomputed
        confidence. All other fields (uncertainty, evidence, contributions)
        are carried over verbatim — the calibrator only shifts the mean.

        Direction-aware confidence: when the calibrator pushes the posterior
        *against* the signal direction (i.e. p_signal < 0.5), confidence drops
        to 0. The Bayes engine itself is direction-neutral and uses
        ``abs(2p − 1)``; here we know the signal direction, so we replace
        that with ``max(0, 2·p_signal − 1)`` — a calibrator that disagrees
        with the signal must reduce confidence, not amplify it.
        """
        if not self.is_active:
            return report
        new_posterior = self.apply_side_aware(
            report.posterior_probability, direction=direction, regime=regime
        )
        normalized = direction.strip().lower()
        p_signal = new_posterior if normalized != "short" else 1.0 - new_posterior
        directional_strength = max(0.0, 2.0 * p_signal - 1.0)
        certainty = max(0.0, 1.0 - report.uncertainty_score)
        new_confidence = _clamp01(directional_strength * certainty)
        return report.model_copy(
            update={
                "posterior_probability": round(new_posterior, 6),
                "confidence_score": round(new_confidence, 6),
            }
        )


__all__ = [
    "DEFAULT_BAYES_CALIBRATOR_PATH",
    "ActiveCalibrator",
    "ActiveCalibratorState",
]
