# DECISION_LOG.md

## Current State (2026-03-24)

- current_phase: `PHASE 5 (active) -- Signal Reliability & Trust`
- current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (frozen �85)`
- next_required_step: `PH5C_EXECUTION`
- baseline: `1619 passed, ruff clean, CI green`
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

### D-83 (2026-03-23): Sprint 44 â Operator API Hardening implemented
- `RequestGovernanceMiddleware` wired into `app/api/main.py`.
- Body-size limit (HTTP 413) enforced at middleware layer.
- `Retry-After` header added to HTTP 429 responses.
- 4 new `AppSettings` governance fields documented in `.env.example`.
- 23 new tests added. `Â§80` contract added to `docs/contracts.md`.

### D-84 (2026-03-24): PH4K formally closed â utility review complete
- PH4K_TAG_SIGNAL_UTILITY_REVIEW formally closed; Â§79 is frozen anchor.
- Commit `c498ca4` is the last clean governance anchor.
- Utility evidence confirmed: watchlist overlap 52%, corr=0.56, priority delta +3.1.
- No code changes (diagnostic-only sprint).

### D-85 (2026-03-24): V-4 Dual-Write closeout prioritized before PH4L
- Operator decision: close V-4 (N-4 Dual-Write) before opening PH4L.
- Rationale: opening PH4L before closing V-4 would mix product-quality work with technical instability.

### D-86 (2026-03-24): V-4 Dual-Write + DB-primary closeout complete
- `app/orchestrator/trading_loop.py`: `run_cycle()` writes `TradingCycleRecord` + `PortfolioStateRecord` via `session_factory` (dual-write, non-fatal on DB error).
- `app/execution/portfolio_read.py`: `build_portfolio_snapshot()` queries `PortfolioStateRecord` when `session_factory` provided; falls back to JSONL on no-record or DB error.
- 5 test warnings (coroutine mock) fixed: `AsyncMock` â `MagicMock` for `session.add()`.
- N-4 formally closed in SPRINT_LEDGER.
- baseline: `1619 passed, ruff clean, CI green`

### D-87 (2026-03-24): Phase-4 closeout draft prepared (superseded by D-88 gate)
- Full arc PH4A-PH4K (11 sprints) + V-4 Phase 1-3 was documented as closeout-ready.
- Decision context: Phase 4 was assessed as closeout-ready; PH4L not mandatory before closeout.
- Rationale: opening PH4L would weaken phase boundary clarity; cumulative impact is well-documented and utility-validated (PH4K).
- Policy anchor preserved: I-13 permanent (`actionable` = LLM-only).
- Next step in this draft was replaced by D-88 closeout gate sequencing.
- Governance: PHASE_PLAN.md, SPRINT_LEDGER.md, AGENTS.md, TASKLIST.md, contracts.md Â§82, intelligence_architecture.md all updated.


### D-88 (2026-03-24): Phase-4 closeout gate is now canonical next step
- Recommended next step is to close Phase 4 formally.
- PH4L must not be opened before Phase-4 closeout unless a strong blocker requires it.
- Confirmed facts for this gate: V-4 Dual-Write closed, N-4 closed, working tree clean, baseline 1609 passed and ruff clean.

### D-89 (2026-03-24): Final canonical closeout sync required before any Phase-5 opening
- There is still a governance conflict between "Phase 4 closed" statements and "Phase 4 closeout gate active" statements.
- Phase 4 canonical closeout sync complete (D-87).
- Phase 5 stays blocked until this conflict is resolved across all governance documents.
- This conservative gate state is superseded by D-90.

### D-90 (2026-03-24): Phase-4 canonical closeout accepted; Phase-5 definition opened next
- Phase 4 is treated as canonically closed: `PHASE 4 -- CLOSED (D-87)`.
- `PHASE4_CLOSEOUT_AND_NEXT_PHASE_GATE` is treated as closed.
- Stronger governance evidence accepted: 10-document sync + closeout commit + clean working tree.
- Conservative closeout-gate report is superseded.
- Next required step is now `PHASE5_DEFINITION`.
- Phase 5 should start narrowly with `PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST`.

### D-91 (2026-03-24): PH5A execution completed; sprint moved to results-review mode
- PH5A execution is treated as complete and artifacts are sufficient for a meaningful review.
- Working tree is clean and status report is available in-repo (`status_report.md`).
- Canonical baseline remains `1609 passed` and `ruff clean`.
- Next required step is `PH5A_RESULTS_REVIEW_AND_CLOSE`.
- PH5B must not be opened before PH5A review is formally closed.

### D-92 (2026-03-24): PH5A review complete; PH5B opened ï¿½ Low Signal Cluster Analysis
- PH5A results review accepted: reliability baseline established.
- Key finding: LLM error proxy rate 27.5% (19/69 docs) is the main Phase-5 gap.
- Signature: `priority=1 + relevance=0 + scope=unknown` ï¿½ not a parse error but a quality gap.
- PH5B sprint defined: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS`.
- Objective: cluster and classify the 19 LLM-error-proxy documents; identify root causes.
- Contract: ï¿½84 added to docs/contracts.md.
- Guardrail: PH5C must not be opened before PH5B review is closed.
- baseline: `1619 passed, ruff clean, CI green`

### D-93 (2026-03-24): PH5B execution complete -- root cause identified
- PH5B cluster analysis script executed on 69-doc Tier3 dataset.
- 19 proxy docs analysed; single dominant cluster: EMPTY_MANUAL (19/19, 100%).
- Root cause: all 19 proxy docs are source=Manual with content="Comments" (8 bytes).
- LLM response is correct -- no content to analyse; low priority/relevance/scope is correct output.
- This is NOT a model failure; it is a data quality / ingestion gap.
- Recommendation (high priority): FILTER_BEFORE_LLM -- skip LLM for empty Manual docs.
- 11 additional non-proxy empty docs identified (content <=20 bytes, mixed priority scores).
- Next step: PH5B results review -> close -> define PH5C (FILTER_BEFORE_LLM implementation).
- Artifacts: artifacts/ph5b/ph5b_cluster_analysis.json, artifacts/ph5b/ph5b_operator_summary.md

### D-94 (2026-03-24): PH5B ready to close; PH5C pre-LLM filter recommended
- PH5B cluster analysis is ready to close with artifacts available for review.
- All 19 LLM-error-proxy cases belong to the EMPTY_MANUAL cluster.
- Root cause is empty/manual placeholder content, not model failure.
- Next required step is PH5C_EXECUTION.
- PH5C is defined as a narrow intervention: PH5C_FILTER_BEFORE_LLM_BASELINE.
- No broader model-quality sprint may open before PH5C is assessed.

### D-94 (2026-03-24): PH5B review accepted -- sprint closed
- PH5B cluster analysis results accepted.
- Root cause confirmed: EMPTY_MANUAL (19/19) -- source=Manual, content placeholder only.
- No model failure; gap is at data-quality/ingestion layer.
- Recommendation: FILTER_BEFORE_LLM is the right next intervention.
- PH5B formally closed. TASKLIST PH5B-6 completed.

### D-95 (2026-03-24): PH5C opened -- Stub Document Pre-Filter
- Sprint defined: PH5C_FILTER_BEFORE_LLM_BASELINE
- Objective: implement a pre-LLM stub/empty-content filter in the analysis pipeline.
- Scope: detect and skip documents where content_len < threshold (proposed: 50 bytes).
- Tag skipped documents as stub_document instead of sending to LLM.
- Expected impact: reduce LLM-error-proxy rate from 27.5% toward 0% for stub cases.
- Guardrail: threshold must not exclude valid short Manual docs -- validation required.
- Guardrail: PH5D must not be opened before PH5C review is closed.
- Contract: par85 added to docs/contracts.md.
- baseline: 1615 passed, ruff clean, mypy 0 errors
