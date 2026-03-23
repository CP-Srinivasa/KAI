# PHASE_PLAN.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition)`
- next_required_step: `PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
- ph4g_status: `closed (formal closeout recorded; §75 frozen anchor)`
- ph4h_status: `active (definition mode; policy review sprint before any I-13 intervention)`
- baseline: `1538 passed, ruff clean`

## Phase 4 Arc (PH4A-H)

| Sprint | Layer | Result |
|---|---|---|
| PH4A | Baseline | 0% overlap, paired=0 |
| PH4B | Overlap | paired=69, MAE 3.13 |
| PH4C | Keyword audit | 42% zero-hit |
| PH4D | Keyword expand | 42%->37.7%, diminishing |
| PH4E | Scoring calibration | relevance 41.2% of gap |
| PH4F | Input completeness | 65% weight hardcoded |
| PH4G | Fallback enrichment | Relevance floor ✅, actionable ❌ (I-13) |
| PH4H | Policy review | Active definition sprint (I-13 / fallback actionability) |

## I-13 Policy Question

`test_rule_only_priority_ceiling_is_at_most_five` enforces max priority 5 for rule-only analysis. PH4G confirmed this policy constraint during execution. Next step is PH4H policy review before any direct `I-13` change.
