# CHANGELOG.md

## 2026-03-23 - Refactoring findings RF-1..RF-7 implemented

### RF-1 CLI/MCP Split
- `app/cli/research.py` extracted from monolithic `main.py` (3400+ lines)
- `app/cli/commands/trading.py`: new `trading_app` Typer group
- `app/agents/tools/canonical_read.py` + `guarded_write.py`: MCP inventory modules
- `main.py` is now a thin registration layer

### RF-2 Working Tree committed
- 3 snapshot commits created for governance docs, code changes, and config files

### RF-3 CORS configurable (prior sprint)
- `APP_CORS_ALLOWED_ORIGINS` env var, `AppSettings.cors_allowed_origins`

### RF-4 DB-based aggregation (Phase 1)
- `TradingCycleRecord` + `PortfolioStateRecord` SQLAlchemy models
- Alembic migration `0007_create_trading_tables.py`
- Dual-write integration pending (Phase 2)

### RF-5 README/Docs Phase-4 update
- README phase status block updated to PH4F closed / PH4G pending
- Sprint and CoinGecko default documented

### RF-6 CoinGecko as default market data provider
- `APP_MARKET_DATA_PROVIDER=coingecko` documented in `.env.example`
- `create_market_data_adapter()` logs WARNING when mock provider used

### RF-7 Test-file splitting
- `tests/unit/cli/` and `tests/unit/mcp/` subpackages created
- 19 new tests added (1538 total, +19 from 1519 baseline)

---

## 2026-03-23 - PH4F execution complete and moved to closeout review

- PH4F execution artifacts generated from frozen paired set (`69` docs).
- Confirmed production Tier-1 path: fallback analysis in `app/analysis/pipeline.py` (not `RuleAnalyzer.analyze()`).
- Confirmed PH4F field gaps:
  - `actionable` missing in `69/69`
  - `market_scope` unknown in `69/69`
  - `tags` empty in `69/69`
  - `relevance_score` default-floor in `56/69`
- Governance state updated to:
  - `current_sprint = PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close)`
  - `next_required_step = PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`
- Baseline unchanged: `1519 passed`, `ruff clean`.

## 2026-03-23 - PH4E closed and PH4F opened (historical)

- PH4E scoring calibration audit formally closed (D-67).
- PH4F (`RULE_INPUT_COMPLETENESS_AUDIT`) opened as diagnostic-only sprint (D-68).
