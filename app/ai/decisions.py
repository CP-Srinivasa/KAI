"""Pure policy primitives for probabilistic, advisory decisions.

The model reports evidence.  KAI decides what that evidence may do.  Keeping
the threshold policy here prevents a transport or provider from becoming a
second control plane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class DecisionDisposition(StrEnum):
    NEGATIVE = "NEGATIVE"
    REVIEW = "REVIEW"
    POSITIVE = "POSITIVE"


@dataclass(frozen=True, slots=True)
class ProbabilityPolicy:
    """Two thresholds with an explicit review band between them."""

    negative_below: float
    positive_at: float

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in (self.negative_below, self.positive_at)
        ):
            raise ValueError("probability thresholds must be finite numbers")
        if not 0 <= self.negative_below < self.positive_at <= 1:
            raise ValueError("thresholds must satisfy 0 <= negative < positive <= 1")

    def classify(self, probability: float) -> DecisionDisposition:
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(float(probability))
            or not 0 <= probability <= 1
        ):
            raise ValueError("probability must be a finite number within [0, 1]")
        if probability >= self.positive_at:
            return DecisionDisposition.POSITIVE
        if probability <= self.negative_below:
            return DecisionDisposition.NEGATIVE
        return DecisionDisposition.REVIEW


__all__ = ["DecisionDisposition", "ProbabilityPolicy"]
