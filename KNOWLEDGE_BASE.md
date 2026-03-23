# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close) | Next: PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION | Baseline: 1519 passed, ruff clean

## PH4E Closeout Evidence (frozen)

- `relevance_score` contribution to priority gap: `41.2%`
- `impact_score` contribution: `32.6%`
- `novelty_score` contribution: `26.1%`
- Rule `relevance_score=0` in `81.2%` of paired docs
- Rule `actionable` never set in paired docs

## PH4F Execution Findings (D-69)

- `RuleAnalyzer.analyze()` is not the production Tier-1 path.
- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py`.
- `actionable` missing: `69/69`
- `market_scope` unknown: `69/69`
- `tags` empty: `69/69`
- `relevance_score` default-floor: `56/69`

## Interpretation

- Primary issue remains input completeness on fallback-tier outputs.
- Next leverage point is narrow fallback-path input enrichment.
- PH4F remains diagnostic-only until formal closeout review.

## Active Gate

- current sprint: `PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close)`
- next required step: `PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`
- contract: `docs/contracts.md §74` (active; execution complete)
- constraints: diagnostic-only · no scoring/rule/threshold/provider/source/model changes.
