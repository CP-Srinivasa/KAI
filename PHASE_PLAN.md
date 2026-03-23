# PHASE_PLAN.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
- next_required_step: `PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- ph4g_status: `active (execution complete; ready to close)`
- ph4h_status: `candidate only (policy review selected after PH4G closeout)`
- baseline: `1538 passed, ruff clean`

## Phase 4 Arc (PH4A-G)

| Sprint | Layer | Result |
|---|---|---|
| PH4A | Baseline | 0% overlap, paired=0 |
| PH4B | Overlap | paired=69, MAE 3.13 |
| PH4C | Keyword audit | 42% zero-hit |
| PH4D | Keyword expand | 42%->37.7%, diminishing |
| PH4E | Scoring calibration | relevance 41.2% of gap |
| PH4F | Input completeness | 65% weight hardcoded |
| PH4G | Fallback enrichment | Relevance floor ✅, actionable ❌ (I-13) |

## I-13 Policy Question

`test_rule_only_priority_ceiling_is_at_most_five` enforces max priority 5 for rule-only analysis. PH4G confirmed this policy constraint during execution. Next step is PH4H policy review before any direct `I-13` change.
