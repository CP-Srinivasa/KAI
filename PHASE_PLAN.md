# PHASE_PLAN.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4K_TAG_SIGNAL_UTILITY_REVIEW (definition frozen)`
- next_required_step: `PH4K_EXECUTION_START`
- baseline: `1554 passed, ruff clean`

## Phase 4 Complete Arc (PH4A-J)

| Sprint | Type | Result |
|---|---|---|
| PH4A | Diagnostic | Baseline: paired=0 |
| PH4B | Diagnostic | paired=69, MAE 3.13. Keyword blindness |
| PH4C | Diagnostic | 42% zero-hit. Gaps: macro/regulatory/AI |
| PH4D | Intervention | Keywords +56. Zero-hit 42%->37.7% |
| PH4E | Diagnostic | relevance 41.2% of gap. Defaults by design |
| PH4F | Diagnostic | Fallback path. 65% weight hardcoded |
| PH4G | Intervention | Relevance floor applied. Actionable blocked (I-13) |
| PH4H | Policy | Option B: I-13 permanent, actionable=LLM-only (S76) |
| PH4I | Intervention | market_scope enrichment complete (S77) |
| PH4J | Intervention | Closed: tags enrichment verified (keyword-hit 4->7, zero-hit 1->4, assets-only 0->4) |
| PH4K | Utility Review | Definition frozen: diagnostic-only utility review; execution authorized |

## I-13 Policy Decision (D-74)

`test_rule_only_priority_ceiling_is_at_most_five` enforces max priority 5 for rule-only analysis.
PH4H policy decision (Option B): `actionable` is an LLM-exclusive semantic judgment.
Rule-only fallback MUST set `actionable=False` -- this is correct by design, not a bug.
I-13 is a permanent invariant. No relaxation planned.

## PH4K Freeze State

PH4J closeout gate is complete and PH4K contract is frozen.
PH4K remains diagnostic-only (no scoring, threshold, provider, or actionability changes).
Execution is authorized with the next step `PH4K_EXECUTION_START`.
