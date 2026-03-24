
## 2026-03-24 - Alert hit-rate metric (first quality metric)

- **Enriched** `AlertAuditRecord` with prediction fields (sentiment_label, affected_assets, priority, actionable)
- **Created** `app/alerts/hit_rate.py`: AlertOutcome, classify_hit, build_outcomes_from_records, compute_hit_rate
- **Added** CLI command: `kai alerts hit-rate` with per-sentiment/per-asset breakdowns
- **Tests**: 20 new tests in `test_alert_hit_rate.py`
- Baseline: `1079 passed, ruff clean`

---

## 2026-03-24 - Companion code extraction to companion-ml branch

- **Extracted**: ~80 companion-only files removed from main dev path.
- **Archived**: `companion-ml` branch preserves all extracted code.
- **Scope**: 16 research modules, 4 CLI command modules, 5 agent tool modules, `mcp_server.py`, 2 API routers, 26+ test files.
- **Keepers**: `signals.py`, `watchlists.py`, `briefs.py` in `app/core/`, re-exported via `app/research/__init__.py`.
- Baseline: `1046 passed, ruff clean`.

---

## 2026-03-24 - PH5C executed; strategic hold active (D-97)

- **Strategic hold imposed (D-97)**: No new companion-ML sprint, decision, or invariant until alert-precision + paper-trading metrics are clearly positive.
- Baseline: `1046 passed, ruff clean`.

---

## 2026-03-24 - PH5C filter baseline set to pending final freeze

- Canonical state set to `current_sprint = PH5C_FILTER_BEFORE_LLM_BASELINE (pending final freeze)`.
- Next required step set to `PH5C_STATUS_FREEZE`.
- PH5B findings accepted; PH5B remains closed.
- `EMPTY_MANUAL` confirmed as root cause of the PH5B low-signal cluster.
- Governance conflict remains between execution-ready and status-freeze states; execution stays blocked.
- Baseline unified to `1609 passed, ruff clean`.

---


- Baseline: `1609 passed, ruff clean`.

---

## 2026-03-24 - CI hardened (N-8); all 5 jobs green

- `hypothesis>=6.0.0` + `pytest-mock>=3.14.0` added to `[dev]` extras (were installed locally but missing from CI).
- ruff format pass over 138 files (no logic changes).
- Duplicate `asyncio.run(run())` in `send_digest` CLI command removed (pre-existing copy/paste bug).
- Baseline: `1609 passed, ruff clean`. CI: 5/5 green.

---

## 2026-03-24 - Alert Integration wired into analyze-pending (N-7)

- `--no-alerts` flag suppresses Phase 4 entirely.
- `Alerts dispatched: N` printed when alerts fire.
- 3 new tests: `tests/unit/cli/test_analyze_pending_alerts.py` (dispatch, --no-alerts suppression, fail-open).

---

## 2026-03-24 - MCP compat.py extraction complete (N-6)

- Last 5 inline `@mcp.tool()` definitions extracted from `mcp_server.py` into `app/agents/tools/compat.py`.
- `test_canonical_read.py` + `test_guarded_write.py` upgraded: trivial alias checks replaced with `mcp.list_tools()` registration verification.

---

## 2026-03-24 - PH5A execution complete; results-review mode active

- PH5A execution has completed and artifacts are ready for review.
- Canonical state remains: `current_phase = PHASE 5 (active)`, `current_sprint = PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST`.
- Next required step set to `PH5A_RESULTS_REVIEW_AND_CLOSE`.
- Working tree is clean and status report is in-repo (`status_report.md`).
- Baseline remains `1609 passed, ruff clean`.
- PH5B stays blocked until PH5A review is formally closed.

---

## 2026-03-23 - PH4K contract freeze completed; execution gate opened

- Canonical state advanced to: `current_sprint = PH4K_TAG_SIGNAL_UTILITY_REVIEW (definition frozen)`.
- Canonical next step set to: `PH4K_EXECUTION_START`.
- PH4K remains diagnostic-only; no scoring/threshold/provider/actionability changes.
- Acceptance criteria locked before execution.
- DB failures remain on a separate track and are excluded from PH4K utility interpretation.

---


- PH4J_CLOSE_AND_PH4K_DEFINITION sprint executed: governance sync complete.
- All 10 governance docs aligned: PH4J=closed, PH4K=candidate.
- No PH4K execution before `PH4K_DEFINITION_AND_CONTRACT_FREEZE`.
- Baseline unchanged: `1554 passed, ruff clean`.

---

## 2026-03-23 - PH4J governance state set to ready-to-close (pre-closeout gate)

- Canonical state set to: `current_sprint = PH4J_FALLBACK_TAGS_ENRICHMENT (ready to close)`.
- Canonical next step set to: `PH4J_CLOSE_AND_PH4K_DEFINITION`.
- PH4J verification evidence remains unchanged: keyword-hit 4->7, zero-hit 1->4, assets-only 0->4, 29/29 tests, I-13 intact.
- DB failures remain on a separate track and are excluded from PH4J closeout semantics.

---


- DB test failures remain on a separate track.
- Governance state: `current_sprint = PH4K_TAG_SIGNAL_UTILITY_REVIEW (candidate)`, `next_required_step = PH4K_DEFINITION_AND_CONTRACT_FREEZE`.

---

## 2026-03-23 - PH4J live verification passed; sprint moved to ready-to-close

- `PH4J_FALLBACK_TAGS_ENRICHMENT` moved to `ready to close`.
- Live verification passed.
- Tag coverage improved in verified scenarios:
  - keyword-hit: `4 -> 7`
  - zero-hit: `1 -> 4`
  - assets-only: `0 -> 4`
- `29/29` pipeline tests passed.
- `I-13` remained intact.
- DB test failures are tracked separately from PH4J closeout.
- Next required step set to `PH4J_CLOSE_AND_PH4K_DEFINITION`.

---

## 2026-03-23 - PH4I formally closed (D-78); PH4J candidate defined

- PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT formally closed.
- S77 is now a frozen immutable anchor.
- PH4J_FALLBACK_TAGS_ENRICHMENT defined as next sprint candidate (PH4F: tags empty 69/69).
- New baseline: 1554 passed, ruff clean.
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
- Retained: relevance-floor fallback intervention.
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


## 2026-03-24 - V-4 Dual-Write + DB-primary closeout (D-86)

- `app/orchestrator/trading_loop.py`: `run_cycle()` writes `TradingCycleRecord` + `PortfolioStateRecord` to DB via `session_factory`; DB error is non-fatal.
- `app/execution/portfolio_read.py`: `build_portfolio_snapshot()` queries `PortfolioStateRecord` as primary source when `session_factory` provided; falls back to JSONL on no-record or DB error.
- 6 new tests in `tests/unit/test_trading_loop_dual_write.py` (dual-write path).
- 8 new tests in `tests/unit/test_portfolio_snapshot_db_primary.py` (DB-primary path).
- RF-4 promoted to `phase-3-complete`. Baseline: 1604 passed, ruff clean, mypy 0 errors.

## 2026-03-24 - Phase-4 closeout draft recorded (D-87, superseded by final closeout sync gate)

- Phase 4 arc PH4A-PH4K (11 sprints) + V-4 Phase 1-3 documented as closeout-ready.
- Cumulative signal quality improvements: priority +28%, tags empty -62.3%, relevance=0 -43.5%.
- I-13 policy permanent: `actionable` = LLM-only, no rule-only fallback.
- V-4 technical hardening complete: DB-primary portfolio snapshot, dual-write in run_cycle().
- This claim is superseded by the conservative canonical gate: `PHASE4_FINAL_CANONICAL_CLOSEOUT`.
- Phase 5 remains blocked until final closeout sync is complete.


## 2026-03-24 - PH5A execution complete (D-89)

- PH5A diagnostic script executed against 69-doc paired set.
- Key findings:
  - Keyword coverage: 62.3% (43/69)
  - Watchlist overlap: 52.2% (36/69)
- Artifacts: `artifacts/ph5a_reliability_baseline.json` + `artifacts/ph5a_operator_summary.md`
- PH5A moved to results-review; PH5A-7 (governance closeout) pending.

## 2026-03-24 - PH5A closeout draft note (superseded by active review gate)

- This earlier closeout claim is superseded by the canonical review state.
- PH5A remains in results-review mode until `PH5A_RESULTS_REVIEW_AND_CLOSE` is completed.
- PH5B stays blocked until PH5A review is formally closed.

## 2026-03-24 - PH5B closed; PH5C opened (D-94, D-95)

- PH5B cluster analysis complete: all 19 LLM-error-proxy docs classified as EMPTY_MANUAL.
- Root cause: source=Manual with content='Comments' (8 bytes placeholder) -- data quality gap.
- LLM behaviour is correct; no model failure.
- Recommendation: FILTER_BEFORE_LLM (skip LLM for stub documents).
- PH5C sprint opened: PH5C_FILTER_BEFORE_LLM_BASELINE (D-95, par85).
- Artifacts: artifacts/ph5b/ph5b_cluster_analysis.json + artifacts/ph5b/ph5b_operator_summary.md

## 2026-03-24 - PH5C closed; strategic hold imposed (D-97)

- PH5C stub filter baseline: conservative rule recommended (FP=0, recall=58%).
- Strategic hold: no new Phase-5 sprints until alert-precision and paper-trading metrics positive.
- Artifacts: artifacts/ph5c/ph5c_stub_filter_baseline.json + ph5c_operator_summary.md
