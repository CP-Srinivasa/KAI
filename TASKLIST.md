# TASKLIST.md

## Current State

- current_phase: `PHASE 6 (active) — V-4 Phase 3`
- current_sprint: `SPRINT_45_V4_DB_PRIMARY_PORTFOLIO_SNAPSHOT`
- status: `closed (D-85, §81)`
- next_required_step: `Define next sprint (PH4L or Phase 5 closeout)`
- baseline: `1609 passed, ruff clean, mypy 0 errors`

## Active Tasks

### SPRINT_45_V4_DB_PRIMARY_PORTFOLIO_SNAPSHOT

- [x] S45-1 Session-factory refactor: TradingLoop accepts `async_sessionmaker` (not `AsyncSession`)
- [x] S45-2 `_write_db()`: session-per-cycle via `async with session_factory()`
- [x] S45-3 `_write_db()`: write `PortfolioStateRecord` when `fill_simulated=True`
- [x] S45-4 `build_portfolio_snapshot()`: `session_factory` param, query latest `PortfolioStateRecord`
- [x] S45-5 `_build_snapshot_from_portfolio_state()`: reconstruct positions from `positions_json`
- [x] S45-6 JSONL fallback when no DB record or factory is None; DB errors non-fatal
- [x] S45-7 Update `test_trading_loop_dual_write.py` to use session_factory mocks
- [x] S45-8 New `test_portfolio_snapshot_db_primary.py` (8 tests, Phase 3)
- [x] S45-9 Update `test_db_first_portfolio_read.py` to use session_factory API
- [x] S45-10 Update SPRINT_LEDGER.md and close Sprint 45

## Closed Tasks (Summary)

- SPRINT_45_V4_DB_PRIMARY_PORTFOLIO_SNAPSHOT - closed (2026-03-24, D-85, §81)
- PH4K_TAG_SIGNAL_UTILITY_REVIEW - closed (2026-03-24, D-84, §79)
- SPRINT_44_OPERATOR_API_HARDENING - closed (2026-03-23, D-83, §80)
- PH4J_FALLBACK_TAGS_ENRICHMENT - closed
- PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT - closed
- PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW - closed
- PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE - closed
- PH4F_RULE_INPUT_COMPLETENESS_AUDIT - closed
- PH4E_SCORING_CALIBRATION_AUDIT - closed
- PH4D_TARGETED_KEYWORD_EXPANSION_BASELINE - closed
- PH4C_RULE_KEYWORD_COVERAGE_AUDIT - closed
- PH4B_TIER3_COVERAGE_EXPANSION - closed
- PH4A_SIGNAL_QUALITY_AUDIT_BASELINE - closed
