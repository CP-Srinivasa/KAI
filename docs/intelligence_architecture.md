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
| 8 | Controlled companion inference + tuning artifact flow + manual promotion | ✅ |
| 9 | Promotion audit hardening: I-34 automated (G6), gates_summary in record, artifact linkage | ✅ |
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
| 8 | Controlled companion inference + tuning artifact flow + manual promotion | ✅ |
| 9 | Promotion audit hardening: I-34 automated (G6), gates_summary in record, artifact linkage | ✅ |
| 10 | Companion shadow run: audit-only parallel inference, divergence JSONL, no routing influence | ✅ |
| 11 | Distillation harness: teacher/candidate/shadow combined readiness report, evaluation engine, distillation manifest | ✅ |
| 12 | Training Job Record: pre-training manifest, post-training eval link, promotion continuity, shadow schema canonicalization | ✅ |
| 13 | Evaluation Comparison + Regression Guard: pre/post model comparison, regression visibility (has_regression), comparison audit artifact, PromotionRecord.comparison_report_path, record-promotion --comparison (I-72), upgrade-cycle-status CLI | ✅ |
| 14 | Controlled A/B/C inference profiles + signal distribution contract (no auto-routing) | ✅ |
| 14C | Runtime Route Activation: ActiveRouteState, route-activate/route-deactivate CLI, I-90–I-93 | ✅ |
| 17 | analyze-pending Route Integration: route_runner.py, ActiveRouteState consumed by analyze-pending, ABCInferenceEnvelope per document → audit JSONL only (I-92, I-93) | ✅ |
| 18 | Controlled MCP Server: app/agents/mcp_server.py, 8 read tools + 3 guarded write tools, _resolve_workspace_path() workspace guard, I-94–I-100 | ✅ |
| 16 | Immutable Signal Handoff Layer: execution_handoff.py (SignalHandoff frozen dataclass), CLI: signal-handoff, JSONL batch export, I-105–I-108 | ✅ |
| 19 | Route-Aware Distribution: classify_delivery_class(), RouteAwareDistributionSummary, DistributionClassificationReport, DeliveryClassification, I-109–I-115 | ✅ |
| 20 | Consumer Collector & Acknowledgement Orchestration: execution_handoff.py (HandoffAcknowledgement, create/append/load_handoff_acknowledgement), distribution.py (HandoffCollectorSummaryReport, build_handoff_collector_summary), acknowledge_signal_handoff MCP (audit-only write, PermissionError on hidden), get_handoff_collector_summary MCP (read), CLI: handoff-acknowledge + handoff-collector-summary, I-116–I-122 | ✅ |
| 21 | Operational Readiness Surface: operational_readiness.py (OperationalReadinessReport, ReadinessIssue, RouteReadinessSummary, AlertDispatchSummary, ProviderHealthSummary, DistributionDriftSummary, OperationalArtifactRefs, build/save_operational_readiness_report), MCP: get_operational_readiness_summary (read-only), CLI: research readiness-summary, I-123–I-130 | ✅ |
| 22 | Provider Health & Distribution Drift Monitoring: operational_readiness.py bleibt der einzige Monitoring-Stack; MCP: get_provider_health(handoff_path, state_path, abc_output_path) + get_distribution_drift(handoff_path, state_path, abc_output_path) als read-only Readiness-Views (I-95, I-134), CLI: research provider-health + research drift-summary als Readiness-Views, operational_alerts.py superseded, I-131–I-138, contracts.md §34 | ✅ |
| 23 | Protective Gates & Remediation Recommendations: operational_readiness.py (interne ProtectiveGateSummary/ProtectiveGateItem in OperationalReadinessReport) als einziger kanonischer Gate-Pfad, read-only advisory system, kein Execution-Hook, MCP: get_protective_gate_summary(...) + get_remediation_recommendations(...), CLI: research gate-summary + research remediation-recommendations, protective_gates.py superseded, I-139–I-145, contracts.md §35 ✅ |
| 24 | Artifact Lifecycle Management: artifact_lifecycle.py (ArtifactEntry, ArtifactInventoryReport frozen execution_enabled=False, ArtifactRotationSummary), build_artifact_inventory(artifacts_dir, stale_after_days=30), rotate_stale_artifacts(dry_run=True default, archive-only never-delete, policy-aware: protected skipped), MCP: get_artifact_inventory (read-only, workspace-confined), CLI: research artifact-inventory + research artifact-rotate (--dry-run default), I-146–I-152, contracts.md §36 ✅ |
| 25 | Safe Artifact Retention & Cleanup Policy: artifact_lifecycle.py erweitert — ArtifactRetentionEntry (frozen, delete_eligible=False immer), ArtifactRetentionReport (frozen, execution_enabled=False, write_back_allowed=False, delete_eligible_count=0), ArtifactCleanupEligibilitySummary, ProtectedArtifactSummary. classify_artifact_retention() reine Klassifikation (I-160). Klassen: audit_trail/promotion/training_data/active_state/evaluation/operational/unknown → protected/rotatable/review_required. rotate_stale_artifacts() policy-aware (I-155). MCP: get_artifact_retention_report + get_cleanup_eligibility_summary + get_protected_artifact_summary. CLI: research artifact-retention. I-153–I-161, contracts.md §37 ✅ |
| 26 | Artifact Governance/Review Surface: artifact_lifecycle.py bleibt der einzige Governance-/Review-Stack auf Basis des kanonischen Retention-Reports. Finale read-only Modelle/Slices: ArtifactRetentionReport, ArtifactCleanupEligibilitySummary, ProtectedArtifactSummary, ReviewRequiredArtifactSummary. MCP: get_artifact_retention_report + get_cleanup_eligibility_summary + get_protected_artifact_summary + get_review_required_summary. CLI: research artifact-retention + research cleanup-eligibility-summary + research protected-artifact-summary + research review-required-summary. Superseded: ArtifactGovernanceSummary, ArtifactPolicyRationaleSummary, get_governance_summary, get_policy_rationale_summary, research governance-summary. contracts.md §38 ✅ |

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
| I-34 | `false_actionable_rate` is the 6th automated promotion gate (G6, ≤ 0.05). Computed by `compare_datasets()`, enforced by `validate_promotion()` as `false_actionable_pass`. Supersedes original "manual, deferred" note. See I-46, `docs/contracts.md §20`. |
| I-51 | Shadow run MUST NEVER call `apply_to_document()` or `repo.update_analysis()`. Zero DB writes to `canonical_documents`. |
| I-52 | Shadow run calls `InternalCompanionProvider.analyze()` directly — independent of `APP_LLM_PROVIDER`. Never a routing override. |
| I-53 | Shadow JSONL is a standalone audit artifact — not EvaluationReport input, not training corpus. |
| I-54 | Shadow run requires `companion_model_endpoint`. Absent → exit 0 (informational), not error. |
| I-55 | Divergence summary is informational only — never used for routing, gating, or output modification. |
| I-58 | `DistillationReadinessReport` is a readiness assessment only. No training, no routing changes. `promotion_validation.is_promotable` is informational. |
| I-59 | Shadow JSONL MUST NEVER be passed as teacher or candidate input in `DistillationInputs`. Shadow is audit context only (I-16, I-53). |
| I-60 | `compute_shadow_coverage()` reads shadow records for divergence stats only — never calls `compare_datasets()`. |
| I-61 | `DistillationReadinessReport.shadow_coverage` is optional — absent shadow does not block distillation readiness. |
| I-62 | `build_distillation_report()` is pure computation — no DB reads, no LLM calls, no network. |
| I-63 | `TrainingJobRecord` is a pre-training manifest only — no training, no API calls, no weight updates. |
| I-64 | `TrainingJobRecord` status="pending" does not represent a trained model. Training is operator-external. |
| I-65 | Post-training evaluation MUST pass G1–G6 via `validate_promotion()`. No bypass. |
| I-66 | Trained model not active until operator reconfigures `APP_LLM_PROVIDER`. No Sprint-12 routing change (I-42). |
| I-67 | Training teacher input MUST be `EXTERNAL_LLM` only. INTERNAL/RULE/Shadow forbidden (I-16, I-19, I-53). |
| I-68 | `record-promotion` remains sole promotion gate. TrainingJobRecord and PostTrainingEvaluationSpec are audit artifacts only. |
| I-69 | Sprint-12 canonical shadow schema: `deviations.*_delta` (evaluation.py format). `divergence.*_diff` is deprecated alias. |
| I-70 | `EvaluationComparisonReport` is comparison artifact only — no routing, no promotion trigger, no gate bypass. |
| I-71 | `compare_evaluation_reports()` is pure computation — two JSON files only. No DB, no LLM, no network. |
| I-72 | Hard regression detected + `--comparison` passed to `record-promotion` → RED warning printed. No auto-block. Operator decides. |
| I-73 | `compare-evaluations` exit 0 ≠ promotable. `check-promotion` still required (I-36, I-65). |
| I-74 | Baseline and candidate must share same `dataset_type` — mismatch → `ValueError`. |
| I-75 | `UpgradeCycleReport` is pure read/summarize. `build_upgrade_cycle_report()` MUST NOT trigger training, evaluation, or routing changes. JSON reads only. |
| I-76 | `UpgradeCycleReport.status` derived from artifact presence (`Path.exists()`) only — never auto-advanced by platform code. |
| I-77 | `UpgradeCycleReport.promotion_readiness=True` is informational. No platform code changes routing or calls `record-promotion` on this basis (I-36, I-68). |
| I-78 | `UpgradeCycleReport.promotion_record_path` only set when operator explicitly supplies it — never auto-populated from env or settings. |
| I-79 | Each `UpgradeCycleReport` = one upgrade attempt. Separate files per cycle. No in-place overwrite (I-38 extends). |
| I-80 | Route profiles are declarative only — never self-activating. |
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
| I-151 | Stale detection uses file `mtime` only — no content inspection of artifact files. |
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
| I-211 | All 41 registered `@mcp.tool()` functions MUST be classified as canonical, active_alias, superseded, or workflow_helper. No tool may be unclassified. Classification is maintained in contracts.md §44. (Updated Sprint 33.) |
| I-212 | Superseded tools MUST NOT appear in `read_tools` in `get_mcp_capabilities()`. They may remain registered for backward compatibility only, and MUST be explicitly documented as superseded. |
| I-213 | Active aliases MUST appear in `read_tools` alongside their canonical counterparts. They may not be silently removed without a contracts.md update and a migration note. |
| I-214 | `get_narrative_clusters` is canonical (not an alias). It MUST appear in `read_tools`. Coverage: at least one targeted test required (I-216 applies). |
| I-215 | `get_operational_escalation_summary` is superseded by `get_escalation_summary` (Sprint 27). It MUST NOT appear in `read_tools`. Its presence in the test suite MUST verify the exclusion (not absence from code). |
| I-216 | Every registered `@mcp.tool()` function MUST have at least one targeted test. After Sprint 32: 0 untested tools. |
| I-217 | `get_mcp_capabilities()` MUST remain the authoritative machine-readable MCP surface description. No agent or operator may assume MCP capabilities without querying it. |
| I-218 | All guarded-write tools MUST append to `mcp_write_audit.jsonl` on every call (I-94). Applies to: `create_inference_profile`, `activate_route_profile`, `deactivate_route_profile`, `acknowledge_signal_handoff`, `append_review_journal_entry`. |
| I-219 | MCP surface changes (adding or removing tools from read_tools or write_tools) MUST be reflected in contracts.md §44 and intelligence_architecture.md within the same sprint. |
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
| I-235 | Signal→Order mapping MUST be deterministic: same signal + same prices → same order parameters. |
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
| I-253 | `app/schemas/runtime_validator.py` is the single canonical schema validator implementation. `Draft202012Validator` with `FormatChecker()` MUST be used — no `jsonschema.validate()` shortcut. `app/core/settings.py::validate_json_schema_payload()` is a compatibility wrapper that delegates to `runtime_validator.py` and MUST NOT be treated as an independent validator. |
| I-254 | `DecisionInstance` MUST be a `TypeAlias` for `DecisionRecord`. No independent `DecisionInstance` dataclass may exist after Sprint 37. |
| I-255 | Legacy approval states (`auto_approved_paper`) and execution states (`submitted`, `filled`, `partial`, `cancelled`, `error`) MUST be normalized to canonical values during `load_decision_journal()`. They MUST NOT appear in new records. |
| I-256 | `app/schemas/runtime_validator.py` provides the public API (`validate_decision_payload`, `validate_config_payload`, `SchemaValidationError`). Callers outside `settings.py` and `execution/models.py` MUST use this public API. |
| I-257 | `SchemaValidationError` MUST be a subclass of `ValueError`. This ensures existing fail-closed `except ValueError` handlers catch schema violations without modification. |
| I-258 | `DECISION_SCHEMA.json` MUST include `report_type` as an optional string property (added Sprint 37). `additionalProperties: false` remains enforced. |
| I-259 | `DecisionRecord._validate_timestamp_utc` MUST validate that `timestamp_utc` is a valid ISO 8601 datetime string. Invalid timestamps MUST raise `ValidationError` before schema validation runs. |
| I-260 | All 25 tests in `test_schema_runtime_binding.py` MUST pass. They enforce that legacy enum values are rejected at the schema layer (not just the pydantic layer). |
| I-261 | `app/core/schema_binding.py` is the schema integrity layer — it validates that the schema FILES themselves are structurally correct. It MUST NOT be confused with `runtime_validator.py` (payload validation). The two modules are complementary, not competing. |
| I-262 | `validate_config_schema()` in `schema_binding.py` MUST verify all 10 safety-critical `const` constraints in CONFIG_SCHEMA.json. Any missing or wrong const value MUST be reported as an error in `SchemaValidationResult.errors`. |
| I-263 | `validate_decision_schema_alignment()` in `schema_binding.py` MUST verify that every field in `DECISION_SCHEMA.json["required"]` exists in `DecisionRecord.model_fields`. Schema fields without a corresponding model field are an error. |
| I-264 | `run_all_schema_validations()` in `schema_binding.py` MUST be callable at startup to detect schema drift. It returns a list of `SchemaValidationResult` — one per check — and MUST NOT raise on failures (advisory, not fail-closed). |
| I-265 | All 14 tests in `test_schema_binding.py` MUST pass. They cover CONFIG_SCHEMA loading, DECISION_SCHEMA validation, safety-const verification, alignment check, fail-closed behavior on malformed files, and `SchemaValidationResult` immutability. |

| I-266 | Telegram is an Operator Surface, NOT an Execution Surface. No Telegram command path may trigger live execution, auto-routing, auto-promotion, or approval-as-execution. This boundary is non-negotiable and MUST be enforced at the implementation level, not only by convention. |
| I-267 | `/approve` and `/reject` Telegram commands are audit-only journal actions. They MUST write an operator intent record to `artifacts/operator_commands.jsonl`. They MUST NOT call any execution engine, order submission path, or mutate the approval state of any live order. |
| I-268 | `TelegramOperatorBot._cmd_risk()` MUST read exclusively from a public `RiskEngine.get_risk_snapshot()` method returning a typed `RiskSnapshot` model. Direct access to private attributes (`_limits`, `_kill_switch_active`, `_paused`, `_daily_loss_pct`, `_total_drawdown_pct`, `_open_position_count`) is forbidden. |
| I-269 | `/signals` MUST read from `app/research/signals.extract_signal_candidates()` (canonical read surface). The response MUST NOT include execution instructions, routing decisions, or live order references. No side effect on signal state is permitted. |
| I-270 | `/journal` and `/daily_summary` MUST read from `app/decisions/journal.build_decision_journal_summary()`. These handlers MUST NOT write to, delete from, or mutate any journal record. |
| I-271 | `/pause`, `/resume`, and `/kill` are guarded_write commands. They MUST be dry_run-gated: when `dry_run=True` (default), they MUST return a "[DRY RUN] No action taken" response without mutating any state. No guarded_write command may bypass the dry_run gate. |
| I-272 | `/kill` MUST require a two-step confirmation via the `_pending_confirm` pattern. A single `/kill` invocation MUST NOT activate the kill switch. The pending confirmation MUST be per-`chat_id` and MUST be consumed on confirm. |
| I-273 | Every Telegram command MUST be audit-logged to `artifacts/operator_commands.jsonl` via `_audit()` BEFORE any handler logic runs. Audit-log write failure MUST be logged as error but MUST NOT prevent the command response from being sent. |
| I-274 | Commands from non-admin `chat_id` values MUST be logged and fail-closed with "Unauthorized. This incident is logged." No command from an unauthorized chat_id may reach any handler. The response MUST be generic — no internal detail disclosed. |
| I-275 | `TelegramOperatorBot` MUST be covered by at least 20 unit tests in `tests/unit/test_telegram_bot.py`. Required coverage: admin gating, unknown command rejection, dry_run behavior of all three guarded_write commands, audit logging on each command, `/kill` two-step confirm, response structure for all 15 commands. |
| I-276 | The canonical Telegram command surface is defined in `TELEGRAM_INTERFACE.md`. Any sprint that changes the command surface (adds, removes, or reclassifies a command) MUST update both `TELEGRAM_INTERFACE.md` and `docs/contracts.md §49` within the same sprint. |
| I-277 | Telegram commands are NOT MCP tools. They share no tool inventory with `app/agents/mcp_server.py`. A Telegram command that reads from a canonical MCP read surface calls the MCP function directly (via lazy import) — it MUST NOT route through the MCP tool dispatch layer. |
| I-278 | `_READ_ONLY_COMMANDS` and `_GUARDED_AUDIT_COMMANDS` in `telegram_bot.py` MUST be disjoint frozensets. No command may appear in both sets. `incident` is classified as `guarded_audit` and MUST NOT appear in `_READ_ONLY_COMMANDS`. This was corrected in Sprint 38C. |
| I-279 | All canonical read commands (those in `_READ_ONLY_COMMANDS`) call exactly one MCP canonical read function via `_load_canonical_surface()`. Every such response MUST contain `execution_enabled=False` and `write_back_allowed=False`. Any response missing these fields MUST be treated as misconfigured and the response MUST be rejected fail-closed. |
| I-280 | `get_telegram_command_inventory()` is the machine-readable Telegram surface contract. `test_telegram_command_inventory_references_registered_cli_research_commands` MUST pass in every sprint that touches `telegram_bot.py`. This test verifies that all CLI refs in `TELEGRAM_CANONICAL_RESEARCH_REFS` map to existing registered CLI research commands. |

| I-281 | Market data adapters are read-only. No method of any `BaseMarketDataAdapter` implementation may submit orders, open positions, send broker instructions, or mutate any execution state. The adapter layer is a passive data source — it has no write access to any broker system. This boundary is non-negotiable and must be enforced at the implementation level. |
| I-282 | `MarketDataPoint.is_stale` is authoritative. A stale data point (is_stale=True) MUST NOT be used as execution input without explicit operator override via a separate guarded mechanism. The TradingLoop MUST skip the cycle when `is_stale=True` or when the return value is `None`. There is no automatic retry, fallback, or re-routing. |
| I-283 | `MarketDataPoint.source` MUST be set by the adapter — never inferred, defaulted, or overwritten by the consumer. The source field is a provenance tag. It is NOT a routing signal and NOT a permission check. Signals derived from a MarketDataPoint SHOULD propagate the source value for traceability. |
| I-284 | `BaseMarketDataAdapter.health_check()` returning `False` MUST NOT be interpreted as a routing trigger or a stop-trading signal. Provider health is a liveness indicator for monitoring only. Automatic provider switching in response to health_check() failure is forbidden. The kill-switch authority belongs to the RiskEngine, not the market data layer. |
| I-285 | All `BaseMarketDataAdapter` methods MUST implement the never-raise contract: transient fetch failures return `None` (get_ticker, get_price, get_market_data_point) or `[]` (get_ohlcv), never raise. Internal errors MUST be logged at WARNING level before returning the null value. health_check() returns `False` on any error — never raises. |
| I-286 | `BacktestEngine.run(signals, prices)` receives market data as a pre-fetched `dict[str, float]`. No adapter call may occur inside `BacktestEngine.run()`. This ensures deterministic backtest replay and prevents live data contamination of historical simulations. See I-234. |
| I-287 | `MockMarketDataAdapter` is the mandatory default data source for paper trading and all unit tests that do not require real market data. Its prices are deterministic (hash-based sinusoidal, 24h period, no random()). Tests that depend on specific price values MUST use `MockMarketDataAdapter`. The mock MUST NOT be replaced by a real adapter without updating tests. |
| I-288 | Market data adapter selection is explicit configuration (DI / settings). No automatic fallback chain between adapters is permitted. A `TradingLoop` or any consumer is constructed with exactly one adapter — it does not switch providers at runtime. Provider changes require explicit reconfiguration and restart. |
| I-289 | A real external market data adapter (e.g. Binance, Alpaca) MUST implement `BaseMarketDataAdapter` completely. Every abstract method MUST be overridden. Any unimplemented method MUST raise `NotImplementedError` — not silently return `None`. Partial implementations are forbidden. |
| I-290 | All `MarketDataPoint.timestamp_utc` values MUST be UTC-aware datetimes. Naive datetimes are invalid. Adapters MUST ensure UTC-awareness before constructing the dataclass. Consumers that receive a naive timestamp MUST treat it as a data error and log a warning — they MUST NOT silently assume UTC. |

| I-291 | `PaperPortfolio` (mutable runtime state) MUST NEVER be directly exposed to any operator surface, MCP tool, CLI command, or Telegram handler. Only `PortfolioSnapshot` (frozen, read-only projection from `app/execution/portfolio_read.py`) may cross the boundary into operator-facing surfaces. `app/execution/portfolio_surface.py` is an internal TradingLoop helper — it MUST NOT be used as an operator surface. |
| I-292 | `PortfolioSnapshot` and `ExposureSummary` in `app/execution/portfolio_read.py` MUST be frozen dataclasses with `execution_enabled=False` and `write_back_allowed=False` as non-overridable fields. Any portfolio surface response that omits or sets these fields to True MUST be treated as a configuration error and rejected fail-closed. `PositionSummary` MUST be frozen but does NOT carry execution flags (it is always embedded inside `PortfolioSnapshot`). |
| I-293 | The canonical source of truth for portfolio state reconstruction is `artifacts/paper_execution_audit.jsonl`. `build_portfolio_snapshot()` in `app/execution/portfolio_read.py` MUST replay `order_filled` events from this JSONL to reconstruct current positions. No live `PaperExecutionEngine` instance may be accessed via MCP or CLI. The function is async to allow optional mark-to-market enrichment via `get_market_data_snapshot()`. |
| I-294 | Mark-to-Market enrichment is optional and fail-closed per `PositionSummary`. If `MarketDataSnapshot.available=False` or `is_stale=True`, the corresponding `PositionSummary.market_data_available` and `market_data_is_stale` flags MUST reflect this. `market_price` and `market_value_usd` MUST be `None` when unavailable. `unrealized_pnl_usd` MUST be `None` when market price is unavailable. The `PortfolioSnapshot` MUST still be returned — MtM failure per position is NOT a fatal snapshot error. |
| I-295 | `ExposureSummary` (in `portfolio_read.py`) is a derived projection of `PortfolioSnapshot`. `build_exposure_summary(snapshot)` MUST accept a `PortfolioSnapshot` and derive the exposure view from it. It MUST NOT independently fetch market data or replay the audit JSONL. The only canonical path is: audit_jsonl → `build_portfolio_snapshot()` → `PortfolioSnapshot` → `build_exposure_summary()`. |
| I-296 | Telegram `/positions` MUST be backed by `get_paper_positions_summary` MCP (canonical_read). The provisional `get_handoff_collector_summary` backing is superseded as of Sprint 40. `TELEGRAM_CANONICAL_RESEARCH_REFS["positions"]` MUST reference `"research paper-positions-summary"`. |
| I-297 | Telegram `/exposure` MUST be backed by `get_paper_exposure_summary` MCP (canonical_read) as of Sprint 40. The stub implementation is superseded. `"exposure"` MUST appear in `_READ_ONLY_COMMANDS`. `TELEGRAM_CANONICAL_RESEARCH_REFS["exposure"]` MUST reference `"research paper-exposure-summary"`. |
| I-298 | `get_paper_portfolio_snapshot`, `get_paper_positions_summary`, and `get_paper_exposure_summary` MUST be registered in `_CANONICAL_MCP_READ_TOOL_NAMES`. They MUST NOT be in `_GUARDED_MCP_WRITE_TOOL_NAMES`. All three are read-only surfaces over `app/execution/portfolio_read.py`. |
| I-299 | `PositionSummary.symbol` is the canonical position key in `PortfolioSnapshot`. Positions are keyed and sorted by symbol. There is no separate `position_id` field — the `symbol` field is stable and unique per open position in the paper portfolio (one position per symbol at a time). |
| I-300 | `app/execution/portfolio_read.py` is the canonical operator portfolio surface module. `app/execution/portfolio_surface.py` is an internal helper for TradingLoop-side formatting (provides `build_portfolio_summary(portfolio, prices)` and `build_exposure_summary(portfolio, prices)` working from a live `PaperPortfolio` object). These two modules serve different roles and MUST NOT be confused. Only `portfolio_read.py` feeds MCP tools, CLI commands, and Telegram handlers. |

| I-301 | `LoopStatus` (new, `app/orchestrator/models.py`) is a frozen read-only projection of TradingLoop operational state derived exclusively from `artifacts/trading_loop_audit.jsonl`. It MUST NOT carry live engine references. `loop_enabled` MUST always be `False` — there is no autonomous background loop. `live_allowed` MUST always be `False`. |
| I-302 | `run_paper_cycle` (guarded_write MCP tool) MUST reject `mode="live"` fail-closed: no cycle is executed, `error` field is set, `execution_enabled=False`, `write_back_allowed=False`, `live_allowed=False`. Mode MUST be one of `{"paper", "shadow"}`. Any other value is rejected identically. There is no fallback, no promotion, no retry. |
| I-303 | `run_paper_cycle` MUST use `MockMarketDataAdapter` as its data source. No real external adapter may be used inside `run_paper_cycle` without explicit operator configuration and a separate guarded mechanism. This ensures isolated, network-free, deterministic paper cycle execution. |
| I-304 | The TradingLoop has no autonomous execution path. There is no daemon, no scheduler, no background thread, no polling loop in the operator-facing control plane. All cycle execution is triggered exclusively by explicit operator calls (MCP `run_paper_cycle` or CLI `research run-paper-cycle`). |
| I-305 | The `run_paper_cycle` response MUST always carry `execution_enabled=False`, `write_back_allowed=False`, and `live_allowed=False` regardless of cycle outcome. A `LoopCycle(status=ERROR)` is the fail-safe return for any internal error — `run_paper_cycle` MUST never raise. |
| I-306 | `artifacts/trading_loop_audit.jsonl` is append-only. `run_paper_cycle` appends exactly one `LoopCycle` record per call. No read-modify-write, no truncation, no deletion. The audit log is the only persistent artifact of a `run_paper_cycle` call. |
| I-307 | `app/orchestrator/loop_read.py` is the canonical read module for TradingLoop state (mirrors the role of `portfolio_read.py`). `read_loop_status(audit_path)` reads `trading_loop_audit.jsonl` to build a `LoopStatus`. It is synchronous, pure, and never raises. If the file does not exist, it returns an empty `LoopStatus` (not an error). |
| I-308 | `run_paper_cycle` MUST be registered in `_GUARDED_MCP_WRITE_TOOL_NAMES`. It MUST NOT appear in `_CANONICAL_MCP_READ_TOOL_NAMES`. `get_loop_status` and `get_loop_cycle_summary` MUST be registered in `_CANONICAL_MCP_READ_TOOL_NAMES` and MUST NOT appear in `_GUARDED_MCP_WRITE_TOOL_NAMES`. |
| I-309 | `run_paper_cycle` uses a fresh `PaperExecutionEngine` (no portfolio replay from `paper_execution_audit.jsonl`). The run-once paper cycle is isolated: its portfolio state is ephemeral and NOT persisted to `paper_execution_audit.jsonl`. The only audit trace is the `LoopCycle` record in `trading_loop_audit.jsonl`. |
| I-310 | The `LoopStatus.mode` field reflects the last known mode from the audit log (the `mode` field of the most recent `LoopCycle`). If no cycles exist, `mode` defaults to `"paper"`. `LoopStatus.loop_enabled=False` is invariant — it documents that no autonomous scheduling exists, not a runtime flag that can be toggled. |
