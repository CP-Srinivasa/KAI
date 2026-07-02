"""Release / live-readiness classification (fail-closed).

Single source of truth for the question "may KAI go live?". Default is
fail-closed: unless EVERY hard gate is satisfied, the classification is
``operator_paper_ready`` / ``paper_only`` / ``live_blocked`` with
machine-readable blocker reasons. Pure, read-only — no execution side effects.
"""

from app.release.readiness import (
    OPERATOR_COMMS,
    TRADING_CRITICAL_MODULES,
    Blocker,
    ExecutionPosture,
    LiveReadinessEvidence,
    ReleaseClassification,
    ReleaseStatus,
    classify_release,
    compute_net_edge_bps,
    default_ignored_mypy_modules,
)

__all__ = [
    "OPERATOR_COMMS",
    "TRADING_CRITICAL_MODULES",
    "Blocker",
    "ExecutionPosture",
    "LiveReadinessEvidence",
    "ReleaseClassification",
    "ReleaseStatus",
    "classify_release",
    "compute_net_edge_bps",
    "default_ignored_mypy_modules",
]
