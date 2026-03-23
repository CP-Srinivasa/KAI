# PHASE_PLAN.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close)`
- next_required_step: `PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`
- ph4a_status: `closed (D-53) - immutable baseline anchor`
- ph4b_status: `closed (D-62) - paired_count=69; overlap restored`
- ph4c_status: `closed (D-61) - keyword coverage gaps identified`
- ph4d_status: `closed (D-68) - targeted keyword expansion completed`
- ph4e_status: `closed (D-67) - defaults-by-design root cause confirmed`
- ph4f_status: `closed (D-68) — §74 frozen anchor`
- ph4g_status: `active (definition — D-69) — fallback-path enrichment baseline`
- baseline: `1519 passed, ruff clean`

## PH4F Execution Findings (frozen inputs, 69 paired docs)

- `RuleAnalyzer.analyze()` is not the production Tier-1 path.
- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py`.
- `actionable` is missing in `69/69` paired docs.
- `market_scope` is `unknown` in `69/69` paired docs.
- `tags` are empty in `69/69` paired docs.
- `relevance_score` is default-floor in `56/69` paired docs.

## PH4F Closeout (D-68 — §74 frozen anchor)

- PH4F formally closed. Diagnostic-only — no rule/scoring/threshold changes made.
- Per-field confirmed counts locked: actionable 69/69 · market_scope 69/69 · tags 69/69 · relevance floor 56/69.

## PH4G Active Sprint (definition — D-69)

- sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE`
- contract: `docs/contracts.md §75` (frozen)
- scope: narrow fallback-path enrichment for top-3 PH4F field gaps
  - actionable: add heuristic estimate to fallback path
  - market_scope: improve inference for docs with no keyword matches
  - tags/relevance: add metadata-based floor when keyword hits are zero
- constraints: no scoring formula changes · no threshold changes · ≤3 fields per iteration · measurement-first
- output: baseline measurement → enrichment → MAE re-measurement; PH4H recommendation
