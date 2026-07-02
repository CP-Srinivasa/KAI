"""Regime-spezifische Calibration — pro Markt-Regime ein eigener Calibrator.

Motivation
----------

Ein einzelner globaler Calibrator (siehe ``app/learning/recalibration.py``)
mittelt über alle Markt-Regimes — die Bayes-Engine driftet aber typischerweise
*regime-abhängig*: in High-Vol oft overconfident, in Low-Vol underconfident.
Ein gemittelter Korrektur-Faktor verschenkt diese Information.

Diese Schicht hält pro Regime einen eigenen ``LogitLinearCalibrator`` und
fällt für sparse Buckets (zu wenig Outcome-Pairs) sauber auf einen globalen
Calibrator zurück.

Persistierung
-------------

Das Bundle wird **als Ganzes** als ein Eintrag im hash-chained
``parameter_journal.jsonl`` versioniert (parameter_path z. B.
``bayes.calibrator.regime_bundle``). ``to_parameter_set()`` liefert eine
flache JSON-Darstellung, die direkt in
``ParameterVersionStore.propose_version(parameter_set=...)`` passt.

Begründung: Ein einzelner Versionseintrag pro Bundle sorgt für atomic
swaps — entweder das ganze Set wird scharfgeschaltet, oder gar keins.
Halbe Aktivierungen (manche Regimes neu, andere alt) sind ein Audit-Albtraum.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.learning.calibration import OutcomePair
from app.learning.recalibration import (
    DEFAULT_MIN_PAIRS_FOR_FIT,
    CalibratorParameters,
    IdentityCalibrator,
    LogitLinearCalibrator,
    fit_calibrator,
)

GLOBAL_BUCKET: Final[str] = "__global__"
UNKNOWN_REGIME_KEY: Final[str] = "__unknown__"


# ─── Models ────────────────────────────────────────────────────────────────────


class RegimeCalibratorEntry(BaseModel):
    """Calibrator-Parameter für ein einzelnes Regime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    regime: str
    intercept: float
    slope: float
    n_fitted: int
    is_identity: bool
    is_fallback: bool  # True wenn Sparse-Bucket → Bundle-Global verwendet
    fit_notes: tuple[str, ...] = Field(default_factory=tuple)


class RegimeCalibratorBundle(BaseModel):
    """Komplettes Bundle: per-Regime Calibratoren + globaler Fallback."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    regimes: dict[str, RegimeCalibratorEntry]
    global_calibrator: RegimeCalibratorEntry
    min_pairs_per_regime: int

    # ----- apply ----------------------------------------------------------

    def transform(self, p: float, regime: str | None = None) -> float:
        """Apply the regime-specific calibrator (or global fallback).

        regime=None → global. Unknown regime → global. Identity-Bundle is
        a no-op.
        """
        entry = self._select_entry(regime)
        cal = self._entry_to_calibrator(entry)
        return cal.transform(p)

    def _select_entry(self, regime: str | None) -> RegimeCalibratorEntry:
        if regime is None or regime not in self.regimes:
            return self.global_calibrator
        entry = self.regimes[regime]
        if entry.is_fallback:
            return self.global_calibrator
        return entry

    @staticmethod
    def _entry_to_calibrator(
        entry: RegimeCalibratorEntry,
    ) -> LogitLinearCalibrator | IdentityCalibrator:
        if entry.is_identity:
            return IdentityCalibrator()
        return LogitLinearCalibrator(
            CalibratorParameters(
                intercept=entry.intercept,
                slope=entry.slope,
                n_fitted=entry.n_fitted,
                is_identity=False,
                fit_notes=entry.fit_notes,
            )
        )

    # ----- persistence ---------------------------------------------------

    def to_parameter_set(self) -> dict[str, Any]:
        """Flat JSON-friendly form for ParameterVersion storage.

        Schema:
        {
          "min_pairs_per_regime": 30,
          "global": {"intercept": ..., "slope": ..., "n_fitted": ...,
                     "is_identity": ..., "is_fallback": false,
                     "fit_notes": [...]},
          "regimes": {
            "low_vol": {... entry ...},
            "high_vol": {... entry ..., "is_fallback": true},
            ...
          }
        }
        """
        return {
            "min_pairs_per_regime": self.min_pairs_per_regime,
            "global": _entry_to_dict(self.global_calibrator),
            "regimes": {k: _entry_to_dict(v) for k, v in self.regimes.items()},
        }

    @classmethod
    def from_parameter_set(cls, payload: dict[str, Any]) -> RegimeCalibratorBundle:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        if "global" not in payload or "regimes" not in payload:
            raise ValueError("payload must contain 'global' and 'regimes' keys")
        global_entry = _entry_from_dict(payload["global"], regime=GLOBAL_BUCKET)
        regimes_raw = payload["regimes"]
        if not isinstance(regimes_raw, dict):
            raise ValueError("'regimes' must be a dict")
        regimes = {k: _entry_from_dict(v, regime=k) for k, v in regimes_raw.items()}
        min_pairs = int(payload.get("min_pairs_per_regime", DEFAULT_MIN_PAIRS_FOR_FIT))
        return cls(
            regimes=regimes,
            global_calibrator=global_entry,
            min_pairs_per_regime=min_pairs,
        )


def _entry_to_dict(entry: RegimeCalibratorEntry) -> dict[str, Any]:
    return {
        "regime": entry.regime,
        "intercept": entry.intercept,
        "slope": entry.slope,
        "n_fitted": entry.n_fitted,
        "is_identity": entry.is_identity,
        "is_fallback": entry.is_fallback,
        "fit_notes": list(entry.fit_notes),
    }


def _entry_from_dict(payload: Any, *, regime: str) -> RegimeCalibratorEntry:
    if not isinstance(payload, dict):
        raise ValueError(f"entry payload for {regime!r} must be a dict")
    return RegimeCalibratorEntry(
        regime=str(payload.get("regime", regime)),
        intercept=float(payload["intercept"]),
        slope=float(payload["slope"]),
        n_fitted=int(payload["n_fitted"]),
        is_identity=bool(payload["is_identity"]),
        is_fallback=bool(payload.get("is_fallback", False)),
        fit_notes=tuple(payload.get("fit_notes", ())),
    )


# ─── Fitter ────────────────────────────────────────────────────────────────────


def _calibrator_to_entry(
    *,
    regime: str,
    cal: LogitLinearCalibrator | IdentityCalibrator,
    is_fallback: bool,
) -> RegimeCalibratorEntry:
    p = cal.parameters
    return RegimeCalibratorEntry(
        regime=regime,
        intercept=p.intercept,
        slope=p.slope,
        n_fitted=p.n_fitted,
        is_identity=p.is_identity,
        is_fallback=is_fallback,
        fit_notes=p.fit_notes,
    )


def fit_regime_calibrators(
    pairs: Sequence[OutcomePair],
    *,
    min_pairs_per_regime: int = DEFAULT_MIN_PAIRS_FOR_FIT,
    expected_regimes: Sequence[str] | None = None,
) -> RegimeCalibratorBundle:
    """Bucket pairs by `regime`, fit a per-regime calibrator, fall back to
    the global fit for sparse buckets.

    `expected_regimes` (optional): explicit list of regimes that should appear
    in the output bundle, even if no pairs landed in them. Sparse/missing
    regimes are emitted as fallback entries pointing at the global calibrator.

    Pairs without `regime` (None) contribute only to the global fit.
    """
    # Bucket
    by_regime: dict[str, list[OutcomePair]] = defaultdict(list)
    global_pool: list[OutcomePair] = []
    for pair in pairs:
        global_pool.append(pair)
        if pair.regime is not None:
            by_regime[pair.regime].append(pair)

    # Global calibrator from the entire pool
    global_cal = fit_calibrator(global_pool, min_pairs=min_pairs_per_regime)
    global_entry = _calibrator_to_entry(regime=GLOBAL_BUCKET, cal=global_cal, is_fallback=False)

    # Determine the union of regimes to emit
    regime_keys: set[str] = set(by_regime.keys())
    if expected_regimes is not None:
        regime_keys.update(expected_regimes)

    regime_entries: dict[str, RegimeCalibratorEntry] = {}
    for regime in sorted(regime_keys):
        bucket = by_regime.get(regime, [])
        if len(bucket) >= min_pairs_per_regime:
            cal = fit_calibrator(bucket, min_pairs=min_pairs_per_regime)
            entry = _calibrator_to_entry(regime=regime, cal=cal, is_fallback=False)
        else:
            entry = _calibrator_to_entry(regime=regime, cal=global_cal, is_fallback=True)
        regime_entries[regime] = entry

    return RegimeCalibratorBundle(
        regimes=regime_entries,
        global_calibrator=global_entry,
        min_pairs_per_regime=min_pairs_per_regime,
    )


__all__ = [
    "GLOBAL_BUCKET",
    "RegimeCalibratorBundle",
    "RegimeCalibratorEntry",
    "UNKNOWN_REGIME_KEY",
    "fit_regime_calibrators",
]
