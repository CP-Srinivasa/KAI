# RISK_REGISTER.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close)`
- next_required_step: `PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`
- baseline: `1519 passed, ruff clean`

---

## Active Phase-4 Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R-PH4-010 | PH4F may drift from input-completeness diagnostics into direct rule reform. | high | medium | Keep PH4F diagnostic-only and block intervention edits before closeout review. | open |
| R-PH4-011 | PH4F input analysis may become too broad without strict field separation. | high | medium | Keep outputs field-separated and prioritize only top pathways for closeout. | open |
| R-PH4-012 | Root-cause confidence may be overstated without paired-set evidence trace. | medium | medium | Keep evidence locked to the frozen 69 paired documents and per-field counters. | open |
| R-PH4G-001 | PH4G may become too broad if too many fields are changed at once. | high | medium | Enforce narrow PH4G scope and limit first intervention pass to highest-leverage pathways. | open |
| R-PH4G-002 | Intervention without tight measurement could reduce interpretability. | medium | medium | Require before/after measurements on the same paired set and explicit pathway mapping. | open |

---

## Resolved / Superseded

- PH4B operational blocker (quota) - resolved.
- PH4D regression risk - resolved (`0` regressions).
- PH4D/PH4E governance conflict - resolved.
- PH4E calibration ambiguity - resolved into PH4F diagnostic path.

---

## Refactoring Findings RF-1 .. RF-7 (2026-03-23)

These findings were addressed in a dedicated refactoring session (2026-03-23).

| ID | Titel | Status | Commit |
|---|---|---|---|
| **RF-1** | CLI/MCP monolith split | ✅ implemented | e2949d3, b8c0fad |
| **RF-2** | Working Tree uncommitted | ✅ implemented | f32b147, cbcb34c, dea0ec8 |
| **RF-3** | CORS hardcoded | ✅ implemented (prior) | 4d2cfdd |
| **RF-4** | DB-based aggregation (models + migration) | ✅ partial | 25f84d4 |
| **RF-5** | README/Docs Phase-4 update | ✅ implemented | a089ca7, e86e3aa |
| **RF-6** | CoinGecko default + mock warning | ✅ implemented | faabd6c |
| **RF-7** | Test-file splitting (cli/ + mcp/ submodules) | ✅ implemented | a05f1e7 |

### RF-1 Detail
- `app/cli/commands/trading.py`: new `trading_app` with market-data, paper-portfolio, trading-loop, backtest, decision-journal commands
- `app/cli/research.py`: research commands fully extracted from main.py
- `app/agents/tools/canonical_read.py` + `guarded_write.py`: MCP tool inventory modules
- Backward-compatible: all `trading-bot research <cmd>` commands unchanged

### RF-4 Detail (partial)
Phase 1 complete: ORM models + Alembic migration (0007).
Phase 2 (dual-write in run_cycle) and Phase 3 (DB-primary portfolio snapshot) are pending sprints.

---

## Confirmed Context

- PH4E is formally closed.
- PH4F execution is complete and in review/closeout mode.
- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py` (not `RuleAnalyzer.analyze()`).
- PH4F paired-set findings: actionable missing `69/69`, market_scope unknown `69/69`, tags empty `69/69`, relevance default-floor `56/69`.
- Technical baseline unchanged: `1519 passed`, `ruff clean`.
