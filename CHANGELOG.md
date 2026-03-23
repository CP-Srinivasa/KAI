# CHANGELOG.md

## 2026-03-23 - PH4F closed (D-70), PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE opened (D-71)

### PH4F Findings (locked — §74 frozen anchor)

| Field | Gap in paired set (69 docs) | Notes |
|---|---|---|
| `actionable` | 69/69 missing (100%) | Hard False in all non-Tier-3 paths |
| `market_scope` | 69/69 unknown | Fallback/internal have no scope inference without keywords |
| `tags` | 69/69 empty | No keyword matches → no tag output |
| `relevance_score` | 56/69 at default floor (81.2%) | 0.0 on no keyword match + empty metadata |

- Root cause: **structural fallback-path under-specification** — not a scoring formula issue.
- LLM-layer coverage: no triggering gap. Gap is `provider=None` → fallback → hard defaults.

### PH4G Definition (§75 frozen)

- Sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE`
- Scope: narrow fallback-path enrichment for top-3 PH4F field gaps.
- Constraints: no scoring formula changes · no threshold changes · ≤3 fields per iteration · measurement-first.

### Guardrails

- No scoring formula, threshold, provider, or runtime changes.
- PH4G is narrow and measurement-first.
- Baseline remains `1519 passed`, `ruff clean`.

## 2026-03-23 - PH4E closed (D-67), PH4F_RULE_INPUT_COMPLETENESS_AUDIT opened (D-68)

### PH4E Findings (locked — §73 frozen anchor)

| Field | Contribution to priority gap | Rule-default rate |
|---|---|---|
| relevance_score | 41.2% | 81.2% of docs return 0.0 (no keyword match) |
| impact_score | 32.6% | always 0.0 (needs LLM) |
| novelty_score | 26.1% | always 0.5 (needs LLM) |
| actionable | — | never set by rule path (needs LLM) |

- Root cause: **defaults by design** — RuleAnalyzer (`app/analysis/rules/rule_analyzer.py` lines 13-18) explicitly documents these as LLM-dependent.
- Classification: architectural input completeness gap, not score formula miscalibration.

### PH4F Definition (§74 frozen)

- Sprint: `PH4F_RULE_INPUT_COMPLETENESS_AUDIT`
- Scope: audit which rules fail to populate relevance_score, impact_score, actionable; audit LLM-layer coverage.
- Constraints: no rule changes · no scoring changes · no threshold changes.

### Guardrails

- No rule/scoring/threshold/provider/source/model/runtime interventions were introduced.
- PH4F remains diagnostic-only.
- Baseline remains `1519 passed`, `ruff clean`.
