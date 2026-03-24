# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-24 | Phase 4 active (technical stabilization complete) | Last clean governance anchor: V4_DUAL_WRITE_AND_DB_PRIMARY_CLOSEOUT (D-86) | Baseline snapshot: 1604 passed, ruff clean, mypy 0 errors

## Active Gate

- last closed sprint: `V4_DUAL_WRITE_AND_DB_PRIMARY_CLOSEOUT (closed D-86)`
- current sprint: `choose PH4L definition or Phase 4 closeout`
- next required step: `PH4L definition or Phase 4 closeout decision`
- constraint: no direct `I-13` change, no fallback actionability expansion

## Technical Stabilization Closeout (2026-03-24)

- V-4 Phase 2+3 complete: `run_cycle()` dual-writes `TradingCycleRecord` + `PortfolioStateRecord`; non-fatal on DB error.
- `build_portfolio_snapshot()` is DB-primary when `session_factory` provided; falls back to JSONL (no-record or DB error).
- 14 new tests confirmed: 6 dual-write + 8 DB-primary.
- RF-4 promoted to `phase-3-complete` in RISK_REGISTER.
- Baseline: `1604 passed`, `ruff clean`, `mypy 0 errors`.
- Risk: leaving dual-write and DB-primary half-open weakens later auditability.

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
- Baseline snapshot for this gate: `1590 passed, 5 failed (DB-pre-existing), ruff clean`.
