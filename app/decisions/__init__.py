# app/decisions — Decision Journal (append-only, audit-safe)
#
# Canonical model: DecisionRecord (from app.execution.models)
# Journal projection lives in app.orchestrator.decision_journal.
# app.decisions.journal is kept as compatibility shim.
# DecisionInstance is a TypeAlias for DecisionRecord (Sprint 37 convergence).

from app.execution.models import DecisionRecord as DecisionRecord  # noqa: PLC0414
