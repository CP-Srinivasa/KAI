# Intelligence Architecture

## Current State (2026-03-23)

| Field | Value |
|---|---|
| current_phase | `PHASE 4 (active)` |
| current_sprint | `PH4J_FALLBACK_TAGS_ENRICHMENT (candidate)` |
| next_required_step | `PH4I_CONTRACT_AND_ACCEPTANCE_FREEZE` |
| ph4e_status | `closed (D-67) — scoring calibration audit complete; §73 frozen anchor` |
| ph4f_status | `closed — frozen intervention anchor; §74 frozen anchor` |
| ph4g_status | `closed — relevance floor applied; actionable reverted (I-13); §75 frozen anchor` |
| ph4h_status | `closed (D-75) — Option B: I-13 permanent; actionable LLM-only; §76 frozen anchor` |
| ph4i_status | `active (definition — D-76) — market_scope enrichment; §77 contract` |
| baseline | `1551 passed, ruff clean` |
| ph4b_status | `closed (D-62) — sections 68 and 69 frozen anchors` |
| ph4c_status | `closed — section 70 frozen audit anchor` |
| ph4d_status | `closed — section 71 frozen anchor` |
| ph4e_contract | `docs/contracts.md §73 (closed D-67)` |
| ph4f_contract | `docs/contracts.md §74 (closed)` |
| ph4g_contract | `docs/contracts.md §75 (closed; frozen anchor)` |
| ph4h_contract | `docs/contracts.md §76 (closed D-75 — frozen anchor)` |
| ph4i_contract | `docs/contracts.md §77 (active definition — D-76)` |
| architecture_status | three-tier stack unchanged; PH4A–PH4H closed/frozen anchors (§67–§76); PH4I active definition (§77) |

---

## PH4A/PH4B (closed â€” frozen anchors)

- PH4A closed: `74` audited records, `6.76%` tier-3 coverage, `paired_count=0`. Primary bottleneck: zero tier overlap.
- PH4B closed (D-62): `paired_count=69`, `tier3_coverage=100.0%`, `signal_to_noise=5.80%`, `priority_mae=3.13`.
- PH4B review confirmed root cause: Tier-1 assigns default scores on keyword miss â€” structural keyword coverage blindness.
- Divergence profile: `18` severe (|delta|>=5), `40` moderate, `11` minor; `0.00%` tag overlap.
- PH4A (Â§67) and PH4B (Â§68, Â§69) are immutable comparison anchors. No re-execution permitted.

## PH4C (closed â€” Â§70 frozen audit anchor, D-66)

- Sprint: `PH4C_RULE_KEYWORD_COVERAGE_AUDIT` (diagnostic-only). Formally closed D-66.
- Contract: `docs/contracts.md Â§70` (frozen immutable anchor).
- Execution outcomes (frozen):
  - KeywordEngine indexed terms: `507`
  - hit buckets over 69 paired docs: `29` zero-hit, `27` low-hit, `13` good-hit
  - low-hit documents carry largest average delta (`+3.4`)
  - top missing categories: macro/finance, regulatory/legal, AI/technology

## PH4D (closed â€” Â§71 frozen anchor, D-68)

- Sprint: `PH4D_TARGETED_KEYWORD_EXPANSION_BASELINE` (targeted keyword expansion). Formally closed D-68.
- Contract: `docs/contracts.md Â§71` (frozen immutable anchor).
- Execution outcomes (frozen):
  - scope applied: macro/finance (+24), regulatory/legal (+18), AI/technology (+14)
  - re-measurement (69 paired docs): zero `29â†’26`, low `27â†’25`, good `13â†’18`, regressions `0`
  - index growth: `507â†’555` (+48)
- Zero-hit review (frozen): 26 remaining; 5 true rule gaps Â· 21 correctly low-value noise.

## Phase 4 Interim Review (closed â€” Â§72, D-65/D-66)

- Outcome: keyword expansion reached diminishing returns. Highest-leverage remaining lever: scoring calibration (priority_mae=3.13).
- Decision: `PH4E_SCORING_CALIBRATION_AUDIT` selected as next sprint (D-66).
- Contract: `docs/contracts.md Â§72` (frozen immutable anchor).

## PH4E Closed Sprint (Â§73 frozen anchor â€” D-67)

- Sprint: `PH4E_SCORING_CALIBRATION_AUDIT` (diagnostic scoring audit). **Formally closed D-67.**
- Contract: `docs/contracts.md Â§73` (immutable frozen anchor â€” no re-execution permitted).
- Execution findings (locked):
  - relevance_score: 41.2% of priority gap; 81.2% of docs return 0.0 (no keyword match)
  - impact_score: 32.6% of priority gap; always 0.0 by design (needs LLM)
  - novelty_score: 26.1% of priority gap; always 0.5 by design (needs LLM)
  - actionable: never set by rule path (needs LLM)
- Root cause: **defaults by design** â€” RuleAnalyzer (`app/analysis/rules/rule_analyzer.py` lines 13-18) explicitly documents these as LLM-dependent fields.
- Classification: architectural input completeness gap, not score formula miscalibration.
- Consequence: PH4F audits whether LLM layer is consistently triggered to fill these fields.

## PH4F Closed Sprint (frozen anchor â€” Â§74, D-69)

- Sprint: `PH4F_RULE_INPUT_COMPLETENESS_AUDIT`. **Formally closed.**
- Contract: `docs/contracts.md Â§74` (closed immutable anchor).
- Execution findings (locked):
  - Production Tier-1 path = `_build_fallback_analysis()` in `pipeline.py` â€” NOT `RuleAnalyzer.analyze()`
  - `actionable`: missing 69/69 paired docs (hard False in all non-Tier-3 paths)
  - `market_scope`: unknown 69/69 paired docs (no inference without keyword matches)
  - `tags`: empty 69/69 paired docs (no keyword hits â†’ no tag output)
  - `relevance_score`: at default floor 56/69 docs (81.2%)
- LLM-layer coverage verdict: no triggering gap; gap is `provider=None` â†’ fallback â†’ hard defaults.
- Consequence: PH4G uses PH4F findings as frozen intervention anchor.

## PH4G Closed Sprint (frozen anchor)

- Sprint: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE`. **Formally closed.**
- Contract: `docs/contracts.md §75` (closed; frozen anchor).
- Execution findings (locked):
  - Relevance-floor fallback intervention: **retained** (applied successfully)
  - Actionable heuristic intervention: **reverted** â€” violates I-13 invariant (rule-only priority ceiling max 5)
  - I-13 invariant: `test_rule_only_priority_ceiling_is_at_most_five` enforces priority â‰¤ 5 for rule-only analysis
  - The +1 actionable bonus in `compute_priority()` would push priority to 7, breaching I-13
- Baseline confirmed unchanged: `1551 passed, ruff clean`.

## PH4H Closed Policy Sprint (frozen anchor)

- Sprint: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW`. **Formally closed.**
- Contract: `docs/contracts.md §76` (closed; frozen anchor D-74/75).
- Policy decision: **Option B**.
- `I-13` remains in force as a permanent invariant.
- `actionable` remains **LLM-only**; fallback stays conservative and non-actionable.

## PH4I Active Definition Sprint (relevance/context enrichment)

- Sprint: `PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT`. **Active in definition mode.**
- Contract: `docs/contracts.md §77` (active definition).
- Focus: enrich `market_scope` in the fallback path for better context without changing scoring or actionability.
- Guardrails: no `I-13` change, no actionable expansion, no provider/model/source changes.
- Next required step: `PH4I_CONTRACT_AND_ACCEPTANCE_FREEZE`.

## Design Principle

**Reliability > Speed > Depth**

Every document receives a valid `AnalysisResult`. The pipeline never returns empty scores.
Tier depth scales with available resources â€” the system degrades gracefully, never silently.

KAI must remain operational when no external LLM provider is configured.
OpenAI, Anthropic, and Gemini are amplifiers of quality, not hard runtime prerequisites.

---

## Three-Tier Stack

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Tier 3 â€” External LLM Provider (current default)              â”‚
â”‚  OpenAI / Anthropic / Gemini                                    â”‚
â”‚  Full output: all scores, narrative, actionable classification  â”‚
â”‚  Priority range: 1â€“10  â”‚  Produces SignalCandidates             â”‚
â”‚  Cost: API call per document                                    â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                               â”‚ fallback if unavailable
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Tier 2 â€” Internal Companion Model (Sprint 5 â€” planned)        â”‚
â”‚  Local inference: GGUF / ONNX / vLLM endpoint                  â”‚
â”‚  Subset output: sentiment, relevance, impact (conservative)     â”‚
â”‚  Priority range: 1â€“8   â”‚  Can produce SignalCandidates          â”‚
â”‚  Cost: local compute, no API key required                       â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                               â”‚ fallback if unavailable
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Tier 1 â€” RuleAnalyzer (implemented, Sprint 4C)                â”‚
â”‚  Deterministic: keyword matching + heuristics                   â”‚
â”‚  Conservative output: relevance only, all others at floor       â”‚
â”‚  Priority range: 1â€“5   â”‚  Never produces SignalCandidates       â”‚
â”‚  Cost: zero (no model, no network)                              â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

All three tiers converge on the same downstream contract:
`CanonicalDocument â†’ AnalysisResult â†’ apply_to_document() â†’ research outputs / alert gate`

No downstream consumer branches on provider family to understand the result schema.

---

## Canonical Compatibility Contract

The analysis layer is provider-agnostic at the `AnalysisResult` boundary.

- Tier 1 writes `AnalysisResult` directly (keyword matching + heuristics)
- Tier 2 emits `LLMAnalysisOutput` (same ABC as Tier 3), normalized into `AnalysisResult` by pipeline
- Tier 3 emits `LLMAnalysisOutput`, normalized into `AnalysisResult` by pipeline

Downstream consumers that receive the same contract regardless of source:
- `PipelineResult.apply_to_document()`
- `DocumentRepository.update_analysis()`
- `ResearchBriefBuilder`
- `extract_signal_candidates()`
- Alert gating via `is_alert_worthy()`

---

## Tier 1 â€” RuleAnalyzer

**Status**: Implemented (`app/analysis/rules/rule_analyzer.py`)

**Guaranteed outputs** (always present, deterministic):

| Field | Value |
|-------|-------|
| `relevance_score` | keyword-density heuristic (0.0â€“0.6) |
| `market_scope` | from matched watchlist categories |
| `affected_assets` | from ticker/alias matches |
| `tags` | from keyword categories |
| `confidence_score` | `1.0` (rule is certain about its output) |
| `explanation_short` | `"Rule-based analysis"` |

**Conservative defaults** (intentionally floor values):

| Field | Value | Reason |
|-------|-------|--------|
| `sentiment_label` | `NEUTRAL` | No opinion without LLM |
| `sentiment_score` | `0.0` | |
| `impact_score` | `0.0` | Unknown without context |
| `novelty_score` | `0.5` | Neutral assumption |
| `actionable` | `False` | Conservative |
| `spam_probability` | `0.0` | Assume legitimate |

**Priority ceiling proof** â€” with all conservative defaults:
```
raw = (relevanceÃ—0.30) + (impactÃ—0.30) + (noveltyÃ—0.20) + (actionableÃ—0.15) + (qualityÃ—0.05)
    â‰¤ (0.60Ã—0.30) + (0.0Ã—0.30) + (0.5Ã—0.20) + (0Ã—0.15) + (1.0Ã—0.05)
    = 0.18 + 0.0 + 0.10 + 0 + 0.05 = 0.33 â†’ priority = round(0.33Ã—9)+1 = 4
```
Maximum achievable (max relevance + max quality): ~5. SignalCandidate threshold is 8 â€” gap is intentional.

**Sprint 4C gap** (not yet fixed):
`apply_to_document()` currently requires `llm_output`. Rule-only results are computed but not persisted.
Fix: Sprint 4C Task 4.10 â€” relax the guard.

---

## Tier 2 â€” Internal Providers

Tier 2 has two distinct implementations. Both implement `BaseAnalysisProvider` â€” zero pipeline changes required.

### Tier 2a â€” InternalModelProvider (`APP_LLM_PROVIDER=internal`)

**Status**: âœ… Implemented (`app/analysis/internal_model/provider.py`)

```
provider_name = "internal"
analysis_source â†’ INTERNAL
```

Rule-based heuristics. No network. Always available. Acts as the guaranteed fallback in `EnsembleProvider`.
Conservative output: `actionable=False`, `sentiment=NEUTRAL`, `impact=0.0`. Priority ceiling ~5.

**Use case**: Last-resort fallback inside EnsembleProvider, or for environments with no model access at all.

### Tier 2b â€” InternalCompanionProvider (`APP_LLM_PROVIDER=companion`)

**Status**: âœ… Implemented (`app/analysis/providers/companion.py`)

```
provider_name = "companion"
analysis_source â†’ INTERNAL
```

HTTP client to a local OpenAI-compatible endpoint (e.g. Ollama, llama.cpp, vLLM â€” localhost only).
Returns full `LLMAnalysisOutput`. Impact capped at 0.8 (Invariant I-17). Can produce SignalCandidates.

**Use case**: Local model inference without external API dependencies.

### Tier 2 Output Scope (InternalCompanionProvider)

| Field | Source | Notes |
|-------|--------|-------|
| `sentiment_label` | model output | |
| `sentiment_score` | model output | |
| `relevance_score` | model output | |
| `impact_score` | model output | **capped at 0.8** (I-17) |
| `tags` | model output | |
| `actionable` | `priority >= 7` | alert threshold, not signal threshold |
| `market_scope` | model output | |
| `affected_assets` | model output | |
| `short_reasoning` | model output | stored in `doc.metadata["explanation_short"]` |
| `novelty_score` | `0.5` | hardcoded conservative |
| `spam_probability` | `0.0` | hardcoded conservative |
| `confidence_score` | `0.7` | hardcoded conservative |

**Priority range with companion**: Typical strong output â†’ priority 8 (SignalCandidate threshold).

### Settings (Implemented)

```python
# app/core/settings.py â€” ProviderSettings
companion_model_endpoint: str | None = None      # e.g. "http://localhost:11434"
companion_model_name: str = "kai-analyst-v1"
companion_model_timeout: int = 10                # seconds
```

### Factory Routing (Implemented)

```python
# app/analysis/factory.py
"internal"   â†’ InternalModelProvider(keyword_engine)       # always returns instance
"companion"  â†’ InternalCompanionProvider(endpoint, model)  # returns None if endpoint not set
```

### Security Constraints

- `companion_model_endpoint` MUST be `localhost` or an explicitly allowlisted internal address.
- No external network calls from companion provider â€” local inference only.
- No API keys for companion model â€” authentication is endpoint-level (internal network).
- Validation at settings load time: reject external URLs for companion endpoint.

---

## Tier 3 â€” External LLM Provider

**Status**: Implemented (OpenAI, Anthropic, Gemini via `app/integrations/`)

**Teacher role**: Tier 3 outputs serve as training signal for Tier 2 distillation (Sprint 6).

**Full output**: All `LLMAnalysisOutput` fields, including `novelty_score`, `spam_probability`, rich narrative.

**Priority range**: 1â€“10. All scores available. Full signal eligibility.

---

## Provider Selection Logic

### Current (implemented)

```
APP_LLM_PROVIDER env var â†’ create_provider() â†’ provider | None
if None â†’ AnalysisPipeline runs without LLM â†’ RuleAnalyzer fallback result

Supported values: "openai", "anthropic", "claude", "gemini", "internal", "companion"
EnsembleProvider: constructed directly (not via APP_LLM_PROVIDER)
```

### EnsembleProvider (implemented)

```python
EnsembleProvider(providers=[openai_provider, internal_provider])
# Tries each in order, returns first success
# InternalModelProvider MUST be the last entry (guaranteed fallback)
# provider_name â†’ "ensemble(openai,internal)" (compound, for traceability)
# model â†’ actual winner's provider_name (tracked at runtime)
```

### Sprint 5C â€” EnsembleProvider Winner-Traceability âœ…

Post-`analyze()` resolution via duck-typing:
- `_resolve_runtime_provider_name(provider)` â€” reads `active_provider_name` from `EnsembleProvider` after `analyze()` completes
- `_resolve_trace_metadata(provider)` â€” reads `provider_chain` from `EnsembleProvider` to build `ensemble_chain`
- `_resolve_analysis_source(provider_name)` â€” string-based, maps winner name to `AnalysisSource`

Result:
- `doc.provider` = actual winning provider name (e.g. `"openai"`, `"internal"`)
- `doc.analysis_source` = correct tier for the winner (never conservative `INTERNAL` override)
- `doc.metadata["ensemble_chain"]` = ordered list of all configured providers (for audit)

---

## AnalysisSource Tracking

### Enum â€” âœ… Implemented

```python
# app/core/enums.py
class AnalysisSource(StrEnum):
    RULE = "rule"                  # Tier 1 â€” fallback / rule-based heuristics
    INTERNAL = "internal"          # Tier 2 â€” InternalModelProvider or InternalCompanionProvider
    EXTERNAL_LLM = "external_llm"  # Tier 3 â€” OpenAI / Anthropic / Gemini
```

### Optional Field + Backward-Compat Property â€” âœ… Implemented (Sprint 5B)

```python
# app/core/domain/document.py
doc.analysis_source: AnalysisSource | None  # set by apply_to_document() via pipeline
doc.effective_analysis_source: AnalysisSource  # @property â€” backward-compat accessor
```

`effective_analysis_source` derivation (fallback for legacy rows without DB column):
- `doc.analysis_source is not None` â†’ return it directly
- `doc.provider in {None, "fallback", "rule"}` â†’ `RULE`
- `doc.provider in {"internal", "companion"}` â†’ `INTERNAL`
- `doc.provider.startswith("ensemble(")` â†’ `INTERNAL` (pre-5C composite guard)
- else â†’ `EXTERNAL_LLM`

### DB Column â€” âœ… Implemented (Sprint 5B, migration 0006)

`canonical_documents.analysis_source VARCHAR(20)` â€” Alembic migration `0006_add_analysis_source_column.py`.

`apply_to_document()` writes `doc.analysis_source` from `AnalysisResult.analysis_source` (set at
pipeline result creation time). The `effective_analysis_source` property remains for backward
compatibility with pre-5B rows.

Enables:
- Distillation corpus selection: only `EXTERNAL_LLM` documents as teacher signal (I-19)
- Quality reporting and filtering by tier in research outputs
- EnsembleProvider winner traceability (Sprint 5C)

---

## Distillation Path (Sprint 6 foundation)

Detailed contract reference: [dataset_evaluation_contract.md](./dataset_evaluation_contract.md)

### Overview

```
Teacher-only dataset
      â”‚  analysis_source = EXTERNAL_LLM
      â–¼
Distillation-ready corpus
      â”‚
      â–¼
Internal benchmark export
      â”‚  analysis_source = INTERNAL
      â–¼
Rule baseline export
      â”‚  analysis_source = RULE
      â–¼
Offline evaluation harness
      â”‚
      â–¼
Distillation readiness report
```

Sprint 6 remains offline at the dataset boundary. The runtime foundation is already in place:
- `export_training_data(..., teacher_only=True)` for strict teacher-only export
- `compare_datasets()` and `load_jsonl()` for offline JSONL comparison
- no new provider runtime or training engine introduced at this stage

### Dataset Roles

| Role | `analysis_source` | Purpose |
|------|-------------------|---------|
| Teacher-only dataset | `EXTERNAL_LLM` | Teacher signal for distillation |
| Internal benchmark export | `INTERNAL` | Measure internal quality against teacher outputs |
| Rule baseline export | `RULE` | Measure deterministic floor and regression gap |

Rules:
- only `EXTERNAL_LLM` is teacher-eligible
- `INTERNAL` is benchmark-only
- `RULE` is baseline-only
- teacher filtering uses `analysis_source` only

### Required Evaluation Metrics

| Metric | Meaning |
|--------|---------|
| `sentiment_agreement` | exact label agreement rate |
| `priority_mae` | mean absolute deviation of priority score |
| `relevance_mae` | mean absolute deviation vs teacher/reference |
| `impact_mae` | mean absolute deviation vs teacher/reference |
| `tag_overlap_mean` | mean Jaccard overlap of normalized tag sets |

Thresholds are intentionally not frozen in the architecture layer yet.
Sprint 6 first standardizes dataset roles, evaluation inputs, and minimal metrics.

---

## Research Output Compatibility

| Output | Tier 1 (Rule) | Tier 2 (Companion) | Tier 3 (External LLM) |
|--------|:---:|:---:|:---:|
| `ResearchBrief.key_documents` | âœ… | âœ… | âœ… |
| `ResearchBrief.top_actionable_signals` | âŒ | âœ… (if priority â‰¥ 8) | âœ… |
| `SignalCandidate` via `extract_signal_candidates()` | âŒ | âœ… | âœ… |
| `direction_hint != "neutral"` | âŒ | âœ… | âœ… |
| `impact_score > 0` | âŒ | âœ… | âœ… |
| Full narrative / explanation | âŒ | Partial | âœ… |

---

## Shadow Run

Sprint 10 adds a controlled shadow mode for the internal companion layer:

- primary analysis remains the only path that updates `CanonicalDocument`
- companion may run concurrently as a sidecar for audit and benchmarking
- shadow output stays outside routing and promotion decisions
- operator-facing traces are written as sidecar artifacts with:
  - `document_id`
  - primary `provider` / `analysis_source`
  - companion summary and scores
  - structured deviations versus primary

This gives KAI a production-near comparison path without introducing a second
pipeline, a second persistence model, or automatic model switching.

---

## Sprint 14 - Controlled A/B/C Inference and Distribution

Sprint 14 formalizes a small operator-facing orchestration layer above the existing
primary, shadow, comparison, promotion, and upgrade-cycle artifacts.

- `A` is the active primary path and remains the only path allowed to own persisted results.
- `B` is the shadow or trained companion path and remains audit-oriented.
- `C` is the control path and remains rule-based.

The architecture stays intentionally conservative:

- route profiles are declarative, not self-activating
- distribution channels decide where outputs are written, not which path wins
- shadow and control outputs remain comparison context, not production overrides

Planned route modes:

| `route_profile` | Active paths | Meaning |
|-----------------|--------------|---------|
| `primary_only` | `A` | current production behavior |
| `primary_with_shadow` | `A + B` | primary plus companion audit |
| `primary_with_control` | `A + C` | primary plus rule control |
| `primary_with_shadow_and_control` | `A + B + C` | full audit envelope |

Planned distribution channels:

| Channel | Default ownership |
|---------|-------------------|
| `research_brief` | `A` only |
| `signal_candidates` | `A` only |
| `shadow_audit_jsonl` | `B` only |
| `comparison_report_json` | A-vs-B and/or A-vs-C |
| `upgrade_cycle_report_json` | audit summary only |
| `promotion_audit_json` | audit linkage only |

Canonical contract: [sprint14_inference_distribution_contract.md](./sprint14_inference_distribution_contract.md)

---

## Implementation Order

| Sprint | Component | Status |
|--------|-----------|--------|
| 4C | Relax `apply_to_document()` guard â€” Tier 1 results persisted | âœ… |
| 4C | `analyze_pending` None-guard â€” FAILED instead of silent | âœ… |
| 4D | `InternalModelProvider` (heuristic, zero deps) | âœ… |
| 4D | `EnsembleProvider` (ordered fallback, first success wins) | âœ… |
| 4D | `InternalCompanionProvider` (HTTP to local model endpoint) | âœ… |
| 4D | Factory routing: `"internal"` / `"companion"` / ensemble | âœ… |
| 5A | `InternalCompanionProvider` settings fields + localhost validation | âœ… |
| 5B | `AnalysisSource` enum + `doc.analysis_source` field | âœ… |
| 5B | `analysis_source` DB migration (migration 0006) + ORM column | âœ… |
| 5B | `effective_analysis_source` property â€” backward-compat accessor | âœ… |
| 5B | Pipeline: `_resolve_analysis_source()` + `apply_to_document()` write | âœ… |
| 5B | Provenance in research outputs: briefs, signals, datasets | âœ… |
| 5C | `EnsembleProvider` Winner-Traceability â€” post-analyze resolution | âœ… |
| 5C | `doc.provider` = winner name; `doc.metadata["ensemble_chain"]` = full list | âœ… |
| 6 | Dataset construction + evaluation harness + distillation readiness | âœ… |
| 7 | Companion benchmark harness + promotion gate + artifact contract | âœ… |
| 8 | Controlled companion inference + tuning artifact flow + manual promotion | âœ… |
| 9 | Promotion audit hardening: I-34 automated (G6), gates_summary in record, artifact linkage | âœ… |
---

## AnalysisSource Tracking

### Enum â€” âœ… Implemented

```python
# app/core/enums.py
class AnalysisSource(StrEnum):
    RULE = "rule"                  # Tier 1 â€” fallback / rule-based heuristics
    INTERNAL = "internal"          # Tier 2 â€” InternalModelProvider or InternalCompanionProvider
    EXTERNAL_LLM = "external_llm"  # Tier 3 â€” OpenAI / Anthropic / Gemini
```

### Optional Field + Backward-Compat Property â€” âœ… Implemented (Sprint 5B)

```python
# app/core/domain/document.py
doc.analysis_source: AnalysisSource | None  # set by apply_to_document() via pipeline
doc.effective_analysis_source: AnalysisSource  # @property â€” backward-compat accessor
```

`effective_analysis_source` derivation (fallback for legacy rows without DB column):
- `doc.analysis_source is not None` â†’ return it directly
- `doc.provider in {None, "fallback", "rule"}` â†’ `RULE`
- `doc.provider in {"internal", "companion"}` â†’ `INTERNAL`
- `doc.provider.startswith("ensemble(")` â†’ `INTERNAL` (pre-5C composite guard)
- else â†’ `EXTERNAL_LLM`

### DB Column â€” âœ… Implemented (Sprint 5B, migration 0006)

`canonical_documents.analysis_source VARCHAR(20)` â€” Alembic migration `0006_add_analysis_source_column.py`.

`apply_to_document()` writes `doc.analysis_source` from `AnalysisResult.analysis_source` (set at
pipeline result creation time). The `effective_analysis_source` property remains for backward
compatibility with pre-5B rows.

Enables:
- Distillation corpus selection: only `EXTERNAL_LLM` documents as teacher signal (I-19)
- Quality reporting and filtering by tier in research outputs
- EnsembleProvider winner traceability (Sprint 5C)

---

## Distillation Path (Sprint 6 foundation)

Detailed contract reference: [dataset_evaluation_contract.md](./dataset_evaluation_contract.md)

### Overview

```
Teacher-only dataset
      â”‚  analysis_source = EXTERNAL_LLM
      â–¼
Distillation-ready corpus
      â”‚
      â–¼
Internal benchmark export
      â”‚  analysis_source = INTERNAL
      â–¼
Rule baseline export
      â”‚  analysis_source = RULE
      â–¼
Offline evaluation harness
      â”‚
      â–¼
Distillation readiness report
```

Sprint 6 remains offline at the dataset boundary. The runtime foundation is already in place:
- `export_training_data(..., teacher_only=True)` for strict teacher-only export
- `compare_datasets()` and `load_jsonl()` for offline JSONL comparison
- no new provider runtime or training engine introduced at this stage

### Dataset Roles

| Role | `analysis_source` | Purpose |
|------|-------------------|---------|
| Teacher-only dataset | `EXTERNAL_LLM` | Teacher signal for distillation |
| Internal benchmark export | `INTERNAL` | Measure internal quality against teacher outputs |
| Rule baseline export | `RULE` | Measure deterministic floor and regression gap |

Rules:
- only `EXTERNAL_LLM` is teacher-eligible
- `INTERNAL` is benchmark-only
- `RULE` is baseline-only
- teacher filtering uses `analysis_source` only

### Required Evaluation Metrics

| Metric | Meaning |
|--------|---------|
| `sentiment_agreement` | exact label agreement rate |
| `priority_mae` | mean absolute deviation of priority score |
| `relevance_mae` | mean absolute deviation vs teacher/reference |
| `impact_mae` | mean absolute deviation vs teacher/reference |
| `tag_overlap_mean` | mean Jaccard overlap of normalized tag sets |

Thresholds are intentionally not frozen in the architecture layer yet.
Sprint 6 first standardizes dataset roles, evaluation inputs, and minimal metrics.

---

## Research Output Compatibility

| Output | Tier 1 (Rule) | Tier 2 (Companion) | Tier 3 (External LLM) |
|--------|:---:|:---:|:---:|
| `ResearchBrief.key_documents` | âœ… | âœ… | âœ… |
| `ResearchBrief.top_actionable_signals` | âŒ | âœ… (if priority â‰¥ 8) | âœ… |
| `SignalCandidate` via `extract_signal_candidates()` | âŒ | âœ… | âœ… |
| `direction_hint != "neutral"` | âŒ | âœ… | âœ… |
| `impact_score > 0` | âŒ | âœ… | âœ… |
| Full narrative / explanation | âŒ | Partial | âœ… |

---

## Shadow Run

Sprint 10 adds a controlled shadow mode for the internal companion layer:

- primary analysis remains the only path that updates `CanonicalDocument`
- companion may run concurrently as a sidecar for audit and benchmarking
- shadow output stays outside routing and promotion decisions
- operator-facing traces are written as sidecar artifacts with:
  - `document_id`
  - primary `provider` / `analysis_source`
  - companion summary and scores
  - structured deviations versus primary

This gives KAI a production-near comparison path without introducing a second
pipeline, a second persistence model, or automatic model switching.

---

## Sprint 14 - Controlled A/B/C Inference and Distribution

Sprint 14 formalizes a small operator-facing orchestration layer above the existing
primary, shadow, comparison, promotion, and upgrade-cycle artifacts.

- `A` is the active primary path and remains the only path allowed to own persisted results.
- `B` is the shadow or trained companion path and remains audit-oriented.
- `C` is the control path and remains rule-based.

The architecture stays intentionally conservative:

- route profiles are declarative, not self-activating
- distribution channels decide where outputs are written, not which path wins
- shadow and control outputs remain comparison context, not production overrides

Planned route modes:

| `route_profile` | Active paths | Meaning |
|-----------------|--------------|---------|
| `primary_only` | `A` | current production behavior |
| `primary_with_shadow` | `A + B` | primary plus companion audit |
| `primary_with_control` | `A + C` | primary plus rule control |
| `primary_with_shadow_and_control` | `A + B + C` | full audit envelope |

Planned distribution channels:

| Channel | Default ownership |
|---------|-------------------|
| `research_brief` | `A` only |
| `signal_candidates` | `A` only |
| `shadow_audit_jsonl` | `B` only |
| `comparison_report_json` | A-vs-B and/or A-vs-C |
| `upgrade_cycle_report_json` | audit summary only |
| `promotion_audit_json` | audit linkage only |

Canonical contract: [sprint14_inference_distribution_contract.md](./sprint14_inference_distribution_contract.md)

---

## Implementation Order

| Sprint | Component | Status |
|--------|-----------|--------|
| 4C | Relax `apply_to_document()` guard â€” Tier 1 results persisted | âœ… |
| 4C | `analyze_pending` None-guard â€” FAILED instead of silent | âœ… |
| 4D | `InternalModelProvider` (heuristic, zero deps) | âœ… |
| 4D | `EnsembleProvider` (ordered fallback, first success wins) | âœ… |
| 4D | `InternalCompanionProvider` (HTTP to local model endpoint) | âœ… |
| 4D | Factory routing: `"internal"` / `"companion"` / ensemble | âœ… |
| 5A | `InternalCompanionProvider` settings fields + localhost validation | âœ… |
| 5B | `AnalysisSource` enum + `doc.analysis_source` field | âœ… |
| 5B | `analysis_source` DB migration (migration 0006) + ORM column | âœ… |
| 5B | `effective_analysis_source` property â€” backward-compat accessor | âœ… |
| 5B | Pipeline: `_resolve_analysis_source()` + `apply_to_document()` write | âœ… |
| 5B | Provenance in research outputs: briefs, signals, datasets | âœ… |
| 5C | `EnsembleProvider` Winner-Traceability â€” post-analyze resolution | âœ… |
| 5C | `doc.provider` = winner name; `doc.metadata["ensemble_chain"]` = full list | âœ… |
| 6 | Dataset construction + evaluation harness + distillation readiness | âœ… |
| 7 | Companion benchmark harness + promotion gate + artifact contract | âœ… |
| 8 | Controlled companion inference + tuning artifact flow + manual promotion | âœ… |
| 9 | Promotion audit hardening: I-34 automated (G6), gates_summary in record, artifact linkage | âœ… |
| 10 | Companion shadow run: audit-only parallel inference, divergence JSONL, no routing influence | âœ… |
| 11 | Distillation harness: teacher/candidate/shadow combined readiness report, evaluation engine, distillation manifest | âœ… |
| 12 | Training Job Record: pre-training manifest, post-training eval link, promotion continuity, shadow schema canonicalization | âœ… |
| 13 | Evaluation Comparison + Regression Guard: pre/post model comparison, regression visibility (has_regression), comparison audit artifact, PromotionRecord.comparison_report_path, record-promotion --comparison (I-72), upgrade-cycle-status CLI | âœ… |
| 14 | Controlled A/B/C inference profiles + signal distribution contract (no auto-routing) | âœ… |
| 14C | Runtime Route Activation: ActiveRouteState, route-activate/route-deactivate CLI, I-90â€“I-93 | âœ… |
| 17 | analyze-pending Route Integration: route_runner.py, ActiveRouteState consumed by analyze-pending, ABCInferenceEnvelope per document â†’ audit JSONL only (I-92, I-93) | âœ… |
| 18 | Controlled MCP Server: app/agents/mcp_server.py, 8 read tools + 3 guarded write tools, _resolve_workspace_path() workspace guard, I-94â€“I-100 | âœ… |
| 16 | Immutable Signal Handoff Layer: execution_handoff.py (SignalHandoff frozen dataclass), CLI: signal-handoff, JSONL batch export, I-105â€“I-108 | âœ… |
| 19 | Route-Aware Distribution: classify_delivery_class(), RouteAwareDistributionSummary, DistributionClassificationReport, DeliveryClassification, I-109â€“I-115 | âœ… |
| 20 | Consumer Collector & Acknowledgement Orchestration: execution_handoff.py (HandoffAcknowledgement, create/append/load_handoff_acknowledgement), distribution.py (HandoffCollectorSummaryReport, build_handoff_collector_summary), acknowledge_signal_handoff MCP (audit-only write, PermissionError on hidden), get_handoff_collector_summary MCP (read), CLI: handoff-acknowledge + handoff-collector-summary, I-116â€“I-122 | âœ… |
| 21 | Operational Readiness Surface: operational_readiness.py (OperationalReadinessReport, ReadinessIssue, RouteReadinessSummary, AlertDispatchSummary, ProviderHealthSummary, DistributionDriftSummary, OperationalArtifactRefs, build/save_operational_readiness_report), MCP: get_operational_readiness_summary (read-only), CLI: research readiness-summary, I-123â€“I-130 | âœ… |
| 22 | Provider Health & Distribution Drift Monitoring: operational_readiness.py bleibt der einzige Monitoring-Stack; MCP: get_provider_health(handoff_path, state_path, abc_output_path) + get_distribution_drift(handoff_path, state_path, abc_output_path) als read-only Readiness-Views (I-95, I-134), CLI: research provider-health + research drift-summary als Readiness-Views, operational_alerts.py superseded, I-131â€“I-138, contracts.md Â§34 | âœ… |
| 23 | Protective Gates & Remediation Recommendations: operational_readiness.py (interne ProtectiveGateSummary/ProtectiveGateItem in OperationalReadinessReport) als einziger kanonischer Gate-Pfad, read-only advisory system, kein Execution-Hook, MCP: get_protective_gate_summary(...) + get_remediation_recommendations(...), CLI: research gate-summary + research remediation-recommendations, protective_gates.py superseded, I-139â€“I-145, contracts.md Â§35 âœ… |
| 24 | Artifact Lifecycle Management: artifact_lifecycle.py (ArtifactEntry, ArtifactInventoryReport frozen execution_enabled=False, ArtifactRotationSummary), build_artifact_inventory(artifacts_dir, stale_after_days=30), rotate_stale_artifacts(dry_run=True default, archive-only never-delete, policy-aware: protected skipped), MCP: get_artifact_inventory (read-only, workspace-confined), CLI: research artifact-inventory + research artifact-rotate (--dry-run default), I-146â€“I-152, contracts.md Â§36 âœ… |
| 25 | Safe Artifact Retention & Cleanup Policy: artifact_lifecycle.py erweitert â€” ArtifactRetentionEntry (frozen, delete_eligible=False immer), ArtifactRetentionReport (frozen, execution_enabled=False, write_back_allowed=False, delete_eligible_count=0), ArtifactCleanupEligibilitySummary, ProtectedArtifactSummary. classify_artifact_retention() reine Klassifikation (I-160). Klassen: audit_trail/promotion/training_data/active_state/evaluation/operational/unknown â†’ protected/rotatable/review_required. rotate_stale_artifacts() policy-aware (I-155). MCP: get_artifact_retention_report + get_cleanup_eligibility_summary + get_protected_artifact_summary. CLI: research artifact-retention. I-153â€“I-161, contracts.md Â§37 âœ… |
| 26 | Artifact Governance/Review Surface: artifact_lifecycle.py bleibt der einzige Governance-/Review-Stack auf Basis des kanonischen Retention-Reports. Finale read-only Modelle/Slices: ArtifactRetentionReport, ArtifactCleanupEligibilitySummary, ProtectedArtifactSummary, ReviewRequiredArtifactSummary. MCP: get_artifact_retention_report + get_cleanup_eligibility_summary + get_protected_artifact_summary + get_review_required_summary. CLI: research artifact-retention + research cleanup-eligibility-summary + research protected-artifact-summary + research review-required-summary. Superseded: ArtifactGovernanceSummary, ArtifactPolicyRationaleSummary, get_governance_summary, get_policy_rationale_summary, research governance-summary. contracts.md Â§38 âœ… |

---

## Invariants

> Full invariant list is canonical in `docs/contracts.md Â§Immutable Invariants`.
> Intelligence-layer invariants (I-14 through I-33) are listed here for quick reference.

| ID | Rule |
|----|------|
| I-14 | `InternalCompanionProvider` implements `BaseAnalysisProvider` exactly â€” zero pipeline changes |
| I-15 | Companion model endpoint MUST be localhost or allowlisted â€” no external inference |
| I-16 | Distillation corpus uses only `analysis_source=EXTERNAL_LLM` documents as teacher signal |
| I-17 | Companion model `impact_score` cap: â‰¤ 0.8 (conservative, not overconfident) |
| I-18 | `AnalysisSource` is set at result creation time â€” immutable after `apply_to_document()` |
| I-19 | Rule-only documents (`analysis_source=RULE`) NEVER serve as distillation teacher signal |
| I-20 | `InternalModelProvider.provider_name` is always `"internal"`, `recommended_priority` â‰¤ 5, `actionable=False`, `sentiment_label=NEUTRAL` â€” hard invariants, not configurable |
| I-21 | `InternalCompanionProvider.provider_name` is always `"companion"` â€” distinct from `"internal"`. Factory routes `"internal"` â†’ `InternalModelProvider`, `"companion"` â†’ `InternalCompanionProvider` |
| I-22 | `EnsembleProvider` requires at least one provider. `InternalModelProvider` MUST be last for guaranteed fallback. All fail â†’ `RuntimeError` |
| I-23 | `EnsembleProvider.model` MUST return the winning provider's `provider_name` immediately after `analyze()` completes â€” this is the canonical winner signal |
| I-24 | `_resolve_runtime_provider_name(provider)` uses duck-typing (`getattr(provider, "active_provider_name", None)`) AFTER `analyze()` succeeds to resolve the winner. `_resolve_analysis_source(winner_name)` is then called string-based. Never invoked in error/fallback paths. |
| I-25 | `doc.provider` stores the winning provider name (e.g. `"openai"`), never the composite ensemble string. `doc.metadata["ensemble_chain"]` records the full ordered list. |
| I-26 | Teacher eligibility is determined exclusively by `analysis_source=EXTERNAL_LLM`. `doc.provider`, `doc.metadata["ensemble_chain"]`, and all other metadata MUST NOT be used as teacher-eligibility criteria. |
| I-27 | `export_training_data(..., teacher_only=True)` is the function-level teacher guardrail. It filters strictly on `doc.analysis_source == EXTERNAL_LLM`; legacy rows without an explicit field are excluded in strict mode. |
| I-28 | Current `evaluate` CLI is a baseline-only comparison path, not a full companion accuracy harness yet. |
| I-29 | Sprint-6 dataset roles are determined only by `analysis_source`: teacher/external, benchmark/internal, baseline/rule. |
| I-30 | `INTERNAL` and `RULE` rows are never teacher labels. |
| I-31 | Teacher-only filtering must never branch on `provider` or `ensemble_chain`. |
| I-32 | Evaluation joins datasets by `document_id` only. |
| I-33 | Sprint-6 minimum metrics are `sentiment_agreement`, `priority_mae`, `relevance_mae`, `impact_mae`, `tag_overlap_mean`. |
| I-34 | `false_actionable_rate` is the 6th automated promotion gate (G6, â‰¤ 0.05). Computed by `compare_datasets()`, enforced by `validate_promotion()` as `false_actionable_pass`. Supersedes original "manual, deferred" note. See I-46, `docs/contracts.md Â§20`. |
| I-51 | Shadow run MUST NEVER call `apply_to_document()` or `repo.update_analysis()`. Zero DB writes to `canonical_documents`. |
| I-52 | Shadow run calls `InternalCompanionProvider.analyze()` directly â€” independent of `APP_LLM_PROVIDER`. Never a routing override. |
| I-53 | Shadow JSONL is a standalone audit artifact â€” not EvaluationReport input, not training corpus. |
| I-54 | Shadow run requires `companion_model_endpoint`. Absent â†’ exit 0 (informational), not error. |
| I-55 | Divergence summary is informational only â€” never used for routing, gating, or output modification. |
| I-58 | `DistillationReadinessReport` is a readiness assessment only. No training, no routing changes. `promotion_validation.is_promotable` is informational. |
| I-59 | Shadow JSONL MUST NEVER be passed as teacher or candidate input in `DistillationInputs`. Shadow is audit context only (I-16, I-53). |
| I-60 | `compute_shadow_coverage()` reads shadow records for divergence stats only â€” never calls `compare_datasets()`. |
| I-61 | `DistillationReadinessReport.shadow_coverage` is optional â€” absent shadow does not block distillation readiness. |
| I-62 | `build_distillation_report()` is pure computation â€” no DB reads, no LLM calls, no network. |
| I-63 | `TrainingJobRecord` is a pre-training manifest only â€” no training, no API calls, no weight updates. |
| I-64 | `TrainingJobRecord` status="pending" does not represent a trained model. Training is operator-external. |
| I-65 | Post-training evaluation MUST pass G1â€“G6 via `validate_promotion()`. No bypass. |
| I-66 | Trained model not active until operator reconfigures `APP_LLM_PROVIDER`. No Sprint-12 routing change (I-42). |
| I-67 | Training teacher input MUST be `EXTERNAL_LLM` only. INTERNAL/RULE/Shadow forbidden (I-16, I-19, I-53). |
| I-68 | `record-promotion` remains sole promotion gate. TrainingJobRecord and PostTrainingEvaluationSpec are audit artifacts only. |
| I-69 | Sprint-12 canonical shadow schema: `deviations.*_delta` (evaluation.py format). `divergence.*_diff` is deprecated alias. |
| I-70 | `EvaluationComparisonReport` is comparison artifact only â€” no routing, no promotion trigger, no gate bypass. |
| I-71 | `compare_evaluation_reports()` is pure computation â€” two JSON files only. No DB, no LLM, no network. |
| I-72 | Hard regression detected + `--comparison` passed to `record-promotion` â†’ RED warning printed. No auto-block. Operator decides. |
| I-73 | `compare-evaluations` exit 0 â‰  promotable. `check-promotion` still required (I-36, I-65). |
| I-74 | Baseline and candidate must share same `dataset_type` â€” mismatch â†’ `ValueError`. |
| I-75 | `UpgradeCycleReport` is pure read/summarize. `build_upgrade_cycle_report()` MUST NOT trigger training, evaluation, or routing changes. JSON reads only. |
| I-76 | `UpgradeCycleReport.status` derived from artifact presence (`Path.exists()`) only â€” never auto-advanced by platform code. |
| I-77 | `UpgradeCycleReport.promotion_readiness=True` is informational. No platform code changes routing or calls `record-promotion` on this basis (I-36, I-68). |
| I-78 | `UpgradeCycleReport.promotion_record_path` only set when operator explicitly supplies it â€” never auto-populated from env or settings. |
| I-79 | Each `UpgradeCycleReport` = one upgrade attempt. Separate files per cycle. No in-place overwrite (I-38 extends). |
| I-80 | Route profiles are declarative only â€” never self-activating. |
| I-81 | `A` is the only production-owning path; `B` and `C` stay audit-only in Sprint 14. |
| I-82 | Shadow and control outputs never overwrite the primary result. |
| I-83 | Distribution does not imply decision, promotion, or routing change. |
| I-84 | Configured routing remains inert until an explicit future runtime hook exists. |
| I-85 | Every A/B/C artifact must preserve `document_id`, path label, provider, and `analysis_source`. |
| I-86 | Comparison summaries remain additive audit context only. |
| I-87 | The control path remains rule-bound and provider-independent. |
| I-139 | Protective gates are strictly read-only and observational. They NEVER trigger auto-remediation. |
| I-140 | `ProtectiveGateSummary.execution_enabled` MUST always be `False`. |
| I-141 | `ProtectiveGateSummary.write_back_allowed` MUST always be `False`. |
| I-142 | `ProtectiveGateItem.recommended_actions` are strictly advisory. They represent human-operator hints only. |
| I-143 | Protective Gates MUST NOT be wired into the core trading engine, alerting layer, or analysis loop. |
| I-144 | MCP and CLI protective-gate surfaces are read-only views only. They MUST NOT expose remediation execution. |
| I-145 | `protective_gates.py` is superseded. The canonical gate contract lives in `operational_readiness.py`. |
| I-146 | `artifact_lifecycle.py` is the sole canonical artifact lifecycle management layer. No second stack. |
| I-147 | `rotate_stale_artifacts()` MUST default to `dry_run=True`. No filesystem writes when `dry_run=True`. |
| I-148 | Rotation archives to `artifacts/archive/<timestamp>/` ONLY. Never deletes, never overwrites source files. |
| I-149 | `get_artifact_inventory` MCP tool is strictly read-only. No filesystem mutations. |
| I-150 | `ArtifactInventoryReport.execution_enabled` MUST always be `False`. |
| I-151 | Stale detection uses file `mtime` only â€” no content inspection of artifact files. |
| I-152 | CLI `artifact-rotate` defaults to `--dry-run`. Operator must pass `--no-dry-run` for actual archival. |
| I-169 | `OperationalEscalationSummary.execution_enabled` MUST always be `False`. |
| I-170 | `OperationalEscalationSummary.write_back_allowed` MUST always be `False`. |
| I-171 | Escalation classification is derived ONLY from `ProtectiveGateSummary` plus `ReviewRequiredArtifactSummary`. No second monitoring stack. |
| I-172 | Blocking escalation rows MUST come ONLY from canonical blocking gate items. Escalation MUST NOT invent new blocking reasons. |
| I-173 | Review-required escalation rows MUST remain advisory and operator-facing only. They MUST NOT trigger cleanup, archival, or deletion. |
| I-174 | `BlockingSummary` and `OperatorActionSummary` are read-only projections of the canonical escalation summary only. No independent data sources. |
| I-175 | CLI and MCP escalation surfaces MUST expose ONLY `escalation-summary`, `blocking-summary`, and `operator-action-summary` / `get_escalation_summary`, `get_blocking_summary`, and `get_operator_action_summary` as the canonical names. |
| I-176 | No escalation surface may mutate route state, handoffs, acknowledgements, artifact retention classes, or any core DB state. |
| I-177 | `ActionQueueSummary.execution_enabled` MUST always be `False`. |
| I-178 | `ActionQueueSummary.write_back_allowed` MUST always be `False`. |
| I-179 | Action queue formation derives ONLY from `OperationalEscalationSummary`; no second escalation or gate stack is permitted. |
| I-180 | Blocking queue entries MUST come only from canonical blocking escalation rows. |
| I-181 | Review-required queue entries MUST stay advisory and operator-facing only; they MUST NOT trigger cleanup, archival, or deletion. |
| I-182 | `BlockingActionsSummary`, `PrioritizedActionsSummary`, and `ReviewRequiredActionsSummary` are read-only projections of the canonical action queue only. |
| I-183 | CLI and MCP action-queue surfaces MUST expose ONLY `action-queue-summary`, `blocking-actions`, `prioritized-actions`, `review-required-actions` / `get_action_queue_summary`, `get_blocking_actions`, `get_prioritized_actions`, `get_review_required_actions` as canonical names. |
| I-184 | No action queue surface may mutate route state, handoffs, acknowledgements, artifact retention classes, or any core DB state. |
| I-185 | `OperatorDecisionPack.execution_enabled` MUST always be `False`. |
| I-186 | `OperatorDecisionPack.write_back_allowed` MUST always be `False`. |
| I-187 | Decision pack formation derives ONLY from canonical readiness, escalation, action-queue, and governance summaries. No independent data sources. |
| I-188 | `OperatorDecisionPack` MUST NOT expose or invoke any trading execution hook. |
| I-189 | CLI `decision-pack-summary` is strictly read-only. No state mutation of any kind. |
| I-190 | MCP `get_decision_pack_summary` and `get_operator_decision_pack` are strictly read-only. No write-back, no routing changes. |
| I-191 | The deprecated `governance-summary` CLI command MUST NOT be exposed in the `research --help` output. |
| I-192 | `OperatorDecisionPack.report_type` MUST always be `"operator_decision_pack"`. |
| I-193 | `OperatorRunbookSummary.execution_enabled` MUST always be `False`. |
| I-194 | `OperatorRunbookSummary.write_back_allowed` MUST always be `False`. |
| I-195 | `OperatorRunbookSummary.auto_remediation_enabled` MUST always be `False`. No auto-remediation of any kind. |
| I-196 | `OperatorRunbookSummary.auto_routing_enabled` MUST always be `False`. No auto-routing or auto-promotion. |
| I-197 | Runbook formation derives ONLY from the canonical `OperatorDecisionPack`. No independent readiness, escalation, or gate stack. |
| I-198 | All `RunbookStep.command_refs` MUST point to actually registered canonical `research` sub-commands only. Superseded, removed, or hypothetical commands MUST NOT appear. |
| I-199 | `OperatorRunbookSummary.report_type` MUST always be `"operator_runbook_summary"`. |
| I-200 | No runbook surface may trigger trade execution, auto-routing, auto-promotion, DB mutation, or artifact deletion. |
| I-201 | `get_registered_research_command_names()` in `app/cli/main.py` is the authoritative CLI registry. All command-ref validation MUST use this set as ground truth. |
| I-202 | `get_mcp_capabilities()` read_tools list MUST match the set of currently registered non-deprecated `@mcp.tool()` functions. Any registered tool not in read_tools MUST be explicitly classified as deprecated. |
| I-203 | Every canonical CLI command MUST have at least one targeted test. After Sprint 31: zero untested commands permitted. |
| I-204 | The superseded MCP tool `get_operational_escalation_summary` MUST NOT appear in the `read_tools` list in `get_mcp_capabilities()`. Active backward-compatible aliases (`get_handoff_summary`, `get_operator_decision_pack`) may remain in read_tools. |
| I-205 | `get_narrative_clusters` is a registered non-deprecated `@mcp.tool()` and MUST appear in the `read_tools` list in `get_mcp_capabilities()`. |
| I-206 | No new CLI command may be merged without a corresponding targeted test covering at minimum the happy path or the primary error path. |
| I-207 | No new `@mcp.tool()` may be merged without being either listed in `read_tools` or `write_tools`, or explicitly documented as a deprecated alias in `get_mcp_capabilities()`. |
| I-208 | The canonical CLI surface is: 4 `query_app` commands + 40 `research_app` commands = 44 total (authoritative: `get_registered_research_command_names()`). Changes to this count require a contracts.md update. |
| I-209 | The canonical MCP surface is: 32 read_tools + 5 write_tools + 1 workflow_helper = 38 documented + 2 active_aliases + 1 superseded (`get_operational_escalation_summary`, not in read_tools) = 41 total registered `@mcp.tool()`. Changes require a contracts.md update. (Updated Sprint 33: +2 read, +1 write.) |
| I-210 | CLI surface drift MUST be caught by `test_research_command_inventory_matches_registration_and_help`. This test is non-negotiable and MUST remain green after every sprint. |
| I-211 | All 41 registered `@mcp.tool()` functions MUST be classified as canonical, active_alias, superseded, or workflow_helper. No tool may be unclassified. Classification is maintained in contracts.md Â§44. (Updated Sprint 33.) |
| I-212 | Superseded tools MUST NOT appear in `read_tools` in `get_mcp_capabilities()`. They may remain registered for backward compatibility only, and MUST be explicitly documented as superseded. |
| I-213 | Active aliases MUST appear in `read_tools` alongside their canonical counterparts. They may not be silently removed without a contracts.md update and a migration note. |
| I-214 | `get_narrative_clusters` is canonical (not an alias). It MUST appear in `read_tools`. Coverage: at least one targeted test required (I-216 applies). |
| I-215 | `get_operational_escalation_summary` is superseded by `get_escalation_summary` (Sprint 27). It MUST NOT appear in `read_tools`. Its presence in the test suite MUST verify the exclusion (not absence from code). |
| I-216 | Every registered `@mcp.tool()` function MUST have at least one targeted test. After Sprint 32: 0 untested tools. |
| I-217 | `get_mcp_capabilities()` MUST remain the authoritative machine-readable MCP surface description. No agent or operator may assume MCP capabilities without querying it. |
| I-218 | All guarded-write tools MUST append to `mcp_write_audit.jsonl` on every call (I-94). Applies to: `create_inference_profile`, `activate_route_profile`, `deactivate_route_profile`, `acknowledge_signal_handoff`, `append_review_journal_entry`. |
| I-219 | MCP surface changes (adding or removing tools from read_tools or write_tools) MUST be reflected in contracts.md Â§44 and intelligence_architecture.md within the same sprint. |
| I-220 | MCP tool mode MUST be one of: `read_only` (no writes), `guarded_write` (workspace-confined + audited), `workflow_helper` (meta). No tool may have `execution_enabled=True` or `write_back_allowed=True` in its output. |
| I-221 | `ReviewJournalEntry` MUST be immutable (frozen dataclass). Once written to JSONL it MUST NOT be edited or deleted. |
| I-222 | Persistence MUST use append-only JSONL. File is opened in `"a"` mode only. No row may be overwritten. |
| I-223 | Journal entries reference operator-facing artifacts or steps via `source_ref`. They do NOT create new control state. |
| I-224 | Valid `review_action` values are strictly `note`, `defer`, `resolve`. Any other value MUST raise `ValueError`. |
| I-225 | `ReviewJournalSummary.execution_enabled` and `ReviewResolutionSummary.execution_enabled` MUST always be `False`. |
| I-226 | Journal writes MUST NOT mutate KAI core DB state, route state, gate state, decision-pack state, or action-queue state. `core_state_unchanged=True` MUST be returned by the MCP guarded-write tool. |
| I-227 | `ReviewJournalSummary` and `ReviewResolutionSummary` are derived read-only projections. They MUST NOT trigger writes, routing, or state changes. |
| I-228 | `operator_review_journal.jsonl` is a protected audit-trail artifact (I-156 family). It MUST NOT be auto-rotated, archived, or deleted. |
| I-229 | `review_id` MUST be a deterministic UUID derived from normalized entry content. Same inputs MUST produce the same `review_id`. |
| I-230 | No journal surface may trigger trading execution, auto-routing, auto-promotion, or auto-remediation. `auto_remediation_enabled` and `auto_routing_enabled` are always `False` throughout the review stack. |
| I-231 | `BacktestEngine` MUST use `PaperExecutionEngine(live_enabled=False)`. No live execution path exists or may be added without explicit operator gate. |
| I-232 | Every signal in a backtest MUST be routed through all active `RiskEngine.check_order()` gates. Gate bypass is prohibited by design. |
| I-233 | `BacktestResult` MUST be a frozen dataclass. All fields are immutable after construction. |
| I-234 | Market data in `BacktestEngine.run()` MUST be provided externally as `dict[str, float]`. No hidden data fetches may occur inside `run()`. |
| I-235 | Signalâ†’Order mapping MUST be deterministic: same signal + same prices â†’ same order parameters. |
| I-236 | `direction_hint=="neutral"` signals MUST be skipped (`skipped_neutral`). `direction_hint=="bearish"` signals MUST be skipped when `long_only=True` (`skipped_bearish`). |
| I-237 | Once a kill switch is triggered during a backtest run, all subsequent signals MUST be recorded as `kill_switch_halted` without further processing. |
| I-238 | `BacktestResult.kill_switch_triggered` MUST accurately reflect whether the kill switch was active at any point during the run. |
| I-239 | `BacktestResult.to_json_dict()` MUST NOT expose internal paths, `live_enabled`, `execution_enabled`, or any sensitive metadata. |
| I-240 | Every `BacktestEngine.run()` call MUST append one audit row to `artifacts/backtest_audit.jsonl` (append-only, I-94 family). |
| I-241 | CLI command `research decision-journal-append` MUST validate all DecisionInstance fields via `create_decision_instance()` before appending. Invalid inputs MUST fail without writing. |
| I-242 | CLI command `research decision-journal-summary` MUST be read-only. It MUST NOT write, modify, or delete any journal record. |
| I-243 | CLI command `research loop-cycle-summary` MUST be read-only. It reads from JSONL audit only. No cycle state may be modified. |
| I-244 | MCP tool `get_decision_journal_summary` MUST return `execution_enabled=False` and `write_back_allowed=False` in every response. |
| I-245 | MCP tool `append_decision_instance` MUST be classified as `guarded_write` and appear in `_GUARDED_MCP_WRITE_TOOL_NAMES`. |
| I-246 | MCP tool `append_decision_instance` MUST enforce workspace confinement and artifacts/ path restriction (I-95 family). |
| I-247 | MCP tool `get_loop_cycle_summary` MUST be classified as `canonical_read` and appear in `_CANONICAL_MCP_READ_TOOL_NAMES`. |
| I-248 | No Sprint 36 tool may trigger a trade, update approval state, or change execution state of any decision or order. |
| I-249 | All Sprint 36 MCP tools MUST appear in `get_mcp_tool_inventory()` and be registered in FastMCP. The inventory-matches-registered test MUST pass. |
| I-250 | The `append_decision_instance` MCP tool MUST write an MCP write-audit record via `_append_mcp_write_audit()` for every successful append. |

| I-251 | `DECISION_SCHEMA.json` MUST be validated at runtime on every `DecisionRecord` instantiation via `_validate_safe_state()`. Decorative-only JSON Schema files are forbidden from Sprint 37 onwards. |
| I-252 | `CONFIG_SCHEMA.json` MUST be validated at startup via `validate_runtime_config_payload()`. Config payloads that fail the schema MUST fail fast before the system reaches operational state. |
| I-253 | `app/schemas/runtime_validator.py` is the single canonical schema validator implementation. `Draft202012Validator` with `FormatChecker()` MUST be used â€” no `jsonschema.validate()` shortcut. `app/core/settings.py::validate_json_schema_payload()` is a compatibility wrapper that delegates to `runtime_validator.py` and MUST NOT be treated as an independent validator. |
| I-254 | `DecisionInstance` MUST be a `TypeAlias` for `DecisionRecord`. No independent `DecisionInstance` dataclass may exist after Sprint 37. |
| I-255 | Legacy approval states (`auto_approved_paper`) and execution states (`submitted`, `filled`, `partial`, `cancelled`, `error`) MUST be normalized to canonical values during `load_decision_journal()`. They MUST NOT appear in new records. |
| I-256 | `app/schemas/runtime_validator.py` provides the public API (`validate_decision_payload`, `validate_config_payload`, `SchemaValidationError`). Callers outside `settings.py` and `execution/models.py` MUST use this public API. |
| I-257 | `SchemaValidationError` MUST be a subclass of `ValueError`. This ensures existing fail-closed `except ValueError` handlers catch schema violations without modification. |
| I-258 | `DECISION_SCHEMA.json` MUST include `report_type` as an optional string property (added Sprint 37). `additionalProperties: false` remains enforced. |
| I-259 | `DecisionRecord._validate_timestamp_utc` MUST validate that `timestamp_utc` is a valid ISO 8601 datetime string. Invalid timestamps MUST raise `ValidationError` before schema validation runs. |
| I-260 | All 25 tests in `test_schema_runtime_binding.py` MUST pass. They enforce that legacy enum values are rejected at the schema layer (not just the pydantic layer). |
| I-261 | `app/core/schema_binding.py` is the schema integrity layer â€” it validates that the schema FILES themselves are structurally correct. It MUST NOT be confused with `runtime_validator.py` (payload validation). The two modules are complementary, not competing. |
| I-262 | `validate_config_schema()` in `schema_binding.py` MUST verify all 10 safety-critical `const` constraints in CONFIG_SCHEMA.json. Any missing or wrong const value MUST be reported as an error in `SchemaValidationResult.errors`. |
| I-263 | `validate_decision_schema_alignment()` in `schema_binding.py` MUST verify that every field in `DECISION_SCHEMA.json["required"]` exists in `DecisionRecord.model_fields`. Schema fields without a corresponding model field are an error. |
| I-264 | `run_all_schema_validations()` in `schema_binding.py` MUST be callable at startup to detect schema drift. It returns a list of `SchemaValidationResult` â€” one per check â€” and MUST NOT raise on failures (advisory, not fail-closed). |
| I-265 | All 14 tests in `test_schema_binding.py` MUST pass. They cover CONFIG_SCHEMA loading, DECISION_SCHEMA validation, safety-const verification, alignment check, fail-closed behavior on malformed files, and `SchemaValidationResult` immutability. |

| I-266 | Telegram is an Operator Surface, NOT an Execution Surface. No Telegram command path may trigger live execution, auto-routing, auto-promotion, or approval-as-execution. This boundary is non-negotiable and MUST be enforced at the implementation level, not only by convention. |
| I-267 | `/approve` and `/reject` Telegram commands are audit-only journal actions. They MUST write an operator intent record to `artifacts/operator_commands.jsonl`. They MUST NOT call any execution engine, order submission path, or mutate the approval state of any live order. |
| I-268 | `TelegramOperatorBot._cmd_risk()` MUST read exclusively from a public `RiskEngine.get_risk_snapshot()` method returning a typed `RiskSnapshot` model. Direct access to private attributes (`_limits`, `_kill_switch_active`, `_paused`, `_daily_loss_pct`, `_total_drawdown_pct`, `_open_position_count`) is forbidden. |
| I-269 | `/signals` MUST read from `app/research/signals.extract_signal_candidates()` (canonical read surface). The response MUST NOT include execution instructions, routing decisions, or live order references. No side effect on signal state is permitted. |
| I-270 | `/journal` MUST read from `get_review_journal_summary()` and `/daily_summary` MUST read from `get_daily_operator_summary()`. Both handlers MUST remain read-only and MUST NOT write to, delete from, or mutate journal/core state. |
| I-271 | `/pause`, `/resume`, and `/kill` are guarded_write commands. They MUST be dry_run-gated: when `dry_run=True` (default), they MUST return a "[DRY RUN] No action taken" response without mutating any state. No guarded_write command may bypass the dry_run gate. |
| I-272 | `/kill` MUST require a two-step confirmation via the `_pending_confirm` pattern. A single `/kill` invocation MUST NOT activate the kill switch. The pending confirmation MUST be per-`chat_id` and MUST be consumed on confirm. |
| I-273 | Every Telegram command MUST be audit-logged to `artifacts/operator_commands.jsonl` via `_audit()` BEFORE any handler logic runs. Audit-log write failure MUST be logged as error but MUST NOT prevent the command response from being sent. |
| I-274 | Commands from non-admin `chat_id` values MUST be logged and fail-closed with "Unauthorized. This incident is logged." No command from an unauthorized chat_id may reach any handler. The response MUST be generic â€” no internal detail disclosed. |
| I-275 | `TelegramOperatorBot` MUST be covered by at least 20 unit tests in `tests/unit/test_telegram_bot.py`. Required coverage: admin gating, unknown command rejection, dry_run behavior of all three guarded_write commands, audit logging on each command, `/kill` two-step confirm, response structure for all 15 commands. |
| I-276 | The canonical Telegram command surface is defined in `TELEGRAM_INTERFACE.md`. Any sprint that changes the command surface (adds, removes, or reclassifies a command) MUST update both `TELEGRAM_INTERFACE.md` and `docs/contracts.md Â§49` within the same sprint. |
| I-277 | Telegram commands are NOT MCP tools. They share no tool inventory with `app/agents/mcp_server.py`. A Telegram command that reads from a canonical MCP read surface calls the MCP function directly (via lazy import) â€” it MUST NOT route through the MCP tool dispatch layer. |
| I-278 | `_READ_ONLY_COMMANDS` and `_GUARDED_AUDIT_COMMANDS` in `telegram_bot.py` MUST be disjoint frozensets. No command may appear in both sets. `incident` is classified as `guarded_audit` and MUST NOT appear in `_READ_ONLY_COMMANDS`. This was corrected in Sprint 38C. |
| I-279 | All canonical read commands (those in `_READ_ONLY_COMMANDS`) call exactly one MCP canonical read function via `_load_canonical_surface()`. Every such response MUST contain `execution_enabled=False` and `write_back_allowed=False`. Any response missing these fields MUST be treated as misconfigured and the response MUST be rejected fail-closed. |
| I-280 | `get_telegram_command_inventory()` is the machine-readable Telegram surface contract. `test_telegram_command_inventory_references_registered_cli_research_commands` MUST pass in every sprint that touches `telegram_bot.py`. This test verifies that all CLI refs in `TELEGRAM_CANONICAL_RESEARCH_REFS` map to existing registered CLI research commands. |

| I-281 | Market data adapters are read-only. No method of any `BaseMarketDataAdapter` implementation may submit orders, open positions, send broker instructions, or mutate any execution state. The adapter layer is a passive data source â€” it has no write access to any broker system. This boundary is non-negotiable and must be enforced at the implementation level. |
| I-282 | `MarketDataPoint.is_stale` is authoritative. A stale data point (is_stale=True) MUST NOT be used as execution input without explicit operator override via a separate guarded mechanism. The TradingLoop MUST skip the cycle when `is_stale=True` or when the return value is `None`. There is no automatic retry, fallback, or re-routing. |
| I-283 | `MarketDataPoint.source` MUST be set by the adapter â€” never inferred, defaulted, or overwritten by the consumer. The source field is a provenance tag. It is NOT a routing signal and NOT a permission check. Signals derived from a MarketDataPoint SHOULD propagate the source value for traceability. |
| I-284 | `BaseMarketDataAdapter.health_check()` returning `False` MUST NOT be interpreted as a routing trigger or a stop-trading signal. Provider health is a liveness indicator for monitoring only. Automatic provider switching in response to health_check() failure is forbidden. The kill-switch authority belongs to the RiskEngine, not the market data layer. |
| I-285 | All `BaseMarketDataAdapter` methods MUST implement the never-raise contract: transient fetch failures return `None` (get_ticker, get_price, get_market_data_point) or `[]` (get_ohlcv), never raise. Internal errors MUST be logged at WARNING level before returning the null value. health_check() returns `False` on any error â€” never raises. |
| I-286 | `BacktestEngine.run(signals, prices)` receives market data as a pre-fetched `dict[str, float]`. No adapter call may occur inside `BacktestEngine.run()`. This ensures deterministic backtest replay and prevents live data contamination of historical simulations. See I-234. |
| I-287 | `MockMarketDataAdapter` is the mandatory default data source for paper trading and all unit tests that do not require real market data. Its prices are deterministic (hash-based sinusoidal, 24h period, no random()). Tests that depend on specific price values MUST use `MockMarketDataAdapter`. The mock MUST NOT be replaced by a real adapter without updating tests. |
| I-288 | Market data adapter selection is explicit configuration (DI / settings). No automatic fallback chain between adapters is permitted. A `TradingLoop` or any consumer is constructed with exactly one adapter â€” it does not switch providers at runtime. Provider changes require explicit reconfiguration and restart. |
| I-289 | A real external market data adapter (e.g. Binance, Alpaca) MUST implement `BaseMarketDataAdapter` completely. Every abstract method MUST be overridden. Any unimplemented method MUST raise `NotImplementedError` â€” not silently return `None`. Partial implementations are forbidden. |
| I-290 | All `MarketDataPoint.timestamp_utc` values MUST be UTC-aware datetimes. Naive datetimes are invalid. Adapters MUST ensure UTC-awareness before constructing the dataclass. Consumers that receive a naive timestamp MUST treat it as a data error and log a warning â€” they MUST NOT silently assume UTC. |

| I-291 | `PaperPortfolio` (mutable runtime state) MUST NEVER be directly exposed to any operator surface, MCP tool, CLI command, or Telegram handler. Only `PortfolioSnapshot` (frozen, read-only projection from `app/execution/portfolio_read.py`) may cross the boundary into operator-facing surfaces. `app/execution/portfolio_surface.py` is an internal TradingLoop helper â€” it MUST NOT be used as an operator surface. |
| I-292 | `PortfolioSnapshot` and `ExposureSummary` in `app/execution/portfolio_read.py` MUST be frozen dataclasses with `execution_enabled=False` and `write_back_allowed=False` as non-overridable fields. Any portfolio surface response that omits or sets these fields to True MUST be treated as a configuration error and rejected fail-closed. `PositionSummary` MUST be frozen but does NOT carry execution flags (it is always embedded inside `PortfolioSnapshot`). |
| I-293 | The canonical source of truth for portfolio state reconstruction is `artifacts/paper_execution_audit.jsonl`. `build_portfolio_snapshot()` in `app/execution/portfolio_read.py` MUST replay `order_filled` events from this JSONL to reconstruct current positions. No live `PaperExecutionEngine` instance may be accessed via MCP or CLI. The function is async to allow optional mark-to-market enrichment via `get_market_data_snapshot()`. |
| I-294 | Mark-to-Market enrichment is optional and fail-closed per `PositionSummary`. If `MarketDataSnapshot.available=False` or `is_stale=True`, the corresponding `PositionSummary.market_data_available` and `market_data_is_stale` flags MUST reflect this. `market_price` and `market_value_usd` MUST be `None` when unavailable. `unrealized_pnl_usd` MUST be `None` when market price is unavailable. The `PortfolioSnapshot` MUST still be returned â€” MtM failure per position is NOT a fatal snapshot error. |
| I-295 | `ExposureSummary` (in `portfolio_read.py`) is a derived projection of `PortfolioSnapshot`. `build_exposure_summary(snapshot)` MUST accept a `PortfolioSnapshot` and derive the exposure view from it. It MUST NOT independently fetch market data or replay the audit JSONL. The only canonical path is: audit_jsonl â†’ `build_portfolio_snapshot()` â†’ `PortfolioSnapshot` â†’ `build_exposure_summary()`. |
| I-296 | Telegram `/positions` MUST be backed by `get_paper_positions_summary` MCP (canonical_read). The provisional `get_handoff_collector_summary` backing is superseded as of Sprint 40. `TELEGRAM_CANONICAL_RESEARCH_REFS["positions"]` MUST reference `"research paper-positions-summary"`. |
| I-297 | Telegram `/exposure` MUST be backed by `get_paper_exposure_summary` MCP (canonical_read) as of Sprint 40. The stub implementation is superseded. `"exposure"` MUST appear in `_READ_ONLY_COMMANDS`. `TELEGRAM_CANONICAL_RESEARCH_REFS["exposure"]` MUST reference `"research paper-exposure-summary"`. |
| I-298 | `get_paper_portfolio_snapshot`, `get_paper_positions_summary`, and `get_paper_exposure_summary` MUST be registered in `_CANONICAL_MCP_READ_TOOL_NAMES`. They MUST NOT be in `_GUARDED_MCP_WRITE_TOOL_NAMES`. All three are read-only surfaces over `app/execution/portfolio_read.py`. |
| I-299 | `PositionSummary.symbol` is the canonical position key in `PortfolioSnapshot`. Positions are keyed and sorted by symbol. There is no separate `position_id` field â€” the `symbol` field is stable and unique per open position in the paper portfolio (one position per symbol at a time). |
| I-300 | `app/execution/portfolio_read.py` is the canonical operator portfolio surface module. `app/execution/portfolio_surface.py` is an internal helper for TradingLoop-side formatting (provides `build_portfolio_summary(portfolio, prices)` and `build_exposure_summary(portfolio, prices)` working from a live `PaperPortfolio` object). These two modules serve different roles and MUST NOT be confused. Only `portfolio_read.py` feeds MCP tools, CLI commands, and Telegram handlers. |

| I-301 | `LoopStatusSummary` (`app/orchestrator/models.py`) is a frozen read-only projection of TradingLoop operational state derived exclusively from `artifacts/trading_loop_audit.jsonl` via `build_loop_status_summary()`. It MUST NOT carry live engine references. `auto_loop_enabled` MUST always be `False` â€” there is no autonomous background loop. `execution_enabled` and `write_back_allowed` MUST always be `False`. |
| I-302 | `run_trading_loop_once()` (`app/orchestrator/trading_loop.py`) MUST reject any `mode` not in `{ExecutionMode.PAPER, ExecutionMode.SHADOW}` via `_run_once_guard()` raising `ValueError` before any cycle executes. `mode="live"` is always rejected fail-closed. No fallback, no promotion, no retry. |
| I-303 | `run_trading_loop_once()` defaults to `provider="mock"`, which instantiates `MockMarketDataAdapter`. No real external adapter may be used in the default run-once path. The default `analysis_profile="conservative"` generates a non-actionable `AnalysisResult` â€” this intentionally produces a `CycleStatus.NO_SIGNAL` cycle without paper order creation. |
| I-304 | The TradingLoop has no autonomous execution path. There is no daemon, no scheduler, no background thread, no polling loop. All cycle execution is triggered exclusively by explicit operator calls: MCP `run_trading_loop_once` (guarded_write) or CLI `research trading-loop-run-once`. |
| I-305 | `run_trading_loop_once()` creates a fresh `PaperExecutionEngine` per call (no portfolio replay from `paper_execution_audit.jsonl`). If a cycle completes with a simulated fill, the `PaperExecutionEngine` writes the fill to `execution_audit_path` (default: `artifacts/paper_execution_audit.jsonl`). This is correct behavior â€” run-once paper fills ARE part of the paper execution audit. |
| I-306 | `artifacts/trading_loop_audit.jsonl` is append-only. `run_trading_loop_once()` appends exactly one `LoopCycle` record per call. No read-modify-write, no truncation, no deletion. |
| I-307 | `build_loop_status_summary(audit_path, mode)` in `app/orchestrator/trading_loop.py` is the canonical read function for TradingLoop operational state. It is synchronous, pure, and never raises. Missing audit file â†’ empty `LoopStatusSummary(total_cycles=0, ...)` without exception. `app/orchestrator/loop_surface.py` has been **REMOVED** from the filesystem (Sprint 41C). Do not reference it. |
| I-308 | `run_trading_loop_once` MUST be registered in `_GUARDED_MCP_WRITE_TOOL_NAMES`. It MUST NOT appear in `_CANONICAL_MCP_READ_TOOL_NAMES`. `get_trading_loop_status` and `get_recent_trading_cycles` MUST be registered in `_CANONICAL_MCP_READ_TOOL_NAMES`. `get_loop_cycle_summary` is a compatibility alias for `get_recent_trading_cycles`. |
| I-309 | `research trading-loop-run-once` CLI command MUST be registered (it is already declared in `FINAL_RESEARCH_COMMAND_NAMES`). Until registered, `test_research_command_inventory_matches_registration_and_help` fails â€” this is the Sprint 41 implementation blocker. Once registered, the test MUST pass. |
| I-310 | `LoopStatusSummary.run_once_allowed` is the canonical runtime gate: `True` only when `mode in {paper, shadow}`. Consumers MUST check `run_once_allowed` before presenting run-once options to the operator. `run_once_block_reason` MUST be non-None whenever `run_once_allowed=False`. |

## Sprint 42+42C â€” Telegram Webhook Hardening (I-311â€“I-320) âœ…

> **Sprint 42C (2026-03-21)**: Invarianten korrigiert auf tatsÃ¤chliche Implementierung. I-311/I-312/I-313/I-315/I-316/I-318/I-319/I-320 wurden mit falschen Namen geschrieben und sind hier finalisiert.

| ID | Invariant |
|---|---|
| I-311 | Webhook-Hardening ist **vollstÃ¤ndig in `app/messaging/telegram_bot.py` integriert** (kein separates Modul). `TelegramWebhookProcessResult` (frozen dataclass: `accepted`, `processed`, `rejection_reason`, `update_id`, `update_type`) ist das kanonische Resultat-Modell. Kein separates Legacy-Validator-/Webhook-Modul. |
| I-312 | `TelegramOperatorBot.process_webhook_update()` MUST never raise. Every failure condition returns a `TelegramWebhookProcessResult` with `accepted=False` and an appropriate `rejection_reason`. No exception propagates to the caller. |
| I-313 | `webhook_secret_token=None/""` at construction â†’ `rejection_reason="webhook_secret_not_configured"` on every request â€” fail-closed. The `webhook_configured` property MUST be checked before processing. `webhook_signature_required: True` in runtime config is satisfied when `webhook_secret_token` is non-empty. |
| I-314 | Secret-Token mismatch â†’ `rejection_reason="invalid_secret_token"`. Missing header â†’ `rejection_reason="missing_secret_token_header"`. Secret comparison MUST use `hmac.compare_digest` (constant-time). Implemented as `_constant_time_secret_match()`. |
| I-315 | `edited_message` is **ALLOWED** by default (`_WEBHOOK_ALLOWED_UPDATES_DEFAULT = ("message", "edited_message")`). Replay risk is mitigated by `update_id` deduplication (see I-316). Operators MAY restrict to `("message",)` only via `webhook_allowed_updates` constructor param. |
| I-316 | Replay deduplication uses an in-memory `OrderedDict` with FIFO eviction at `maxlen=2048` (not `deque`). A duplicate `update_id` â†’ `rejection_reason="duplicate_update_id"`. Buffer is NOT persisted. Restart = empty buffer. This is safe: Telegram confirms delivery on HTTP 200. |
| I-317 | `TelegramOperatorBot.process_update()` MUST only be called when `TelegramWebhookProcessResult.accepted is True`. No rejected update reaches the command dispatch layer. `_reject_webhook()` returns without calling `process_update()` in all error paths. |
| I-318 | Rejected webhook requests MUST produce exactly one audit append to `artifacts/telegram_webhook_rejections.jsonl` (rejections only â€” not all requests). Accepted requests are covered by the existing `artifacts/operator_commands.jsonl`. No secrets or credentials in the audit. |
| I-319 | `get_webhook_status_summary()` returns `execution_enabled=False` and `write_back_allowed=False` as structural invariants. The webhook hardening layer has no execution path and no state mutation path beyond the in-memory replay buffer. |
| I-320 | `webhook_secret_token` is a constructor parameter of `TelegramOperatorBot` (NOT an `OperatorSettings` field). The caller is responsible for sourcing it from `OPERATOR_TELEGRAM_WEBHOOK_SECRET` env-var. Empty string = fail-closed (`webhook_secret_not_configured`). |

## Sprint 43+43C â€” FastAPI Operator API Surface (I-321â€“I-330) âœ…

> **Sprint 43C (2026-03-21):** Invarianten basieren auf tatsÃ¤chlicher Implementierung in `app/api/routers/operator.py`. Sprint-43-Entwurf hatte andere Endpunkt-Namen und Webhook-Endpoints die nicht implementiert wurden.

| ID | Invariant |
|---|---|
| I-321 | `app/api/routers/operator.py` ist das kanonische Operator-API-Modul. Es enthÃ¤lt **keine eigene Business-Logik** â€” ausschliesslich Delegation an bestehende MCP-Funktionen (`mcp_server.*`). Kein Datenbankzugriff, kein direkter Adapter-Zugriff, kein Scheduling. |
| I-322 | `require_operator_api_token` ist eine FastAPI-Dependency-Funktion (DI), gesetzt als `dependencies=[Depends(require_operator_api_token)]` auf dem gesamten Router. Sie ist NICHT Ã¼ber die bestehende `app/security/auth.py`-Bearer-Middleware implementiert. Leer `APP_API_KEY` â†’ 503. Kein Header â†’ 401. Falscher Token â†’ 403. Tokenvergleich via `secrets.compare_digest` (constant-time). |
| I-323 | `GET /operator/status` und `GET /operator/readiness` sind **Aliases** â€” beide rufen `mcp_server.get_operational_readiness_summary()` ohne weitere Parameter auf. Kein inhaltlicher Unterschied. Beide sind read_only. |
| I-324 | Alle 7 read-only Endpoints sind **pure Passthrough** â€” der Router fÃ¼gt keine Transformation, kein Enrichment und kein Fallback-Handling hinzu. Die `execution_enabled=False`- und `write_back_allowed=False`-Invarianten kommen aus den Backing-MCP-Funktionen (nicht vom Router). |
| I-325 | `POST /operator/trading-loop/run-once` delegiert vollstÃ¤ndig an `mcp_server.run_trading_loop_once(...)`. Ein `ValueError` aus dem MCP-Layer (z.B. bei `mode=live`) wird als HTTP 400 an den Aufrufer weitergegeben. Der Router setzt keinen eigenen Mode-Guard. |
| I-326 | `auto_loop_enabled=False` im Response von `GET /operator/trading-loop/status` ist eine Strukturinvariante von `LoopStatusSummary` (I-301). Der Router setzt dieses Feld nicht â€” es kommt aus `get_trading_loop_status()`. |
| I-327 | Explizit verbotene Pfade im Operator-Router: `/operator/trade`, `/operator/execute`, `/operator/order`, `/operator/fill`, `/operator/broker`, `/operator/live`. Diese Pfade dÃ¼rfen NIEMALS registriert werden. `test_no_trading_routes` verifiziert dies. |
| I-328 | `GET /operator/webhook-status` und `POST /operator/webhook` (Telegram-Webhook-Delegation via `app.state.telegram_bot`) sind in Sprint 43 **NICHT implementiert**. Sie sind Sprint-43+-Backlog. Die entsprechenden Tests in `test_operator_api.py` (8 failing) dokumentieren diese offene Arbeit. |
| I-329 | `tests/unit/test_api_operator.py` (13 Tests, alle passing) ist die kanonische Implementierungsreferenz fÃ¼r Sprint 43. `tests/unit/test_operator_api.py` (9 Tests, 8 failing) ist eine stale-spec-Datei aus der ursprÃ¼nglichen Â§54-Planung und beschreibt Endpunkte die nicht implementiert wurden. |
| I-330 | Kein Operator-Endpoint darf `execution_enabled=True` zurÃ¼ckgeben. Kein Operator-Endpoint darf `write_back_allowed=True` zurÃ¼ckgeben. Diese Invarianten sind strukturell (backing MCP-Funktionen setzen sie) â€” kein Enforcement auf Ebene des Routers nÃ¶tig. |


## Sprint 44 â€” Operator API Hardening & Request Governance (I-331â€“I-340)

> **Sprint 44 (2026-03-21):** Invarianten fuer den Transport-/Governance-Layer auf Basis des kanonischen Operator-API-Surface aus Sprint 43. Keine neue Business-Logik, keine neuen Endpoints, keine neue Execution-Surface.

| ID | Invariant |
|---|---|
| I-331 | Jeder `/operator/*`-Response MUSS eine `request_id` (UUID4) auf Top-Level enthalten. Die `request_id` MUSS ausserdem als `X-Request-Id` Response-Header propagiert werden. Eine fehlende oder leere `request_id` ist ein Implementierungsfehler. |
| I-332 | Der Client KANN eine `request_id` via `X-Request-Id` Request-Header vorgeben. Wenn der Wert ein valides UUID4-Format hat, wird er uebernommen. Ungueltige oder fehlende Header-Werte â†’ server-generiertes UUID4. Die Entscheidung erfolgt in `get_request_id()` (Dependency). |
| I-333 | `artifacts/operator_api_audit.jsonl` ist das kanonische Operator-API-Audit-Log. Es wird fuer JEDEN `/operator/*`-Request geschrieben, der die Auth-Pruefung bestanden hat. Audit-Fehler sind nicht fatal â€” sie werden auf WARNING geloggt. Das Log enthaelt keine Secrets, Tokens oder Credentials. |
| I-334 | `POST /operator/trading-loop/run-once` akzeptiert optionalen `X-Idempotency-Key` Header. Wenn gesetzt und bereits im In-memory-Buffer gesehen â†’ HTTP 409 mit `error="duplicate_idempotency_key"`. Wenn nicht gesetzt â†’ kein Idempotency-Check. Der Idempotency-Buffer ist kein Scheduler und startet keine Zyklen. |
| I-335 | Der Idempotency-Buffer ist in-memory (`OrderedDict` FIFO, maxlen=256, analog Telegram-Replay-Buffer I-316). Er ist NICHT persistent. Prozess-Neustart = leerer Buffer. Cluster-Konsistenz ist nicht garantiert und nicht erforderlich. |
| I-336 | ALLE Fehlerantworten aus `/operator/*` MUESSEN der kanonischen Error-Shape folgen: `{"error": "<code>", "detail": "<human>", "request_id": "<uuid>"}`. Kein unstrukturierter Error-String. Die `error`-Codes sind in Â§55.5 kanonisch definiert. |
| I-337 | `endpoint_class` im Audit-Log ist `"read_only"` fuer alle GET-Endpoints, `"guarded_write"` fuer `POST /operator/trading-loop/run-once`. Kein anderer Wert ist zulaessig. |
| I-338 | Das Audit-Log `artifacts/operator_api_audit.jsonl` enthaelt NIEMALS: Bearer-Tokens, API-Keys, Request-Bodies, Response-Bodies, Nutzlast-Details. Nur Transport-Metadaten: request_id, method, path, endpoint_class, idempotency_key, outcome, http_status, execution_enabled, write_back_allowed. |
| I-339 | HTTP 500 aus `/operator/*` darf niemals einen nackten Stacktrace oder eine unstrukturierte Fehlermeldung zurueckgeben. Jede unbehandelte Exception â†’ `{"error": "internal_error", "detail": "<sanitized>", "request_id": "<uuid>"}` mit HTTP 500. `request_id` ist auch bei 500 verpflichtend (falls generiert). |
| I-340 | Sprint 44 oeffnet keinen neuen Live-, Broker-, Routing- oder Trading-Pfad. `mode=live` bleibt `error_code="mode_not_allowed"` (HTTP 400) aus dem kanonischen TradingLoop-Guard. Kein neues Execution-Gate wird durch Hardening eingefuehrt. |


> **Sprint 44C (2026-03-22):** I-331â€“I-340 (oben) wurden auf Basis des Â§55-Entwurfs geschrieben und enthalten Drift. Kanonische, implementierungsbasierte Korrekturen:

| ID | Invariant (kanonisch, Sprint 44C) |
|---|---|
| I-331C | Jeder `/operator/*`-Response enthÃ¤lt `X-Request-ID` und `X-Correlation-ID` als Response-Headers. `request_id` hat das Format `req_<uuid4_hex>` (nicht UUID4). Client KANN via `X-Request-ID` Request-Header eigene ID vorgeben (validiert via `^[A-Za-z0-9._:-]{1,128}$`). |
| I-332C | `X-Correlation-ID` ist ein zweiter Kontext-Identifier: wenn Client nicht setzt, default = request_id. Beide werden in `request.state` gespeichert und in ALLEN Fehler-Payloads propagiert. `bind_operator_request_context` ist die kanonische Dependency. |
| I-333C | Das Audit-Log ist `artifacts/operator_api_guarded_audit.jsonl` â€” ausschliesslich fÃ¼r `POST /operator/trading-loop/run-once` (guarded POST), NICHT fÃ¼r alle Operator-Requests. Felder: event, endpoint, request_id, correlation_id, idempotency_key, outcome, error_code, idempotency_replayed, mode, symbol, provider, execution_enabled=false, write_back_allowed=false. |
| I-334C | `Idempotency-Key` Header (nicht `X-Idempotency-Key`) ist **REQUIRED** fÃ¼r `POST /operator/trading-loop/run-once`. Fehlt er â†’ HTTP 400 `missing_idempotency_key`. Replay-Verhalten: gleicher Key + gleicher SHA256-Fingerprint â†’ stored Response zurÃ¼ck, keine NeuausfÃ¼hrung. Gleicher Key + anderer Fingerprint â†’ HTTP 409 `idempotency_key_conflict`. |
| I-335C | Der Idempotency-Buffer ist `OrderedDict[str, _IdempotencyRecord]` (maxlen=256, FIFO, Thread-safe mit Lock). `_IdempotencyRecord` enthÃ¤lt `request_fingerprint` und `response_payload`. Ein Replay setzt `idempotency_replayed=True`. Replay-Requests zÃ¤hlen NICHT gegen den Rate-Limiter. |
| I-336C | Alle Fehlerantworten aus `/operator/*` haben die verschachtelte Shape: `{"error": {"code": "<code>", "message": "<msg>", "request_id": "<id>", "correlation_id": "<id>"}, "execution_enabled": false, "write_back_allowed": false}`. Auth-Fehler-Codes: `operator_api_disabled`, `missing_authorization_header`, `invalid_authorization_scheme`, `invalid_api_key`. |
| I-337C | Sliding-Window Rate-Limiter fÃ¼r `POST /operator/trading-loop/run-once`: 5 Requests pro 30 Sekunden pro `operator_subject` (= `token_<sha256[:16]>` des Bearer-Tokens). Ãœberschreitung â†’ HTTP 429 `guarded_rate_limited`. In-memory (`deque[float]`), nicht persistent, Thread-safe (Lock). |
| I-338C | `_append_guarded_audit()` ist never-raise: `OSError` â†’ silent `return`, keine Caller-sichtbare Exception. Der `_WORKSPACE_ROOT` Pfad ist via `Path(__file__).resolve().parents[3]` gesetzt â€” testbar via `monkeypatch.setattr(operator_router, "_WORKSPACE_ROOT", tmp_path)`. |
| I-339C | `_resolve_read_payload()` ist der kanonische Wrapper fÃ¼r alle read-only Endpoints. Er setzt Context-Headers und fÃ¤ngt JEDE Exception â†’ HTTP 503 mit endpoint-spezifischem Error-Code (z.B. `status_unavailable`, `portfolio_snapshot_unavailable`). |
| I-340C | Sprint 44 oeffnet keinen neuen Live-, Broker- oder Execution-Pfad. `_reset_operator_guard_state_for_tests()` ist eine Test-Helper-Funktion â€” sie leert `_IDEMPOTENCY_CACHE` und `_GUARDED_RATE_LIMIT_BUCKETS` fÃ¼r deterministische Unit-Tests. |


---

## Sprint 45 â€” S45_OPERATOR_USABILITY_BASELINE (2026-03-22)

| ID | Invariant |
|---|---|
| I-341 | `get_daily_operator_summary` ist ein reiner Aggregations-Tool. Er oeffnet keinen eigenen Datenpfad, keine externe API, keine DB-Verbindung. Alle Daten kommen ausschliesslich aus Delegation an bestehende MCP-Tools (`get_operational_readiness_summary`, `get_recent_trading_cycles`, `get_paper_portfolio_snapshot`, `get_paper_exposure_summary`, `get_decision_pack_summary`, `get_review_journal_summary`). |
| I-342 | `execution_enabled=false` und `write_back_allowed=false` sind invariante Felder in JEDEM `daily_operator_summary`-Response â€” unabhaengig vom Aggregations-Ergebnis. |
| I-343 | Aggregation ist best-effort: wenn ein Sub-Tool eine Exception wirft, gibt `get_daily_operator_summary` einen Response mit degradierten Feldern zurueck (Fallback-Werte). Es wird keine Exception propagiert. Das `sources`-Feld listet nur die Sub-Tools, die erfolgreich beigetragen haben. |
| I-344 | `report_type` ist immer `"daily_operator_summary"`. Kein anderer Wert ist zulaessig. |
| I-345 | Die CLI-Ausgabe von `trading-bot research daily-summary` ist menschenlesbar â€” kein JSON-Dump ohne expliziten `--json`-Flag. |
| I-346 | `GET /operator/daily-summary` unterliegt denselben Auth- und Governance-Guardrails wie alle anderen `/operator/*`-Endpoints: Bearer-Token, fail-closed, X-Request-ID/X-Correlation-ID, kanonische Error-Shape. |
| I-347 | Telegram `/daily_summary` delegiert an `get_daily_operator_summary`. Keine separate Aggregations-Logik in `telegram_bot.py`. Surface-Drift ist per Konstrukt ausgeschlossen. |
| I-348 | Sprint 45 oeffnet keinen Live-, Broker- oder Execution-Pfad. `mode=live` bleibt fail-closed in allen vier neuen Surfaces. |
| I-349 | Das `sources`-Feld im Response dokumentiert welche Sub-Tools zu diesem Aggregat beigetragen haben. Es ist eine Liste von Strings (Tool-Namen). Bei vollstaendigem Erfolg: alle 6 Tools. Bei Teilausfall: nur die erfolgreichen. |
| I-350 | Sprint 45 fuegt keinen neuen Aggregations-Layer hinzu, der parallel zum bestehenden MCP-Tool-Layer existiert. Der Daily Operator View ist eine Aggregations-Fassung bestehender Tools â€” kein zweiter Stack. |



---

> **Sprint 45C (2026-03-22):** I-341â€“I-350 (oben) basieren auf dem Â§56-Entwurf.
> Kanonische Korrekturen:

| ID | Invariant (kanonisch, Sprint 45C) |
|---|---|
| I-341C | `get_daily_operator_summary` hat keinen eigenen Datenpfad. Es ruft 6 bestehende MCP-Tools via `_safe_daily_surface_load` auf und uebergibt die Ergebnisse an `build_daily_operator_summary` aus `app/research/operational_readiness.py`. |
| I-342C | `execution_enabled=False` und `write_back_allowed=False` sind invariante Felder in `DailyOperatorSummary.to_json_dict()`. Der Dataclass-Default setzt beide. |
| I-343C | Aggregation ist best-effort via `_safe_daily_surface_load`: Exception in einem Sub-Tool â†’ `None` zurueck â†’ `build_daily_operator_summary` erhaelt `None` fuer diese Sektion â†’ Fallback-Werte. Das `sources`-Feld enthaelt nur Sub-Tool-Namen die erfolgreich `dict`-Payload geliefert haben. |
| I-344C | `report_type` ist immer `"daily_operator_summary"`. `interface_mode` ist immer `"read_only"`. Beide sind Dataclass-Defaults und werden nie vom Caller ueberschrieben. |
| I-345C | CLI `trading-bot research daily-summary` importiert `get_daily_operator_summary` aus `mcp_server` und gibt human-readable Tabellenformat aus. `--json` Flag gibt kanonisches JSON-Schema aus. |
| I-346C | `GET /operator/daily-summary` delegiert via `_resolve_read_payload` an `mcp_server.get_daily_operator_summary` mit `error_code="daily_summary_unavailable"`. Gleiche Auth + Request-Governance wie alle anderen `/operator/*`-Endpoints. |
| I-347C | Telegram `/daily_summary` delegiert an `self._get_daily_operator_summary` â†’ `mcp_server.get_daily_operator_summary`. Format: Markdown mit `_inline`-escaped Werten. Kein JSON-Dump. Kein eigenstaendiger Aggregations-Pfad. |
| I-348C | Sprint 45 oeffnet keinen Live-, Broker- oder Execution-Pfad. `execution_enabled=False` und `write_back_allowed=False` sind in allen vier Surfaces (MCP, CLI, API, Telegram) invariant. |
| I-349C | `sources` ist eine Liste von Strings der Sub-Tool-Namen die `dict`-Payload geliefert haben: `"readiness_summary"`, `"recent_cycles"`, `"portfolio_snapshot"`, `"exposure_summary"`, `"decision_pack_summary"`, `"review_journal_summary"`. Source-Name ist `"recent_cycles"` (nicht `"recent_cycles_summary"`). |
| I-350C | `build_daily_operator_summary` ist eine pure Funktion ohne I/O. Sie empfaengt nur `dict | None` Parameter. Die Testbarkeit ist dadurch direkt (kein Mock erforderlich fuer Unit-Tests der Aggregations-Logik). |



---

## Sprint 46 â€” S46_OPERATOR_DASHBOARD_BASELINE (2026-03-22)

Status: implemented (Codex). Dashboard remains read-only and daily-summary-backed.

| ID | Invariant |
|---|---|
| I-351 | `GET /dashboard` ist eine reine Praesentation-Surface. Sie ruft `mcp_server.get_daily_operator_summary()` auf und rendert das Ergebnis als HTML. Kein eigener Datenpfad, kein zweiter Aggregat-Layer. |
| I-352 | Das Dashboard enthaelt keinen JavaScript-Code. Kein JS-Framework, keine fetch-Calls vom Browser, keine WebSockets. Nur statisches HTML mit Inline-CSS. |
| I-353 | `execution_enabled` und `write_back_allowed` muessen im Dashboard-HTML sichtbar als `False` erscheinen. Sie sind Pflichtfelder der visuellen Ausgabe. |
| I-354 | Fail-closed: leeres `APP_API_KEY` â†’ HTTP 503. Das Dashboard erfordert keinen Bearer-Token vom Browser, prueft aber ob ein API-Key konfiguriert ist. |
| I-355 | Auto-Refresh via `<meta http-equiv="refresh" content="60">`. Kein JavaScript-Polling. |
| I-356 | Bei Exception in `get_daily_operator_summary` gibt das Dashboard eine HTML-Fehlerseite aus (Status "unavailable") â€” kein Python-Stack-Trace, kein HTTP 500 mit JSON-Body. |
| I-357 | Keine neue externe Dependency (kein Jinja2, kein Chart-Lib, kein CSS-Framework). HTML via f-string Template in `app/api/routers/dashboard.py`. |
| I-358 | Das Dashboard-Modul ist `app/api/routers/dashboard.py`. `app/api/main.py` includet den Router. Kein neues CLI-Command, kein neues Telegram-Command, kein neues MCP-Tool. |
| I-359 | Farb-Konvention (CSS-only): `readiness_status == "ok"` gruen, `"warning"` orange, alles andere rot. Nur fuer visuelle Schnellorientierung â€” kein funktionaler Unterschied. |
| I-360 | Sprint 46 oeffnet keinen Live-, Broker-, Execution- oder guarded-Action-Pfad. Dashboard ist ausschliesslich read-only und zeigt denselben Stand wie `GET /operator/daily-summary`. |

## S46C â€” Dashboard Path Freeze (2026-03-22)

### Canonical Runtime Path (einzig gÃ¼ltig)

| Surface | Route | Implementation |
|---|---|---|
| Operator Dashboard | `GET /dashboard` | `app/api/routers/dashboard.py` |

### Explizit NICHT implementiert

- `/static/dashboard.html` â€” **existiert nicht**, wurde nie implementiert, ist S46 Out-of-Scope
- `StaticFiles`-Mount â€” **nicht vorhanden** in `app/api/main.py`
- Jinja2-Template â€” **nicht installiert**, nicht verwendet

### Teststand-Drift ErklÃ¤rung

| Baseline | Wert | Bedeutung |
|---|---|---|
| S45C freeze reference | 1498 passed | Daily Operator Summary frozen |
| S46 implementation | 1503 passed | +5 Dashboard-Tests |
| S46C freeze | **1503 passed, ruff clean** | Einzige gÃ¼ltige Referenz ab jetzt |

### S46C Invariant-ErgÃ¤nzungen

| # | Invariant |
|---|---|
| I-361 | Kein `/static/dashboard.html` und kein `StaticFiles`-Mount. Einziger Dashboard-Einstieg ist `GET /dashboard` via `app/api/routers/dashboard.py`. |
| I-362 | Teststand 1503 (nicht 1498). 1498 war S45C-Baseline; 1503 ist S46-Baseline mit 5 neuen Dashboard-Tests in `tests/unit/test_api_dashboard.py`. |
| I-363 | `app/security/auth.py` whitelistet `/dashboard` und `/dashboard/` ohne Bearer-Token â€” kein Browserzwang. Kein anderer Dashboard-Pfad ist whitelistet. |

## S47 â€” Drilldown & History Invarianten (I-364..I-368)

| # | Invariant |
|---|---|
| I-364 | `GET /operator/review-journal` delegiert ausschliesslich an `mcp_server.get_review_journal_summary()` |
| I-365 | `GET /operator/resolution-summary` delegiert ausschliesslich an `mcp_server.get_resolution_summary()` |
| I-366 | Beide Endpoints erfordern Bearer-Auth â€” kein Auth-Whitelist-Eintrag |
| I-367 | `execution_enabled: false`, `write_back_allowed: false` in allen neuen Responses |
| I-368 | Kanonische Drilldown-Kette (Â§59.5) ist implementiert â€” kein zweiter Daily-Aggregat-Pfad |

### S47 Implementierungsstatus (Codex)

- `app/api/routers/operator.py` exponiert:
  - `GET /operator/review-journal` -> `mcp_server.get_review_journal_summary()`
  - `GET /operator/resolution-summary` -> `mcp_server.get_resolution_summary()`
- Beide Endpoints nutzen die bestehende Operator-Governance
  (`Authorization`, `X-Request-ID`, `X-Correlation-ID`, fail-closed Error-Shape).
- API-Coverage erweitert in `tests/unit/test_api_operator.py`
  (je Endpoint success + failure shape).

### Kanonische Drilldown-Kette (S47, normativ)

```
GET /operator/daily-summary      â†’ Tageseinstieg
GET /dashboard                   â†’ Visueller Ueberblick
  â†“
GET /operator/readiness          â†’ Issues-Liste
GET /operator/decision-pack      â†’ Blocking-Decisions
GET /operator/trading-loop/status
GET /operator/trading-loop/recent-cycles?last_n=N
GET /operator/portfolio-snapshot
GET /operator/exposure-summary
  â†“ (neu S47)
GET /operator/review-journal     â†’ Review-Journal-Historie
GET /operator/resolution-summary â†’ Per-Source-AuflÃ¶sungsstatus
```

## S48 â€” Operator Surface Completion Invarianten (I-369..I-373)

Status: CLOSED (S48 completed, baseline 1515 passed / ruff clean).

| # | Invariant |
|---|---|
| I-369 | `/resolution` Telegram delegiert an `mcp_server.get_resolution_summary()` â€” kein eigenes Aggregat |
| I-370 | `/decision_pack` Telegram delegiert an `mcp_server.get_decision_pack_summary()` â€” kein eigenes Aggregat |
| I-371 | Dashboard Drilldown-Referenz: statisches HTML, kein JS, kein zweiter Backend-Call |
| I-372 | Alle neuen Telegram-Outputs: `execution_enabled=False`, `write_back_allowed=False` |
| I-373 | Surface Completion Matrix (Â§61.2) ist autoritativ fÃ¼r Surface-ParitÃ¤t |

### Surface Completion Matrix (normativ, S48-Ziel)

| Surface | daily_summary | readiness | decision_pack | review_journal | resolution | portfolio | exposure | trading_loop |
|---|---|---|---|---|---|---|---|---|
| **API** | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… |
| **CLI** | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… |
| **Telegram** | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | n/a |
| **Dashboard** | âœ… | âœ…(visual) | âœ…(status) | n/a | n/a | âœ… | âœ… | âœ…(last) |

## S48B â€” Scope-Freeze-Invarianten

| # | Invariant |
|---|---|
| I-374 | Â§61 ist der einzige bindende S48-Contract. Kein frÃ¼herer Entwurf (Dashboard-Subpages) ist fÃ¼r S48 gÃ¼ltig. |
| I-375 | Dashboard-Subpages existieren nicht und werden in S48 nicht implementiert. |
| I-376 | S48 liefert exakt 3 Deliverables: Telegram `/resolution`, Telegram `/decision_pack`, Dashboard Drilldown-Referenz. |
| I-377 | Alle neuen Telegram-Commands folgen dem `_load_canonical_surface`-Pattern (kein direktes `mcp_server`-Calling). |

## S49 â€” Alerting/Digest Baseline Invarianten (I-378..I-383)

Status: ACTIVE.

| # | Invariant |
|---|---|
| I-378 | `get_alert_audit_summary()` liest nur `alert_audit.jsonl` â€” kein neues Aggregat, keine Pipeline-Ã„nderung |
| I-379 | `GET /operator/alert-audit` erfordert Bearer-Auth â€” kein Whitelist-Eintrag |
| I-380 | `execution_enabled=False`, `write_back_allowed=False` in allen S49-Responses |
| I-381 | `ALERT_DRY_RUN=true` bleibt Standard â€” S49 verÃ¤ndert keine Alert-Trigger |
| I-382 | `DailyOperatorSummary` und `build_daily_operator_summary` werden in S49 nicht verÃ¤ndert (frozen) |
| I-383 | Kein Digest-Scheduling, kein Cron-Trigger, kein Auto-Dispatch in S49 |

## S50A â€” Canonical Path Inventory Invarianten (I-384..I-389)

Status: ACTIVE (inventory-first consolidation step).

| # | Invariant |
|---|---|
| I-384 | S50A erweitert keine Produktiv-Business-Logik; Fokus ist Inventar und Governance-Klarheit. |
| I-385 | Der kanonische technische Referenzstand bleibt `1519 passed` und `ruff clean`. |
| I-386 | FÃ¼r jede Operator-Surface wird eine eindeutige Klassifikation gefÃ¼hrt: `canonical`, `alias`, `superseded`, `provisional`. |
| I-387 | `CANONICAL_SURFACE_INVENTORY.md` ist die S50A-Source-of-Truth fÃ¼r Surface-Klassifikation und Implementierungsreferenzen. |
| I-388 | Refactoring ist nachgelagert; vor Inventory-Freeze keine Pfad-Umbauten. |
| I-389 | Dashboard bleibt Single-Path (`GET /dashboard`), `/static/dashboard.html` bleibt superseded/absent. |

## S50A Final Review and Freeze Invarianten (I-390..I-392)

| # | Invariant |
|---|---|
| I-390 | Vor S50B muss ein formaler Freeze-Record fuer S50A vorliegen (`S50A_FINAL_REVIEW_AND_FREEZE`). |
| I-391 | Antigravity-Readability-Review und Claude-Governance-Review sind verpflichtende Gate-Pruefungen vor Freeze. |
| I-392 | Das provisional CLI set ist als primÃ¤rer Entscheidungsgegenstand fuer den Freeze zu behandeln. |

### Alerting-Architektur (read-only Operator-Sicht, S49)

```
alert_audit.jsonl (append-only, geschrieben von AlertService)
    â†“
get_alert_audit_summary() [MCP-Tool, NEU S49]
    â†“
GET /operator/alert-audit [API, NEU S49]
Telegram /alert_status   [NEU S49]
```

Bestehende Pipeline bleibt unverÃ¤ndert:
```
pipeline/service.py â†’ AlertService.process_document() â†’ alert_audit.jsonl
                    â†’ AlertService.send_digest() [manuell, via CLI/API/test]
```


---

## Phase-3 / S50A â€” Architecture Stability Note (2026-03-22)

The three-tier intelligence architecture (Rule â†’ Local â†’ External LLM) is stable and unchanged.
Phase-3 Sprint S50 is a consolidation sprint. S50A is documentation-only (canonical path inventory).

**No changes to this architecture file in S50A.**
The inventory covers operator-facing runtime surfaces (MCP, API, CLI, Telegram, Dashboard) â€”
not the analysis tier stack defined in this document.

The intelligence architecture is classified as **canonical** in the S50A inventory:
- Tier 1 (`RuleAnalyzer`): canonical, implemented, in use
- Tier 2 (internal companion model): aspirational â€” Sprint 5 planned, not yet implemented
- Tier 3 (external LLM providers): canonical, implemented, in use

**S50A invariant I-384 applies**: zero code changes to `app/` in S50A.

---

## Phase-4 / PH4A â€” Signal Quality Audit Context (2026-03-22)

Phase 4 opens with `PH4A_SIGNAL_QUALITY_AUDIT_BASELINE`. The three-tier stack is the primary subject of the audit.
Contract and acceptance freeze is completed in `docs/contracts.md` Â§67; execution proceeds on the frozen metric/data-slice/output set only.

### What PH4A Evaluates

PH4A does not change the architecture. It audits the quality of outputs produced by the existing stack:

| Tier | Audit Focus |
|---|---|
| Tier 1 (RuleAnalyzer) | Score distribution, keyword coverage, relevance precision |
| Tier 3 (LLM provider) | Sentiment accuracy, novelty detection quality, actionability of narratives |
| Cross-tier | Signal-to-noise ratio in research outputs; alert precision across tiers |

### Architecture Stability Constraint

- No new tiers, no new providers, no new analysis models are added during PH4A.
- Tier 2 (internal companion model) remains aspirational â€” not a PH4A target.
- The `AnalysisResult` contract boundary (I-384) is unchanged.

### Quality Baseline Anchor

PH4A will produce a **quality baseline record** covering:
- Score distributions across real document samples
- Top quality gaps ranked by operator impact
- Signal-to-noise and alert precision metrics

This record becomes the canonical reference for all Phase-4 expansion decisions (source additions, provider additions, Tier 2 implementation). No expansion sprint opens without referencing it.

**PH4A invariants I-403, I-404, I-405 apply**: no source additions, no provider additions, quality metrics defined before expansion.

