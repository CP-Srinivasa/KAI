"""Alert threshold engine.

Wraps is_alert_worthy() with a configurable min_priority.
Single responsibility: decide yes/no for alerting.
"""

from __future__ import annotations

from app.analysis.scoring import is_alert_worthy
from app.core.domain.document import AnalysisResult


class ThresholdEngine:
    """Decides whether an analysis result warrants an alert.

    min_priority: minimum priority score (1–10) to trigger.
    Default is 7 — only high-priority documents alert.
    """

    def __init__(self, min_priority: int = 7) -> None:
        if not 1 <= min_priority <= 10:
            raise ValueError(f"min_priority must be between 1 and 10, got {min_priority}")
        self._min_priority = min_priority

    @property
    def min_priority(self) -> int:
        return self._min_priority

    def should_alert(
        self,
        result: AnalysisResult,
        spam_probability: float = 0.0,
    ) -> bool:
        """Return True if this result should trigger an alert."""
        return is_alert_worthy(
            result,
            min_priority=self._min_priority,
            spam_probability=spam_probability,
        )
