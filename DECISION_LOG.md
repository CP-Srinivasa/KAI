# DECISION_LOG.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close)`
- next_required_step: `PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`
- baseline: `1519 passed, ruff clean`

## Canonical Decisions

### D-53 (2026-03-22): PH4A formally closed
### D-54 (2026-03-22): PH4B opened - overlap-first
### D-55 (2026-03-22): PH4B contract frozen (§68)
### D-56 (2026-03-23): OpenAI selected as single-provider
### D-57 (2026-03-23): PH4B execution passed (paired=69, MAE=3.13)
### D-58 (2026-03-23): Review-first continuation
### D-59 (2026-03-23): Root cause: keyword coverage blindness
### D-60 (2026-03-23): PH4B closed - PH4C opened
### D-61 (2026-03-23): PH4C complete - 42% zero-hit confirmed
### D-62 (2026-03-23): PH4C closed - PH4D opened
### D-63 (2026-03-23): PH4D complete - targeted keyword expansion delivered
### D-64 (2026-03-23): PH4D closed - diminishing returns
### D-65 (2026-03-23): PH4E opened - scoring calibration audit
### D-66 (2026-03-23): PH4E complete - defaults-by-design identified
### D-67 (2026-03-23): PH4E formally closed

### D-68 (2026-03-23): PH4F opened - diagnostic-only input completeness audit
- Constraint: no rule changes, no scoring changes, no threshold changes.
- Contract: `docs/contracts.md §74`.

### D-69 (2026-03-23): PH4F execution complete
- Finding: `RuleAnalyzer.analyze()` is not the production Tier-1 path.
- Finding: Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py`.
- Finding: `actionable` missing in `69/69` paired docs.
- Finding: `market_scope` unknown in `69/69` paired docs.
- Finding: `tags` empty in `69/69` paired docs.
- Finding: `relevance_score` default-floor in `56/69` paired docs.
- Consequence: PH4F enters closeout review mode; no intervention before review.

### D-70 (2026-03-23): PH4F ready to close; PH4G candidate selected
- Decision: PH4F can be formally closed after results review.
- Candidate next sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE` (narrow and intervention-minimal).
- Next required step: `PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`.
