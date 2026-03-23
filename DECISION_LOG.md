# DECISION_LOG.md

## Current State (2026-03-23)

- current_sprint: `PH4K_TAG_SIGNAL_UTILITY_REVIEW (definition frozen)`
- next_required_step: `PH4K_EXECUTION_START`
- baseline: `1554 passed, ruff clean`

## Canonical Decisions

### D-53: PH4A closed (baseline anchor)
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
### D-68: PH4G complete (relevance floor, actionable reverted I-13)
### D-69: PH4H complete — Option B (I-13 permanent, actionable=LLM-only)
### D-74/75: PH4H closed (S76)
### D-76/78: PH4I market_scope enrichment (S77)

### D-79 (2026-03-23): PH4J ready to close — tags enrichment verified
- Intervention: Added categories, affected_assets, source_name, market_scope.value to fallback tags.
- Reordered variable computation so market_scope is available before tags assembly.
- Live test: keyword-hit 4->7 tags, zero-hit 1->4 tags, assets-only 0->4 tags.
- 29/29 pipeline tests passed, I-13 intact.
- Note: Code was externally reverted once during review; re-applied and verified.

### D-80 (2026-03-23): PH4K_TAG_SIGNAL_UTILITY_REVIEW selected as next sprint candidate
- PH4J is functionally successful and ready for formal closeout.
- Next sprint: PH4K_TAG_SIGNAL_UTILITY_REVIEW — assess operator utility of enriched tags.
- Rationale: tag quantity improved (4→7, 1→4, 0→4); next question is utility, not more expansion.
- DB test failures remain on a separate track; not a PH4J or PH4K blocker.
- Next step: `PH4K_DEFINITION_AND_CONTRACT_FREEZE`.

### D-81 (2026-03-23): Conservative transition state locked
- Canonical transition state set to `PH4J_CLOSE_AND_PH4K_DEFINITION`.
- PH4J is treated as closed in governance docs before PH4K activation.
- PH4K remains candidate/definition only until `PH4K_DEFINITION_AND_CONTRACT_FREEZE`.
- DB failures remain on a separate track and are excluded from PH4J functional assessment.

### D-82 (2026-03-23): PH4K contract and acceptance freeze completed
- PH4K governance state accepted as canonical and contract freeze finalized.
- PH4K remains diagnostic-only with strict non-goals (no scoring/threshold/provider/actionability changes).
- Acceptance criteria are locked before execution start.
- Execution remains gated to the PH4K scope and DB-failure noise stays separated.
- Next step: `PH4K_EXECUTION_START`.
