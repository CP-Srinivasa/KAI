# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4J_FALLBACK_TAGS_ENRICHMENT (candidate) | Next: PH4J_DEFINITION_AND_CONTRACT_FREEZE | Baseline: 1551 passed, ruff clean

## PH4H Frozen Policy Outcome

- Policy choice: **Option B**
- `I-13` remains enforced.
- `actionable` remains **LLM-only**.
- Simulated fallback actionability would push rule-only priority into the `6-9` range and is therefore not adopted.

## Why This Matters

- Tier-1 remains conservative and fail-closed.
- Safety is prioritized over additional fallback aggressiveness.
- The next quality lever is relevance/context enrichment, not actionability expansion.

## Active Gate

- current sprint: `PH4J_FALLBACK_TAGS_ENRICHMENT (candidate)`
- next required step: `PH4J_DEFINITION_AND_CONTRACT_FREEZE`
- contract: `docs/contracts.md §78` (candidate for PH4J)
- constraints: no direct `I-13` change, no fallback actionability expansion

## PH4I Outcome

- `_fallback_market_scope()` enriched with crypto_assets, tickers, title keyword signals.
- Before: market_scope UNKNOWN 69/69. After: CRYPTO/EQUITIES resolved where asset signals present.
- New baseline: 1551 passed (+13 tests), ruff clean.
- §77 closed (D-78; frozen anchor).
- Next: PH4J_FALLBACK_TAGS_ENRICHMENT (tags empty 69/69 from PH4F).
