# DECISION_LOG.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4E_SCORING_CALIBRATION_AUDIT`
- next_required_step: `PH4E_EXECUTION_START`
- baseline: `1519 passed, ruff clean`

## Canonical Decisions

### D-65: Interim review completed
- Decision: PH4A-PH4D was treated as one evidence arc.
- Outcome: keyword expansion showed diminishing returns.

### D-66: Next lever selected
- Decision: `PH4E_SCORING_CALIBRATION_AUDIT` is the next sprint candidate.
- Constraint: diagnostic-only; no direct scoring/threshold/runtime changes.

### D-68: Governance conflict resolved
- Decision: PH4D is formally closed across governance docs.
- Decision: PH4E is active in definition mode across governance docs.

### D-69: PH4E contract freeze completed
- Decision: PH4E scope and acceptance are frozen before execution.
- Constraint: no scoring formula changes, no threshold changes, no rule/provider/source/model changes.

### D-70: PH4E execution can start
- Decision: next required step is `PH4E_EXECUTION_START`.
- Consequence: PH4E runs as diagnostic-only execution against frozen inputs.
