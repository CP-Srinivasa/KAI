# app/decisions — Decision Journal (append-only, audit-safe)
#
# Canonical model: DecisionRecord (from app.execution.models)
# Journal projection: create_decision_instance / append_decision_jsonl (in journal.py)
# DecisionInstance is a TypeAlias for DecisionRecord (Sprint 37 convergence).

from app.execution.models import DecisionRecord as DecisionRecord  # noqa: PLC0414
