# Intelligence Architecture

## Design Principle

**Reliability > Speed > Depth**

Every document receives a valid `AnalysisResult`. The pipeline never returns empty scores.
Tier depth scales with available resources — the system degrades gracefully, never silently.

KAI must remain operational when no external LLM provider is configured.
OpenAI, Anthropic, and Gemini are amplifiers of quality, not hard runtime prerequisites.

---

## Three-Tier Stack

```
┌─────────────────────────────────────────────────────────────────┐
│  Tier 3 — External LLM Provider (current default)              │
│  OpenAI / Anthropic / Gemini                                    │
│  Full output: all scores, narrative, actionable classification  │
│  Priority range: 1–10  │  Produces SignalCandidates             │
│  Cost: API call per document                                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │ fallback if unavailable
┌──────────────────────────────▼──────────────────────────────────┐
│  Tier 2 — Internal Companion Model (Sprint 5 — planned)        │
│  Local inference: GGUF / ONNX / vLLM endpoint                  │
│  Subset output: sentiment, relevance, impact (conservative)     │
│  Priority range: 1–8   │  Can produce SignalCandidates          │
│  Cost: local compute, no API key required                       │
└──────────────────────────────┬──────────────────────────────────┘
                               │ fallback if unavailable
┌──────────────────────────────▼──────────────────────────────────┐
│  Tier 1 — RuleAnalyzer (implemented, Sprint 4C)                │
│  Deterministic: keyword matching + heuristics                   │
│  Conservative output: relevance only, all others at floor       │
│  Priority range: 1–5   │  Never produces SignalCandidates       │
│  Cost: zero (no model, no network)                              │
└─────────────────────────────────────────────────────────────────┘
```

All three tiers converge on the same downstream contract:
`CanonicalDocument → AnalysisResult → apply_to_document() → research outputs / alert gate`

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

## Tier 1 — RuleAnalyzer

**Status**: Implemented (`app/analysis/rules/rule_analyzer.py`)

**Guaranteed outputs** (always present, deterministic):

| Field | Value |
|-------|-------|
| `relevance_score` | keyword-density heuristic (0.0–0.6) |
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

**Priority ceiling proof** — with all conservative defaults:
```
raw = (relevance×0.30) + (impact×0.30) + (novelty×0.20) + (actionable×0.15) + (quality×0.05)
    ≤ (0.60×0.30) + (0.0×0.30) + (0.5×0.20) + (0×0.15) + (1.0×0.05)
    = 0.18 + 0.0 + 0.10 + 0 + 0.05 = 0.33 → priority = round(0.33×9)+1 = 4
```
Maximum achievable (max relevance + max quality): ~5. SignalCandidate threshold is 8 — gap is intentional.

**Sprint 4C gap** (not yet fixed):
`apply_to_document()` currently requires `llm_output`. Rule-only results are computed but not persisted.
Fix: Sprint 4C Task 4.10 — relax the guard.

---

## Tier 2 — Internal Providers

Tier 2 has two distinct implementations. Both implement `BaseAnalysisProvider` — zero pipeline changes required.

### Tier 2a — InternalModelProvider (`APP_LLM_PROVIDER=internal`)

**Status**: ✅ Implemented (`app/analysis/internal_model/provider.py`)

```
provider_name = "internal"
analysis_source → INTERNAL
```

Rule-based heuristics. No network. Always available. Acts as the guaranteed fallback in `EnsembleProvider`.
Conservative output: `actionable=False`, `sentiment=NEUTRAL`, `impact=0.0`. Priority ceiling ~5.

**Use case**: Last-resort fallback inside EnsembleProvider, or for environments with no model access at all.

### Tier 2b — InternalCompanionProvider (`APP_LLM_PROVIDER=companion`)

**Status**: ✅ Implemented (`app/analysis/providers/companion.py`)

```
provider_name = "companion"
analysis_source → INTERNAL
```

HTTP client to a local OpenAI-compatible endpoint (e.g. Ollama, llama.cpp, vLLM — localhost only).
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

**Priority range with companion**: Typical strong output → priority 8 (SignalCandidate threshold).

### Settings (Implemented)

```python
# app/core/settings.py — ProviderSettings
companion_model_endpoint: str | None = None      # e.g. "http://localhost:11434"
companion_model_name: str = "kai-analyst-v1"
companion_model_timeout: int = 10                # seconds
```

### Factory Routing (Implemented)

```python
# app/analysis/factory.py
"internal"   → InternalModelProvider(keyword_engine)       # always returns instance
"companion"  → InternalCompanionProvider(endpoint, model)  # returns None if endpoint not set
```

### Security Constraints

- `companion_model_endpoint` MUST be `localhost` or an explicitly allowlisted internal address.
- No external network calls from companion provider — local inference only.
- No API keys for companion model — authentication is endpoint-level (internal network).
- Validation at settings load time: reject external URLs for companion endpoint.

---

## Tier 3 — External LLM Provider

**Status**: Implemented (OpenAI, Anthropic, Gemini via `app/integrations/`)

**Teacher role**: Tier 3 outputs serve as training signal for Tier 2 distillation (Sprint 6).

**Full output**: All `LLMAnalysisOutput` fields, including `novelty_score`, `spam_probability`, rich narrative.

**Priority range**: 1–10. All scores available. Full signal eligibility.

---

## Provider Selection Logic

### Current (implemented)

```
APP_LLM_PROVIDER env var → create_provider() → provider | None
if None → AnalysisPipeline runs without LLM → RuleAnalyzer fallback result

Supported values: "openai", "anthropic", "claude", "gemini", "internal", "companion"
EnsembleProvider: constructed directly (not via APP_LLM_PROVIDER)
```

### EnsembleProvider (implemented)

```python
EnsembleProvider(providers=[openai_provider, internal_provider])
# Tries each in order, returns first success
# InternalModelProvider MUST be the last entry (guaranteed fallback)
# provider_name → "ensemble(openai,internal)" (compound, for traceability)
# model → actual winner's provider_name (tracked at runtime)
```

### Sprint 5C — EnsembleProvider Winner-Traceability ✅

Post-`analyze()` resolution via duck-typing:
- `_resolve_runtime_provider_name(provider)` — reads `active_provider_name` from `EnsembleProvider` after `analyze()` completes
- `_resolve_trace_metadata(provider)` — reads `provider_chain` from `EnsembleProvider` to build `ensemble_chain`
- `_resolve_analysis_source(provider_name)` — string-based, maps winner name to `AnalysisSource`

Result:
- `doc.provider` = actual winning provider name (e.g. `"openai"`, `"internal"`)
- `doc.analysis_source` = correct tier for the winner (never conservative `INTERNAL` override)
- `doc.metadata["ensemble_chain"]` = ordered list of all configured providers (for audit)

---

## AnalysisSource Tracking

### Enum — ✅ Implemented

```python
# app/core/enums.py
class AnalysisSource(StrEnum):
    RULE = "rule"                  # Tier 1 — fallback / rule-based heuristics
    INTERNAL = "internal"          # Tier 2 — InternalModelProvider or InternalCompanionProvider
    EXTERNAL_LLM = "external_llm"  # Tier 3 — OpenAI / Anthropic / Gemini
```

### Optional Field + Backward-Compat Property — ✅ Implemented (Sprint 5B)

```python
# app/core/domain/document.py
doc.analysis_source: AnalysisSource | None  # set by apply_to_document() via pipeline
doc.effective_analysis_source: AnalysisSource  # @property — backward-compat accessor
```

`effective_analysis_source` derivation (fallback for legacy rows without DB column):
- `doc.analysis_source is not None` → return it directly
- `doc.provider in {None, "fallback", "rule"}` → `RULE`
- `doc.provider in {"internal", "companion"}` → `INTERNAL`
- `doc.provider.startswith("ensemble(")` → `INTERNAL` (pre-5C composite guard)
- else → `EXTERNAL_LLM`

### DB Column — ✅ Implemented (Sprint 5B, migration 0006)

`canonical_documents.analysis_source VARCHAR(20)` — Alembic migration `0006_add_analysis_source_column.py`.

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
      │  analysis_source = EXTERNAL_LLM
      ▼
Distillation-ready corpus
      │
      ▼
Internal benchmark export
      │  analysis_source = INTERNAL
      ▼
Rule baseline export
      │  analysis_source = RULE
      ▼
Offline evaluation harness
      │
      ▼
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
| `ResearchBrief.key_documents` | ✅ | ✅ | ✅ |
| `ResearchBrief.top_actionable_signals` | ❌ | ✅ (if priority ≥ 8) | ✅ |
| `SignalCandidate` via `extract_signal_candidates()` | ❌ | ✅ | ✅ |
| `direction_hint != "neutral"` | ❌ | ✅ | ✅ |
| `impact_score > 0` | ❌ | ✅ | ✅ |
| Full narrative / explanation | ❌ | Partial | ✅ |

---

## Implementation Order

| Sprint | Component | Status |
|--------|-----------|--------|
| 4C | Relax `apply_to_document()` guard — Tier 1 results persisted | ✅ |
| 4C | `analyze_pending` None-guard — FAILED instead of silent | ✅ |
| 4D | `InternalModelProvider` (heuristic, zero deps) | ✅ |
| 4D | `EnsembleProvider` (ordered fallback, first success wins) | ✅ |
| 4D | `InternalCompanionProvider` (HTTP to local model endpoint) | ✅ |
| 4D | Factory routing: `"internal"` / `"companion"` / ensemble | ✅ |
| 5A | `InternalCompanionProvider` settings fields + localhost validation | ✅ |
| 5B | `AnalysisSource` enum + `doc.analysis_source` field | ✅ |
| 5B | `analysis_source` DB migration (migration 0006) + ORM column | ✅ |
| 5B | `effective_analysis_source` property — backward-compat accessor | ✅ |
| 5B | Pipeline: `_resolve_analysis_source()` + `apply_to_document()` write | ✅ |
| 5B | Provenance in research outputs: briefs, signals, datasets | ✅ |
| 5C | `EnsembleProvider` Winner-Traceability — post-analyze resolution | ✅ |
| 5C | `doc.provider` = winner name; `doc.metadata["ensemble_chain"]` = full list | ✅ |
| 6 | Dataset construction + evaluation harness + distillation readiness | ✅ |
| 7 | Companion benchmark harness + promotion gate + artifact contract | ✅ |
| 8 | Controlled companion inference + tuning artifact flow + manual promotion | ⏳ |

---

## Invariants

> Full invariant list is canonical in `docs/contracts.md §Immutable Invariants`.
> Intelligence-layer invariants (I-14 through I-33) are listed here for quick reference.

| ID | Rule |
|----|------|
| I-14 | `InternalCompanionProvider` implements `BaseAnalysisProvider` exactly — zero pipeline changes |
| I-15 | Companion model endpoint MUST be localhost or allowlisted — no external inference |
| I-16 | Distillation corpus uses only `analysis_source=EXTERNAL_LLM` documents as teacher signal |
| I-17 | Companion model `impact_score` cap: ≤ 0.8 (conservative, not overconfident) |
| I-18 | `AnalysisSource` is set at result creation time — immutable after `apply_to_document()` |
| I-19 | Rule-only documents (`analysis_source=RULE`) NEVER serve as distillation teacher signal |
| I-20 | `InternalModelProvider.provider_name` is always `"internal"`, `recommended_priority` ≤ 5, `actionable=False`, `sentiment_label=NEUTRAL` — hard invariants, not configurable |
| I-21 | `InternalCompanionProvider.provider_name` is always `"companion"` — distinct from `"internal"`. Factory routes `"internal"` → `InternalModelProvider`, `"companion"` → `InternalCompanionProvider` |
| I-22 | `EnsembleProvider` requires at least one provider. `InternalModelProvider` MUST be last for guaranteed fallback. All fail → `RuntimeError` |
| I-23 | `EnsembleProvider.model` MUST return the winning provider's `provider_name` immediately after `analyze()` completes — this is the canonical winner signal |
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
