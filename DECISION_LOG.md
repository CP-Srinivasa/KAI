

## Current State (2026-03-24)


- current_phase: `PHASE 5 (active)`


- current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (closed D-97)`


- next_required_step: `STRATEGIC_HOLD -- no new sprint until alert-precision + paper-trading positive`


- baseline: `1449 passed, ruff clean, mypy 0 errors`


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




- `RequestGovernanceMiddleware` wired into `app/api/main.py`.


- Body-size limit (HTTP 413) enforced at middleware layer.


- `Retry-After` header added to HTTP 429 responses.


- 4 new `AppSettings` governance fields documented in `.env.example`.








- Commit `c498ca4` is the last clean governance anchor.


- Utility evidence confirmed: watchlist overlap 52%, corr=0.56, priority delta +3.1.


- No code changes (diagnostic-only sprint).


### D-85 (2026-03-24): V-4 Dual-Write closeout prioritized before PH4L


- Operator decision: close V-4 (N-4 Dual-Write) before opening PH4L.


- Rationale: opening PH4L before closing V-4 would mix product-quality work with technical instability.


### D-86 (2026-03-24): V-4 Dual-Write + DB-primary closeout complete


- `app/orchestrator/trading_loop.py`: `run_cycle()` writes `TradingCycleRecord` + `PortfolioStateRecord` via `session_factory` (dual-write, non-fatal on DB error).


- `app/execution/portfolio_read.py`: `build_portfolio_snapshot()` queries `PortfolioStateRecord` when `session_factory` provided; falls back to JSONL on no-record or DB error.




- N-4 formally closed in SPRINT_LEDGER.


- baseline: `1449 passed, ruff clean, mypy 0 errors`


### D-87 (2026-03-24): Phase-4 closeout draft prepared (superseded by D-88 gate)


- Full arc PH4A-PH4K (11 sprints) + V-4 Phase 1-3 was documented as closeout-ready.


- Decision context: Phase 4 was assessed as closeout-ready; PH4L not mandatory before closeout.


- Rationale: opening PH4L would weaken phase boundary clarity; cumulative impact is well-documented and utility-validated (PH4K).


- Policy anchor preserved: I-13 permanent (`actionable` = LLM-only).


- Next step in this draft was replaced by D-88 closeout gate sequencing.




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




- PH5A results review accepted: reliability baseline established.


- Key finding: LLM error proxy rate 27.5% (19/69 docs) is the main Phase-5 gap.




- PH5B sprint defined: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS`.


- Objective: cluster and classify the 19 LLM-error-proxy documents; identify root causes.




- Guardrail: PH5C must not be opened before PH5B review is closed.


- baseline: `1449 passed, ruff clean, mypy 0 errors`


### D-93 (2026-03-24): PH5B execution complete -- root cause identified


- PH5B cluster analysis script executed on 69-doc Tier3 dataset.


- 19 proxy docs analysed; single dominant cluster: EMPTY_MANUAL (19/19, 100%).


- Root cause: all 19 proxy docs are source=Manual with content="Comments" (8 bytes).


- LLM response is correct -- no content to analyse; low priority/relevance/scope is correct output.


- This is NOT a model failure; it is a data quality / ingestion gap.


- Recommendation (high priority): FILTER_BEFORE_LLM -- skip LLM for em


 LLM-error-proxy cases belong to the EMPTY_MANUAL cluster.


- Root cause is empty/manual placeholder content, not model failure.


- Next required step is PH5C_STATUS_FREEZE.


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


- baseline: 1609 passed, ruff clean


### D-96 (2026-03-24): PH5C governance reconciliation is canonical


- PH5B findings are accepted and PH5B remains closed.


- `EMPTY_MANUAL` is confirmed as the root cause of the PH5B low-signal cluster.


- PH5C is the intended next sprint; rollback to PH5B is rejected.


- Governance drift between PH5B-close and PH5C-active states must be resolved in favor of PH5C.


- Current sprint is set to `PH5C_FILTER_BEFORE_LLM_BASELINE (frozen ÃÂï¿½85)`.


- Next required step is `PH5C_STATUS_FREEZE`.


- Baseline unified to `1609 passed, ruff clean`.


### D-97 (2026-03-24, superseded): PH5C pending final freeze top state
- Historical note only: this interim top-state was later superseded by D-97 strategic hold.
- PH5B remains closed and accepted.
- PH5C is the intended next sprint.
- There is still a governance conflict between PH5C execution-ready and PH5C status-freeze states.
- Do not start PH5C execution yet.
- Resolve PH5C top-state conflict first and complete PH5C_STATUS_FREEZE.
- Baseline remains 1609 passed, ruff clean.

### D-96 (2026-03-24): PH5C execution complete -- stub filter baseline established
- Script executed: scripts/ph5c_stub_filter_baseline.py
- 69 tier3 docs analysed; 30 docs below threshold (content_len <= 50 bytes).
- Recommended threshold: 50 bytes (consistent across 10-100 byte range).
- Proxy catch rate: 100.0% (19/19) -- all proxy docs caught by filter.
- False positives: 10 non-proxy stubs (also empty -- safe to filter).
- Short-finance docs (keep): 3 docs have finance keywords despite short content.
- Projected proxy rate after filter: 0.0% (from 27.5%).
- Artifacts: artifacts/ph5c/ph5c_stub_filter_baseline.json + ph5c_operator_summary.md
- Next step: PH5C results review and close.

### D-97 (2026-03-24): Strategic hold -- Phase-5 and companion-ML infrastructure frozen
- PH5C results accepted: conservative_placeholder_skip recommended.
  - Aggressive rule: 30 flagged, recall=100%, FP=11, proxy_rate_after=0.0%
  - Conservative rule: 11 flagged, recall=58%, FP=0, proxy_rate_after=11.6%
  - Recommended: conservative rule (zero false positives, safe first path).
- Strategic hold decision: no new sprints, decisions, or invariants in the
  companion-ML / signal-reliability infrastructure until the following
  conditions are met:
    1. Alert precision shows a clearly positive finding.
    2. Paper-trading metrics show a clearly positive finding.
- Rationale: further model-infrastructure investment without positive signal
  metrics creates governance overhead without measurable product value.
- PH5D (filter implementation) and all subsequent Phase-5 sprints are
  BLOCKED until the hold is lifted by the operator.
- Phase 5 status: HOLD.
- This decision is not reversible by the AI; only the operator can lift the hold.

### D-98 (2026-03-24): Alert Hit Rate prioritised as first quality metric
- No new feature work until Alert Hit Rate is computable for 50+ alerts.
- Definition: Alert Hit Rate = alerts where predicted signal materialised / total alerts sent.
- An 'alert' is any document dispatched via the alert pipeline (Telegram / email).
- A 'hit' requires post-hoc annotation: operator confirms signal outcome within a defined window.
- Minimum dataset: 50 annotated alerts required before the metric is meaningful.
- Prerequisite infrastructure:
    1. Structured alert log (alert_id, document_id, timestamp, asset, direction, priority).
    2. Outcome annotation store (alert_id, outcome: hit|miss|inconclusive, annotated_at).
    3. Metric computation script: alert_hit_rate = hits / (hits + misses).
- All companion-ML / Phase-5 feature work remains on hold (D-97).
- This decision supersedes D-97 in priority order: D-98 defines the condition
  under which feature work may resume.
- Next sprint (when authorised): AHR-1 -- Alert Hit Rate Infrastructure.

### D-99 (2026-03-24): No new sprint contract documents
- Decisions are documented as short code comments or 3-line entries in DECISION_LOG.
- No standalone contract documents (docs/contracts.md, sprint-specific .md files) for new work.
- Existing contract docs remain as historical reference but are not maintained.

### D-100 (2026-03-24): AHR-1 Alert Hit Rate Infrastructure — CLOSED
- Sprint AHR-1 delivered the missing outcome annotation layer.
- Delivered:
    1. `app/alerts/audit.py`: AlertOutcomeAnnotation dataclass (hit/miss/inconclusive),
       append_outcome_annotation(), load_outcome_annotations().
    2. `app/alerts/hit_rate.py`: build_outcomes_from_records() gains annotations param;
       manual annotations resolve is_hit without live price data;
       price data takes precedence when both are present.
    3. `app/cli/main.py`: `alerts annotate` command for operator annotation;
       `alerts hit-rate` auto-loads alert_outcomes.jsonl.
    4. 14 new tests (1098 total pass).
- Infrastructure prerequisites for D-98 are now satisfied.
- Remaining gate: collect 50+ annotated alerts before feature-work resumes.
- Status: CLOSED.

### D-101 (2026-03-24): Priority MAE and LLM-Error-Proxy accepted as production metrics
- Priority MAE=3.13 and LLM-Error-Proxy=27.5% are accepted as known limitations.
- These are production metrics improved through operation and real data, not further internal sprints.

### D-102 (2026-03-24): Persona/avatar/speech stubs extracted
- app/persona/, app/messaging/avatar_*, app/messaging/speech_* confirmed fully removed.
- Voice and avatar are not trading-signal product; no re-integration planned.

### D-103 (2026-03-24): CLI reduced to 5 core commands
- Default CLI (`app`): ingest rss, pipeline run, query analyze-pending, alerts evaluate-pending, alerts send-test.
- All other commands remain accessible via `full_app` for backward compatibility.

### D-104 (2026-03-24): actionable=0 permanent in Tier1/keyword fallback (I-13 reaffirmed)
- actionable=0 is accepted as permanent state in Tier1/keyword-only fallback path.
- Focus is on LLM-driven alerts with real signal quality, not Tier1 optimisation.

### D-105 (2026-03-24): 30-day production gate for trading-signal work
- Review date: 2026-04-23 (30 days from 2026-03-24).
- After a real 7-day ingestion run with LLM analysis: if alert_audit.jsonl has <5 triggered alerts OR alert precision <30%, stop trading-signal work.
- On stop: focus only on data quality (feeds, keywords, spam-filter) and do not introduce new architecture.

### D-106 (2026-03-24): docs/ auf lebende Architektur reduziert
- Lebend: CLAUDE.md + docs/contracts.md. Alles andere → docs/archive/ (34 Dateien, inkl. intelligence_architecture.md).
- Keine historischen Docs mehr im aktiven Pfad.
