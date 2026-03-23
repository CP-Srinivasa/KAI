# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close) | Next: PH4G_CLOSE_AND_PH4H_POLICY_REVIEW | Baseline: 1538 passed, ruff clean

## PH4F Frozen Anchor

- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py`.
- PH4F paired-set findings remain the intervention anchor:
  - `actionable` missing: `69/69`
  - `market_scope` unknown: `69/69`
  - `tags` empty: `69/69`
  - `relevance_score` default-floor: `56/69`

## PH4G Execution Findings

- Relevance-floor fallback intervention was applied successfully.
- Actionable-heuristic intervention was reverted.
- `I-13` keeps rule-only priority capped at `5`.

## Interpretation

- PH4G delivered measurable fallback improvement.
- Further fallback enrichment is now policy-constrained.
- Next useful step is PH4H policy review, but only after formal PH4G closeout.

## Active Gate

- current sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
- next required step: `PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- contract: `docs/contracts.md §75` (execution complete; closeout pending)
- constraints: no direct `I-13` change before policy review
