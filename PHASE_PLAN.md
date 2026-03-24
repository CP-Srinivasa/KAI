# PHASE_PLAN.md

## Current State (2026-03-24)

- current_phase: `PHASE 5 (active) -- Signal Reliability & Trust`
- current_sprint: `PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST (frozen §83)`
- next_required_step: `PH5A_EXECUTION`
- phase4_status: `CLOSED (D-87)`
- baseline: `1609 passed, ruff clean`

## Phase 4 Closeout (Canonical, 2026-03-24)

Phase 4 has completed a full PH4A-PH4K arc. Canonical closeout is accepted (D-87), V-4 Dual-Write is closed, N-4 is closed, and Phase 5 is now unblocked.

## Next Actions (Phase 5 Definition)

- Open Phase 5 definition.
- Define `PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST`.
- Freeze PH5A contract.
- Keep Phase 5 scope narrow: reliability and signal trust before further fallback micro-optimizations.
## Phase 4 Complete Arc (PH4A-K, 11 Sprints)

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
| PH4J | Intervention | Tags enrichment: keyword-hit 4->7, zero-hit 1->4, assets-only 0->4 |
| PH4K | Utility Review | **Closed (D-84)**: watchlist overlap 52%, corr=0.56, priority delta +3.1 |

## PH4K Results Review Inputs (Cumulative Phase 4 Impact)

### Signal Quality

| Metric | Before Phase 4 | After Phase 4 | Delta |
|---|---|---|---|
| Priority avg | 2.36 | 3.01 | +28% |
| Priority changed | - | 56/69 (81.2%) | - |
| Tags empty | 69/69 (100%) | 26/69 (37.7%) | -62.3% |
| Avg tags/doc | 0 | 2.0 | +2.0 |
| Relevance=0 | 56/69 (81.2%) | 26/69 (37.7%) | -43.5% |
| Scope unknown | 69/69 (100%) | 47/69 (68.1%) | -31.9% |

### Utility Evidence (PH4K Artifacts)

| Metric | Value |
|---|---|
| fallback_tags_populated_docs | 69/69 |
| watchlist_overlap_docs | 36/69 (52.17%) |
| corr(tag_count, tier3_priority) | 0.5564 |
| mean_tier3_priority_with_watch_overlap | 5.4444 |
| mean_tier3_priority_without_watch_overlap | 2.3333 |
| mean_tag_jaccard_vs_tier3 | 0.069 |

## I-13 Policy (Permanent)

`actionable` is LLM-exclusive. Rule-only fallback: `actionable=False`. No relaxation.
