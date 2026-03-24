# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-24 | PHASE 5 (active) -- Signal Reliability & Trust | PH5C active (D-95, par85) | next_required_step: PH5C_EXECUTION | baseline: 1615 passed, ruff clean

## Phase Status

- current_phase: `PHASE 5 (active) -- Signal Reliability & Trust`
- current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (active D-95, par85)`
- next_required_step: `PH5C_EXECUTION`
- phase4_status: `CLOSED (D-87)`
- ph5b_finding: all 19 LLM-error-proxy docs are EMPTY_MANUAL (placeholder content, not model failure)

## Newly Confirmed Facts

- All 19 LLM-error-proxy cases belong to the `EMPTY_MANUAL` cluster.
- Root cause is empty/manual placeholder content, not model failure.
- PH5B produced cluster analysis artifacts.
- Working tree is clean.
- Status report is in-repo (`status_report.md`).
- Baseline remains `1609 passed` and `ruff clean`.

## Current Assumptions and Decisions

- Assumption: a pre-LLM stub/empty filter is the smallest useful next intervention.
- Assumption: PH5C should improve reliability interpretation and LLM cost efficiency.
- Decision: PH5B is ready to close.
- Decision: recommended next sprint is `PH5C_FILTER_BEFORE_LLM_BASELINE`.
- Decision: do not open a broader model-quality sprint before PH5C is assessed.

## Next Actions

- Close PH5B formally.
- Define PH5C as a narrow pre-LLM filter sprint.
- Freeze PH5C contract.

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



