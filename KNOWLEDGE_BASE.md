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
- PH4G is formally closed and frozen as §75 intervention anchor.

## PH4H Definition Focus

- Central policy question: `I-13` rule-only ceiling vs fallback actionability.
- PH4H is diagnostic/review-first and remains non-intervention until contract freeze.
- No direct `I-13` change is allowed before freeze and explicit policy rationale.

## Active Gate

- current sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition)`
- next required step: `PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
- contract: `docs/contracts.md §76` (active definition; freeze pending)
- constraints: no direct `I-13` change before policy freeze
