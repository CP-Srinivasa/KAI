# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close) | Next: PH4G_CLOSE_AND_PH4H_POLICY_REVIEW | Baseline: 1538 passed, ruff clean

## PH4E Closeout Evidence (frozen)

- `relevance_score` contribution to priority gap: `41.2%`
- `impact_score` contribution: `32.6%`
- `novelty_score` contribution: `26.1%`
- Rule `relevance_score=0` in `81.2%` of paired docs
- Rule `actionable` never set in paired docs

## PH4F Execution Findings (frozen intervention anchor)

- `RuleAnalyzer.analyze()` is not the production Tier-1 path
- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py`
- `actionable` missing: `69/69`
- `market_scope` unknown: `69/69`
- `tags` empty: `69/69`
- `relevance_score` default-floor: `56/69`

## PH4G Execution Findings

- Relevance-floor fallback intervention was applied successfully.
- Actionable-heuristic intervention was reverted.
- `I-13` keeps rule-only priority capped at `5`, which constrains further fallback actionability changes.

## Interpretation

- PH4G delivered measurable fallback-path progress without broad reform.
- Remaining blocker is policy clarity, not immediate implementation depth.
- Next useful step is PH4H policy review before further intervention.

## Active Gate

- current sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active — definition)`
- next required step: `PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
- contract: `docs/contracts.md §76`
- constraints: no direct `I-13` change before policy review; review-only sprint
