# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-23 | Phase 4 active | Sprint: PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition) | Next: PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE | Baseline: 1538 passed, ruff clean

## PH4F Frozen Anchor

- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py`.
- PH4F paired-set findings remain the intervention anchor:
  - `actionable` missing: `69/69`
  - `market_scope` unknown: `69/69`
  - `tags` empty: `69/69`
  - `relevance_score` default-floor: `56/69`

## PH4G Closed Findings

- Relevance-floor fallback intervention was applied successfully.
- Actionable-heuristic intervention was reverted.
- `I-13` keeps rule-only priority capped at `5`.
- PH4G is formally closed and frozen as a policy-constrained intervention anchor.

## Interpretation

- PH4G delivered measurable fallback improvement and is now closed.
- Further fallback enrichment is policy-constrained by I-13.
- PH4H is the correct next sprint for policy clarification before any new intervention.

## Active Gate

- current sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition)`
- next required step: `PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
- contract: `docs/contracts.md §76` (active definition; freeze pending)
- constraints: no direct `I-13` change before policy freeze
