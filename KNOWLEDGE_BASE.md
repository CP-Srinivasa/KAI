# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (frozen D-77; execution-ready) | Next: PH4I_EXECUTION | Baseline: 1538 passed, ruff clean

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

- current sprint: `PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (frozen D-77; execution-ready)`
- next required step: `PH4I_EXECUTION`
- contract: `docs/contracts.md §77` (frozen D-77; execution-ready)
- constraints: no direct `I-13` change, no fallback actionability expansion
