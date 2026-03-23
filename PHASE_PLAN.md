# PHASE_PLAN.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (active definition)`
- next_required_step: `PH4J_DEFINITION_AND_CONTRACT_FREEZE`
- ph4g_status: `closed (formal closeout recorded; S75 frozen anchor)`
- ph4h_status: `closed (D-74/75; policy decision recorded; S76 frozen anchor)`
- ph4i_status: `active (definition — D-76) — market_scope enrichment; §77 contract`
- baseline: `1551 passed, ruff clean`

## Phase 4 Arc (PH4A-I)

| Sprint | Layer | Result |
|---|---|---|
| PH4A | Baseline | 0% overlap, paired=0 |
| PH4B | Overlap | paired=69, MAE 3.13 |
| PH4C | Keyword audit | 42% zero-hit |
| PH4D | Keyword expand | 42%->37.7%, diminishing |
| PH4E | Scoring calibration | relevance 41.2% of gap |
| PH4F | Input completeness | 65% weight hardcoded |
| PH4G | Fallback enrichment | Relevance floor OK, actionable blocked (I-13) |
| PH4H | Policy review | actionable=LLM-only confirmed; I-13 permanent |
| PH4I | Market scope | market_scope enrichment in fallback path (PH4F finding) |

## I-13 Policy Decision (D-74)

`test_rule_only_priority_ceiling_is_at_most_five` enforces max priority 5 for rule-only analysis.
PH4H policy decision (Option B): `actionable` is an LLM-exclusive semantic judgment.
Rule-only fallback MUST set `actionable=False` -- this is correct by design, not a bug.
I-13 is a permanent invariant. No relaxation planned.

## PH4I Next Sprint

`PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT` addresses `market_scope unknown 69/69` from PH4F.
Policy-safe: market_scope is metadata, no scoring impact, no I-13 conflict.
