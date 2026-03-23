# DECISION_LOG.md

## Current State (2026-03-23)

- current_sprint: `PH4G (closed)`
- next_required_step: `PH4H_POLICY_REVIEW`
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
### D-67: PH4F complete (fallback path, 65% hardcoded)

### D-68 (2026-03-23): PH4G complete — relevance floor + I-13 blocker
- Intervention 1 (relevance floor 0.08): Applied successfully.
- Intervention 2 (actionable heuristic): Reverted — violates I-13 invariant (rule-only priority ceiling max 5). The +1 actionable bonus in `compute_priority()` pushed priority to 7.
- Code comment at `pipeline.py:487-489` documents the constraint.
- Consequence: `actionable` can never be True in fallback mode without relaxing I-13.

### D-69 (2026-03-23): PH4G formally closed — §75 immutable anchor
- PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE formally closed.
- §75 is now an immutable frozen anchor. No re-execution permitted.
- Retained: relevance-floor fallback intervention.
- Reverted: actionable heuristic (blocked by I-13).
- Baseline confirmed: 1538 passed, ruff clean.

### D-70 (2026-03-23): PH4H opened — policy review before any I-13 change
- Sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW`.
- Contract: `docs/contracts.md §76`.
- Purpose: review-only sprint; no code changes, no I-13 relaxation permitted before policy decision.
- Policy options under review: (a) relax I-13, (b) accept actionable as permanently LLM-only, (c) hybrid gate.
