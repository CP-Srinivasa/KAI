# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4F_RULE_INPUT_COMPLETENESS_AUDIT | Next: PH4F_EXECUTION_START | Baseline: 1519 passed, ruff clean

## PH4E Closeout Evidence

- `relevance_score` contribution to priority gap: `41.2%`
- `impact_score` contribution: `32.6%`
- `novelty_score` contribution: `26.1%`
- rule `relevance_score=0` in `81.2%` of paired docs
- rule `actionable` never set in paired docs

## Interpretation

- Primary issue is rule-input completeness, not score calibration tuning.
- PH4F is therefore scoped as diagnostic-only input completeness audit.

## PH4F Execution Findings (D-69 — 2026-03-23)

### Per-Path Input Gap Map

| Field | RuleAnalyzer (test-only) | Pipeline Fallback | InternalModel (Tier 2) | External LLM (Tier 3) |
|---|---|---|---|---|
| relevance_score | 0.0 on no keyword match | 0.0 on empty doc + no metadata | heuristic (density-based) | LLM semantic |
| impact_score | **hard 0.0** | heuristic (max 0.35) | heuristic (max 0.30) | LLM |
| novelty_score | **hard 0.5** | age-based (0.6→0.25) | age-based (0.55→0.25) | LLM |
| actionable | **hard False** | **hard False** | **hard False** | LLM |
| sentiment | **hard NEUTRAL** | **hard NEUTRAL** | **hard NEUTRAL** | LLM |

**Note**: `RuleAnalyzer.analyze()` is NOT used in production pipeline — only in tests. Production rule path = `_build_fallback_analysis()`.

### LLM-Layer Coverage Audit

- LLM triggered when: `provider is not None` AND `run_llm=True` AND no API exception.
- `pipeline/service.py` correctly gates: `run_llm=provider is not None` — no triggering gap.
- `run_llm=False` in 2 CLI paths by design: companion-eval and route-benchmark.
- When LLM raises exception → falls back to `_build_fallback_analysis()` → `actionable=False`.
- **Gap is structural**: `provider=None` means fallback; no heuristic path provides `actionable=True`.

### Top-3 Missing Input Pathways (priority-ranked)

1. **`actionable`** — 100% hard False in all non-Tier-3 paths. Weight: 0.15 + 1 point bonus. Zero heuristic coverage.
2. **`relevance_score`** — 81.2% zero in paired set. RuleAnalyzer: 0.0 on no keyword match. Fallback: 0.0 with empty docs.
3. **`impact_score`** — RuleAnalyzer hard 0.0; fallback/internal heuristics cap at 0.30–0.35 vs. full LLM range.

### PH4G Scope Recommendation

1. Ensure `InternalModelProvider` is always the minimum provider (not `None`) when no external key configured.
2. Evaluate `actionable` heuristic in InternalModelProvider (e.g., keyword-confluence threshold).
3. Improve `relevance_score` fallback for docs with no keyword matches (metadata-based floor).

## PH4F Closeout Evidence (locked — D-68)

- actionable: missing `69/69` paired docs (100%)
- market_scope: unknown `69/69` paired docs (100%)
- tags: empty `69/69` paired docs (100%)
- relevance_score: at default floor `56/69` paired docs (81.2%)
- Production Tier-1 path = `_build_fallback_analysis()` (NOT `RuleAnalyzer.analyze()`)
- LLM triggering gap: none — gap is structural (`provider=None` → fallback)

## Active Gate

- current sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE`
- next required step: `PH4G_EXECUTION_START`
- contract: `docs/contracts.md §75` (definition frozen)
- constraints: ≤3 fields per iteration · measurement-first · no scoring formula changes.
