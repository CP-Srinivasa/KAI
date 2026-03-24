# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-24 | PHASE 4 CLOSED (D-87) | last_sprint: PHASE4_CLOSEOUT_AND_NEXT_PHASE_GATE (closed D-87) | next: Phase 5 definition | baseline: 1604 passed, ruff clean, mypy 0 errors

## Phase Status

- current_phase: `PHASE 4 (CLOSED D-87, 2026-03-24)`
- current_sprint: `PHASE4_CLOSEOUT_AND_NEXT_PHASE_GATE -- CLOSED`
- next_required_step: `Phase 5 definition`
- canonical closeout sync: complete

## Newly Confirmed Facts

- V-4 Dual-Write is closed.
- N-4 is closed.
- Working tree is clean.
- Current technical baseline is `1609 passed` and `ruff clean`.
- Phase 4 has completed a full PH4A-PH4K arc.
- Governance conflict remains between "Phase 4 closed" and "Phase 4 closeout gate active".

## Current Assumptions and Decisions

- Assumption: Phase 4 is complete enough to be formally closed.
- Assumption: PH4L is not mandatory before closing Phase 4.
- Decision: recommended next step is to close Phase 4 formally.
- Decision: PH4L stays blocked until closeout, unless a strong blocker demands escalation.
- Decision: Phase 5 remains blocked until final canonical closeout sync is complete.

## Closeout Actions

- Phase 4 canonical closeout sync complete (D-87).
- Close the gate sprint everywhere.
- Then start Phase 5 definition.
## PH4H Policy Anchor

- Policy choice: Option B
- `I-13` remains enforced.
- `actionable` remains LLM-only.

## PH4J Verification Outcome

- PH4J live verification passed.
- Fallback tags include: `categories`, `affected_assets`, `source_name`, `market_scope.value`.
- Tag improvements: keyword-hit `4->7`, zero-hit `1->4`, assets-only `0->4`.
- `29/29` pipeline tests passed.
- `I-13` remained intact.
- DB test failures remain on a separate track.

## PH4K Closed (D-84)

- Sprint: `PH4K_TAG_SIGNAL_UTILITY_REVIEW`. **Formally closed (D-84).**
- fallback_tags_populated_docs: `69/69`.
- watchlist_overlap_docs: `36/69` (`52.17%`).
- corr(tag_count, tier3_priority): `0.5564`.
- mean_tier3_priority with watchlist overlap: `5.4444`.
- mean_tier3_priority without watchlist overlap: `2.3333`.
- Result: strong utility signal confirmed; results review complete.

## PH4I Frozen Anchor

- `_fallback_market_scope()` enrichment is closed and frozen (`section 77`, `D-78`).
- Baseline snapshot for this gate: `1609 passed, ruff clean`.
