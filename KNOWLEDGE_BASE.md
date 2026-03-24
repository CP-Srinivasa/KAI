# KNOWLEDGE_BASE.md - KAI Canonical Knowledge Index

> Stand: 2026-03-24 | PHASE 5 (active) -- Signal Reliability & Trust | PH5B active (D-92, §84) | next_required_step: PH5B_EXECUTION | baseline: 1615 passed, ruff clean

## Phase Status

- current_phase: `PHASE 5 (active) -- Signal Reliability & Trust`
- current_sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS (active D-92, §84)`
- next_required_step: `PH5B_EXECUTION`
- phase4_status: `CLOSED (D-87)`
- phase5_guardrail: PH5B active — do not open PH5C before PH5B review closes

## Newly Confirmed Facts

- V-4 Dual-Write is closed.
- N-4 is closed.
- Working tree is clean.
- PH5A execution has already completed.
- Status report is in-repo (`status_report.md`).
- Current technical baseline is `1609 passed` and `ruff clean`.
- Phase 4 has completed a full PH4A-PH4K arc.
- Phase 4 canonical closeout is accepted.
- Strong governance evidence accepted: 10-doc sync + closeout commit + clean working tree.

## Current Assumptions and Decisions

- Assumption: PH5A artifacts are sufficient for a meaningful review.
- Assumption: the next useful step is review, not another execution pass.
- Decision: treat PH5A as execution-complete.
- Decision: move PH5A into results-review mode.
- Decision: do not open PH5B before PH5A review closes.

## PH5B Active

- PH5A closed (D-92): fallback=0%, LLM-error-proxy=27.5%, tag-fill=100%, keyword-cov=62.3%.
- PH5B sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS` — root cause the 27.5% LLM error proxy.
- Next: run `scripts/ph5b_cluster_analysis.py`.

## PH4H Policy Anchor

- Policy choice: Option B
- `I-13` remains enforced.
- `actionable` remains LLM-only.

## PH4J Verification Outcome

- PH4J live verification passed.
- Fallback tags include: `categories`, `affected_assets`, `source_name`, `market_scope.value`.
- Tag improvements: keyword-hit `4->7`, zero-hit `1->4`, assets-only `0->4`.
- `29/29` pipeline tests passed.
- `I-13` remained intact.
- DB test failures remain on a separate track.

## PH4K Closed (D-84)

- Sprint: `PH4K_TAG_SIGNAL_UTILITY_REVIEW`. **Formally closed (D-84).**
- fallback_tags_populated_docs: `69/69`.
- watchlist_overlap_docs: `36/69` (`52.17%`).
- corr(tag_count, tier3_priority): `0.5564`.
- mean_tier3_priority with watchlist overlap: `5.4444`.
- mean_tier3_priority without watchlist overlap: `2.3333`.
- Result: strong utility signal confirmed; results review complete.

## PH4I Frozen Anchor

- `_fallback_market_scope()` enrichment is closed and frozen (`section 77`, `D-78`).
- Baseline snapshot for this gate: `1609 passed, ruff clean`.


