# CHANGELOG.md

## 2026-03-23 - PH4I formally closed (D-78); PH4J candidate defined

- PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT formally closed.
- S77 is now a frozen immutable anchor.
- PH4J_FALLBACK_TAGS_ENRICHMENT defined as next sprint candidate (PH4F: tags empty 69/69).
- New baseline: 1551 passed, ruff clean.
- Governance state advanced to:
  - current_sprint = PH4J_FALLBACK_TAGS_ENRICHMENT (candidate)
  - next_required_step = PH4J_DEFINITION_AND_CONTRACT_FREEZE

---

## 2026-03-23 - PH4I execution complete (I3+I4); market_scope enriched in fallback path

- `_fallback_market_scope()` extended with PH4I enrichment signals:
  - `document.crypto_assets` length -> CRYPTO votes
  - `document.tickers` length -> EQUITIES votes
  - Title keyword scan (bitcoin, ethereum, crypto, defi, etc.) -> CRYPTO signal
- 13 new tests added (1551 total; +13 from 1538 baseline).
- ruff clean confirmed.
- Before: market_scope UNKNOWN 69/69 (PH4F finding).
- After: crypto_assets/tickers/title keywords now resolve UNKNOWN to CRYPTO/EQUITIES where signals exist.
- Governance state advanced to:
  - `current_sprint = PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (ready to close)`
  - `next_required_step = PH4I_CLOSE_AND_PH4J_DEFINITION`

---

## 2026-03-23 - PH4I contract frozen (§77 D-77); execution-ready

- §77 contract frozen: scope = market_scope enrichment only in `_build_fallback_analysis()`.
- No scoring changes, no I-13 conflict, no actionable expansion.
- Acceptance criteria locked: market_scope > 0/69; 1538+ passed; ruff clean.
- Governance state advanced to:
  - `current_sprint = PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (execution-ready)`
  - `next_required_step = PH4I_EXECUTION`
- Baseline unchanged: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4H policy review complete; I-13 confirmed permanent; PH4I defined

- PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW formally closed (D-74/D-75).
- Policy decision (D-74): `actionable` is an LLM-exclusive semantic judgment -- Option B selected.
  - Option 1 (relax I-13): rejected -- weakens fail-closed guarantee; no semantic basis.
  - Option 3 (hybrid gate): rejected -- arbitrary threshold; complexity without evidence.
- I-13 invariant confirmed as permanent: `test_rule_only_priority_ceiling_is_at_most_five`.
- S76 frozen as immutable anchor.
- PH4I defined (D-76): `PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT` -- market_scope unknown 69/69 (PH4F finding).
- S77 opened as active-definition contract.
- Governance state advanced to:
  - `current_sprint = PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (active definition)`
  - `next_required_step = PH4I_CONTRACT_AND_ACCEPTANCE_FREEZE`
- Baseline unchanged: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4G formally closed; PH4H opened in active definition mode

- PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE formally closed and frozen as Â§75 anchor.
- PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW opened as active definition sprint.
- Governance state advanced to:
  - `current_sprint = PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition)`
  - `next_required_step = PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
- Central policy question fixed for freeze: `I-13` rule-only ceiling vs fallback actionability.
- Baseline reconfirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4G execution complete and moved to ready-to-close gate

- PH4G execution completed; sprint remains active in closeout mode.
- Relevance-floor fallback intervention retained.
- Actionable-heuristic intervention reverted due `I-13` ceiling constraint.
- Governance state set to:
  - `current_sprint = PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
  - `next_required_step = PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- PH4H remains candidate-only until PH4G formal closeout is recorded.
- Baseline reconfirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - [superseded] Premature PH4G closeout/opening record

- PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE formally closed.
- Â§75 is now a frozen immutable anchor.
- Retained: relevance-floor fallback intervention.
- Reverted: actionable heuristic (I-13 constraint â€” rule-only priority ceiling â‰¤ 5).
- PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW opened (D-70, Â§76).
- PH4H is review-only: no code changes, no I-13 relaxation before policy decision.
- Governance state advanced to:
  - `current_sprint = PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (superseded draft state)`
  - `next_required_step = PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE (superseded draft state)`
- Baseline confirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4G execution complete; moved to closeout and PH4H policy review gate

- PH4G execution completed and sprint moved to `ready to close`.
- Relevance-floor fallback intervention is retained.
- Actionable-heuristic intervention was reverted.
- `I-13` policy constraint remains active (`rule-only priority <= 5`).
- Governance state advanced to:
  - `current_sprint = PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
  - `next_required_step = PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- Baseline reconfirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4F closed and PH4G moved to contract-freeze definition

- PH4F formally closed after results review; findings frozen as PH4G intervention anchor.
- Governance state advanced to:
  - `current_sprint = PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE`
  - `next_required_step = PH4G_CONTRACT_AND_ACCEPTANCE_FREEZE`
- PH4G remains definition-only pending contract/acceptance freeze.
- Baseline updated and reconfirmed: `1538 passed`, `ruff clean`.

---

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


