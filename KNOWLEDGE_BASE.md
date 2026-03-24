# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4K_TAG_SIGNAL_UTILITY_REVIEW | Next: PH4K_RESULTS_REVIEW_AND_CLOSE | Baseline: 1554 passed, ruff clean

## Active Gate

- current sprint: `PH4K_TAG_SIGNAL_UTILITY_REVIEW`
- next required step: `PH4K_RESULTS_REVIEW_AND_CLOSE`
- contract: `docs/contracts.md section 79` (results-review mode)
- constraints: no direct `I-13` change, no fallback actionability expansion

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

## PH4K Execution Outcomes (D-80)

- Sprint: `PH4K_TAG_SIGNAL_UTILITY_REVIEW`.
- fallback_tags_populated_docs: `69/69`.
- watchlist_overlap_docs: `36/69` (`52.17%`).
- corr(tag_count, tier3_priority): `0.5564`.
- mean_tier3_priority with watchlist overlap: `5.4444`.
- mean_tier3_priority without watchlist overlap: `2.3333`.
- Result status: strong utility signal; formal results review still pending.

## PH4I Frozen Anchor

- `_fallback_market_scope()` enrichment is closed and frozen (`section 77`, `D-78`).
- Baseline remains `1554 passed, ruff clean`.
