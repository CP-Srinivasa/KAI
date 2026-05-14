"""Signal-side facade over the audit/ + bayes_journal layer.

Schritt 3 aus `kai_adaptive_learning_backlog_20260509.md` (Architect+Neo
Review): the signal generator must not know that an `app/audit/` schema
even exists. Adding a new reasoning-phase or swapping the journal
implementation should be a one-module change inside this adapter, not
a two-module refactor that crosses package boundaries.

Boundary rules
--------------
- `app/signals/generator.py` may import only from `app/signals/audit_adapter`
  for everything audit-related.
- The adapter holds the `phase=PHASE_*` constants and the
  `append_bayes_report` import; nothing else inside `signals/` should
  reference `app.audit.*` or `app.signals.bayes_journal`.
- `None` (default) is a no-op: callers see the same behaviour they would
  see today with `reasoning_journal=None` and `bayes_audit_path=None`.

The signal-facing method names describe *what the generator did* (e.g.
"calibrator_apply", "bayes_gate_reject"), not the journal-side concept
(e.g. "PHASE_CONFIDENCE_CHANGE"). That keeps the audit-vocabulary out
of the signal-engine vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.audit.structured_reasoning import (
    PHASE_CONFIDENCE_CHANGE,
    PHASE_INVALIDATION,
    ReasoningJournal,
)
from app.signals.bayes_journal import append_bayes_report

if TYPE_CHECKING:
    from app.signals.bayesian_confidence import ConfidenceReport


class SignalAuditAdapter:
    """Single place where signals/ talks to audit/ + bayes_journal.

    Both inputs are optional; an adapter constructed with both `None`
    is a silent no-op and the signal generator behaves exactly as if
    no audit hooks were configured.
    """

    def __init__(
        self,
        *,
        reasoning_journal: ReasoningJournal | None = None,
        bayes_audit_path: Path | str | None = None,
    ) -> None:
        self._reasoning_journal = reasoning_journal
        self._bayes_audit_path = Path(bayes_audit_path) if bayes_audit_path is not None else None

    @property
    def is_journaling(self) -> bool:
        """True if at least one audit sink is configured."""
        return self._reasoning_journal is not None or self._bayes_audit_path is not None

    # -- Reasoning-journal hooks ---------------------------------------------

    def log_calibrator_apply(
        self,
        *,
        decision_id: str,
        actor: str,
        rationale_summary: str,
        inputs: Mapping[str, Any],
        outputs: Mapping[str, Any],
        confidence_before: float,
        confidence_after: float,
        parameter_versions: Mapping[str, str],
        evidence_refs: Sequence[str] = (),
    ) -> None:
        """Record a calibrator-apply step (raw posterior → calibrated)."""
        if self._reasoning_journal is None:
            return
        self._reasoning_journal.log_step(
            decision_id=decision_id,
            phase=PHASE_CONFIDENCE_CHANGE,
            actor=actor,
            rationale_summary=rationale_summary,
            inputs=dict(inputs),
            outputs=dict(outputs),
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            parameter_versions=dict(parameter_versions),
            evidence_refs=tuple(evidence_refs),
        )

    def log_bayes_gate_reject(
        self,
        *,
        decision_id: str,
        actor: str,
        rationale_summary: str,
        inputs: Mapping[str, Any],
        outputs: Mapping[str, Any],
        parameter_versions: Mapping[str, str],
    ) -> None:
        """Record a bayes-gate invalidation (signal rejected by gate)."""
        if self._reasoning_journal is None:
            return
        self._reasoning_journal.log_step(
            decision_id=decision_id,
            phase=PHASE_INVALIDATION,
            actor=actor,
            rationale_summary=rationale_summary,
            inputs=dict(inputs),
            outputs=dict(outputs),
            parameter_versions=dict(parameter_versions),
        )

    # -- Bayes-journal hook --------------------------------------------------

    def record_raw_bayes_report(
        self,
        *,
        decision_id: str,
        symbol: str,
        direction: str,
        report: ConfidenceReport,
    ) -> None:
        """Append the raw Bayes report (pre-calibration) to the audit JSONL."""
        if self._bayes_audit_path is None:
            return
        append_bayes_report(
            decision_id=decision_id,
            symbol=symbol,
            direction=direction,
            report=report,
            path=self._bayes_audit_path,
        )


__all__ = ["SignalAuditAdapter"]
