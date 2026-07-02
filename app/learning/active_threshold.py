"""Boot-time loader for a single float-valued threshold parameter.

Mirrors the contract of ``app/learning/active_calibrator.py`` but for the
simpler case of *one numeric value* per parameter_path:

  parameter_set: {"value": 0.78, "default": 0.30}

Used by gating sites (e.g. ``signal.thresholds.min_bayes_confidence``) that
want to read the operator-approved threshold from the YAML snapshot, with a
hard-coded default fallback if no snapshot exists.

Boot-safe: missing snapshot or malformed payload → returns the hard default.
The active threshold is **immutable after load** — no live re-load — to avoid
mid-run flips that would break audit attribution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.learning.config_snapshot import (
    DEFAULT_SNAPSHOT_DIR,
    ConfigSnapshot,
    read_snapshot,
)

logger = logging.getLogger(__name__)


DEFAULT_MIN_BAYES_CONFIDENCE_PATH: Final[str] = "signal.thresholds.min_bayes_confidence"


@dataclass(frozen=True)
class ActiveThresholdState:
    """Loaded view of a threshold parameter."""

    parameter_path: str
    value: float
    default_value: float
    snapshot: ConfigSnapshot | None

    @property
    def is_active(self) -> bool:
        return self.snapshot is not None

    @property
    def version_id(self) -> str | None:
        return self.snapshot.version_id if self.snapshot else None

    @property
    def activated_at_utc(self) -> str | None:
        return self.snapshot.activated_at_utc if self.snapshot else None


class ActiveThreshold:
    """Read-only float threshold sourced from a YAML snapshot."""

    def __init__(self, state: ActiveThresholdState) -> None:
        self._state = state

    @classmethod
    def load(
        cls,
        *,
        parameter_path: str,
        default_value: float,
        snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
    ) -> ActiveThreshold:
        snapshot = read_snapshot(parameter_path, snapshot_dir)
        value = default_value
        if snapshot is not None:
            raw = snapshot.parameter_set.get("value")
            try:
                value = float(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "[active-threshold] %s payload missing or invalid 'value', "
                    "falling back to default=%s: %s",
                    parameter_path,
                    default_value,
                    exc,
                )
                value = default_value
                snapshot = None  # treat as inactive
        return cls(
            ActiveThresholdState(
                parameter_path=parameter_path,
                value=value,
                default_value=default_value,
                snapshot=snapshot,
            )
        )

    @classmethod
    def fixed(cls, *, parameter_path: str, value: float) -> ActiveThreshold:
        """Construct a 'fixed' threshold without consulting any snapshot."""
        return cls(
            ActiveThresholdState(
                parameter_path=parameter_path,
                value=value,
                default_value=value,
                snapshot=None,
            )
        )

    # ----- read-only API ------------------------------------------------

    @property
    def value(self) -> float:
        return self._state.value

    @property
    def default_value(self) -> float:
        return self._state.default_value

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
    def state(self) -> ActiveThresholdState:
        return self._state


__all__ = [
    "DEFAULT_MIN_BAYES_CONFIDENCE_PATH",
    "ActiveThreshold",
    "ActiveThresholdState",
]
