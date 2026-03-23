# DECISION_LOG.md

## Current State (2026-03-23)

- current_sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
- next_required_step: `PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- baseline: `1538 passed, ruff clean`

## Canonical Decisions

### D-53: PH4A closed
### D-54-D-56: PH4B definition/freeze/provider
### D-57: PH4B passed (paired=69, MAE=3.13)
### D-58-D-59: Review-first, root cause keyword blindness
### D-60: PH4B closed, PH4C opened
### D-61: PH4C complete (42% zero-hit)
### D-62: PH4C closed, PH4D opened
### D-63: PH4D complete (56 keywords, 42%->37.7%)
### D-64: PH4D closed, diminishing returns
### D-65: PH4E opened (scoring calibration)
### D-66: PH4E complete (defaults by design)
### D-67: PH4F complete (fallback path and input-completeness anchor)

### D-68 (2026-03-23): PH4G execution complete — relevance floor + I-13 policy blocker
- Intervention 1 (relevance floor): applied successfully.
- Intervention 2 (actionable heuristic): reverted due `I-13` rule-only ceiling (`priority <= 5`).
- Consequence: further fallback usefulness is now policy-constrained, not purely implementation-constrained.

### D-69 (2026-03-23): PH4G moved to ready-to-close state
- PH4G remains the active sprint until formal closeout is recorded.
- Retained: relevance-floor fallback intervention.
- Reverted: actionable heuristic (blocked by policy, not by implementation defect).
- Baseline reconfirmed: `1538 passed`, `ruff clean`.

### D-70 (2026-03-23): PH4H selected as next sprint candidate
- Recommended next sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW`.
- Constraint: no direct `I-13` change before PH4H policy review.
- Next required step: `PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`.
