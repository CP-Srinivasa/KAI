# DECISION_LOG.md

## Current State (2026-03-24)

- current_phase: `PHASE 4 (active, with technical stabilization needed)`
- current_sprint: `V4_DUAL_WRITE_AND_DB_PRIMARY_CLOSEOUT (closed)`
- next_required_step: `Choose PH4L definition or Phase 4 closeout`
- baseline: `1604 passed, ruff clean, mypy 0 errors`

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
### D-69: PH4H Option B (I-13 permanent, actionable=LLM-only)
### D-74/75: PH4H closed (S76)
### D-76/78: PH4I market_scope enrichment (S77)
### D-79: PH4J closed (tags enrichment verified)

### D-80 (2026-03-23): PH4K execution complete
- PH4K produced utility artifacts on the 69 paired documents.
- fallback_tags_populated_docs: `69/69`.
- watchlist_overlap_docs: `36/69 (52.17%)`.
- corr(tag_count, tier3_priority): `0.5564`.
- mean_tier3_priority with overlap vs without overlap: `5.4444` vs `2.3333`.
- DB-failure track remains explicitly separated from PH4K interpretation.

### D-81 (2026-03-23): PH4K moved to results-review mode
- PH4K remains active until review and closeout are completed.
- No PH4L opening before PH4K review closes.
- Next required step set to `PH4K_RESULTS_REVIEW_AND_CLOSE`.

### D-82 (2026-03-23): mypy 42 pre-existing errors resolved
- Root cause: `app.agents.mcp_server` lacked `__all__` after MCP-module-split (Sprint 43).
- Fix: explicit `__all__` added to `mcp_server.py` listing all re-exported symbols.
- `get_handoff_collector_summary` `handoff_path` made optional (None-safe).
- 13 unused `type: ignore` comments removed from `telegram_bot.py`.
- Result: `mypy app/ --ignore-missing-imports` 0 errors.

### D-83 (2026-03-23): Sprint 44 — Operator API Hardening implemented
- `RequestGovernanceMiddleware` wired into `app/api/main.py`.
- Body-size limit (HTTP 413) enforced at middleware layer.
- `Retry-After` header added to HTTP 429 responses.
- 4 new `AppSettings` governance fields documented in `.env.example`.
- 23 new tests added. `§80` contract added to `docs/contracts.md`.

### D-84 (2026-03-24): PH4K formally closed as clean governance anchor
- Commit `c498ca4` is treated as the last explicitly referenced clean governance anchor.
- PH4A through PH4K are complete enough that PH4L is not urgent.
- Immediate focus shifted from new Phase-4 scope to technical stabilization.

### D-85 (2026-03-24): V-4 dual-write / DB-primary closeout prioritized before PH4L
- Recommended next sprint: `V4_DUAL_WRITE_AND_DB_PRIMARY_CLOSEOUT`.
- Confirmed technical WIP is concentrated in `app/execution/portfolio_read.py`, `app/orchestrator/trading_loop.py`, and related tests.
- Decision rationale: opening PH4L before closing V-4 would mix product-quality work with technical instability and reduce auditability.

### D-86 (2026-03-24): V-4 Dual-Write + DB-primary closeout complete
- `app/orchestrator/trading_loop.py`: `run_cycle()` writes `TradingCycleRecord` + `PortfolioStateRecord` via `session_factory` (dual-write, non-fatal on DB error).
- `app/execution/portfolio_read.py`: `build_portfolio_snapshot()` queries `PortfolioStateRecord` when `session_factory` provided; falls back to JSONL on no-record or DB error.
- 14 new tests: 6 dual-write (`test_trading_loop_dual_write.py`) + 8 DB-primary (`test_portfolio_snapshot_db_primary.py`).
- RF-4 promoted to `phase-3-complete` in RISK_REGISTER.
- Baseline: `1604 passed`, `ruff clean`, `mypy 0 errors`.
