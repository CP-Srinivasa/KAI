"""Alert threshold engine.

Wraps is_alert_worthy() with a configurable min_priority.
Single responsibility: decide yes/no for alerting.

2026-09-09: Die Entscheidung haengt nicht mehr an der gerundeten Prioritaet.
``min_priority`` bleibt die Konfiguration und die Zahl, ueber die gesprochen
wird; intern wird sie in eine Schwelle auf der kontinuierlichen Groesse
uebersetzt. Grund: ``round(raw * 9) + 1`` legt die Kante zufaellig zwischen zwei
tatsaechlich vorkommende raw-Werte, und die liegen nur 0,005 auseinander. Details
und Messwerte in app/analysis/scoring.py bei ALERT_GATE_RAW.
"""

from __future__ import annotations

from app.analysis.scoring import ALERT_GATE_RAW, is_alert_worthy, min_priority_as_raw_gate
from app.core.domain.document import AnalysisResult


class ThresholdEngine:
    """Decides whether an analysis result warrants an alert.

    min_priority: minimum priority score (1–10) to trigger.
    Default is 7 — only high-priority documents alert.
    """

    def __init__(self, min_priority: int = 7, *, gate_raw: float | None = None) -> None:
        if not 1 <= min_priority <= 10:
            raise ValueError(f"min_priority must be between 1 and 10, got {min_priority}")
        self._min_priority = min_priority
        # Der kalibrierte Wert gilt fuer die Standardschwelle 7. Wer eine andere
        # Schwelle konfiguriert, bekommt deren exakte Entsprechung auf ``raw`` —
        # sonst wuerde eine Konfigurationsaenderung stillschweigend wirkungslos.
        if gate_raw is not None:
            self._gate_raw = gate_raw
        elif min_priority == 7:
            self._gate_raw = ALERT_GATE_RAW
        else:
            self._gate_raw = min_priority_as_raw_gate(min_priority)

    @property
    def min_priority(self) -> int:
        return self._min_priority

    @property
    def gate_raw(self) -> float:
        """Die tatsaechlich wirksame Schwelle auf der kontinuierlichen Groesse."""
        return self._gate_raw

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
            gate_raw=self._gate_raw,
        )
