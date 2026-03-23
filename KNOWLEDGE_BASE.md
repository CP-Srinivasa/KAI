# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4K_TAG_SIGNAL_UTILITY_REVIEW (definition frozen) | Next: PH4K_EXECUTION_START | Baseline: 1554 passed, ruff clean

## Active Gate

- current sprint: `PH4K_TAG_SIGNAL_UTILITY_REVIEW (definition frozen)`
- next required step: `PH4K_EXECUTION_START`
- contract: `docs/contracts.md section 79` (definition frozen)
- constraints: no direct `I-13` change, no fallback actionability expansion

## PH4H Policy Anchor

- Policy choice: **Option B**
- `I-13` remains enforced.
- `actionable` remains **LLM-only**.

## PH4J Verification Outcome

- PH4J live verification passed.
- Fallback tags include: `categories`, `affected_assets`, `source_name`, `market_scope.value`.
- Tag improvements: keyword-hit `4->7`, zero-hit `1->4`, assets-only `0->4`.
- `29/29` pipeline tests passed.
- `I-13` remained intact.
- DB test failures remain on a separate track.

## PH4K Contract Freeze Outcome

- Sprint: `PH4K_TAG_SIGNAL_UTILITY_REVIEW`
- Focus: assess operator utility of PH4J-enriched tags.
- Policy-safe: no scoring changes, no I-13 conflict, no actionability expansion.
- Acceptance criteria are locked.
- Execution is now authorized as the next step.

## PH4I Frozen Anchor

- `_fallback_market_scope()` enrichment is closed and frozen (section 77, D-78).
- Baseline remains `1554 passed, ruff clean`.
