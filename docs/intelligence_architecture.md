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

## Tier 2 — InternalCompanionProvider (Sprint 5 — planned)

**Status**: Architectural slot reserved. `app/analysis/providers/` directory exists (empty).

### Interface

Implements `BaseAnalysisProvider` exactly — **zero pipeline changes required**:

```python
# app/analysis/providers/companion.py  (Sprint 5)
class InternalCompanionProvider(BaseAnalysisProvider):
    provider_name = "internal"
    model: str  # e.g. "kai-analyst-v1"

    async def analyze(
        self, title: str, text: str, context: dict
    ) -> LLMAnalysisOutput: ...
```

### Output Scope (Sprint 5)

The companion model is trained to produce a **subset** of `LLMAnalysisOutput`.

Fields it MUST produce (trained):

| Field | Notes |
|-------|-------|
| `sentiment_label` | BULLISH / BEARISH / NEUTRAL |
| `sentiment_score` | -1.0 .. 1.0 |
| `relevance_score` | 0.0 .. 1.0 |
| `impact_score` | Conservative — cap at 0.8 |
| `tags` | Category tags |
| `actionable` | bool |
| `market_scope` | LOCAL / REGIONAL / GLOBAL |
| `affected_assets` | list[str] |
| `explanation_short` | Brief reasoning |

Fields with **conservative defaults** (not trained in Sprint 5):

| Field | Default | Reason |
|-------|---------|--------|
| `novelty_score` | `0.5` | Neutral — companion has no memory |
| `spam_probability` | `0.0` | Assume legitimate |
| `confidence_score` | `0.7` | Intermediate confidence |

**Priority range with companion**: With typical outputs (sentiment + impact up to 0.8, actionable=True):
```
raw ≈ (0.8×0.30) + (0.7×0.30) + (0.5×0.20) + (1×0.15) + (1.0×0.05)
    = 0.24 + 0.21 + 0.10 + 0.15 + 0.05 = 0.75 → priority = 8
```
Companion CAN produce `SignalCandidate` objects (priority ≥ 8 achievable).

### Settings Extension (Sprint 5)

```python
# app/core/settings.py — new fields in ProviderSettings
companion_model_endpoint: str | None = None      # e.g. "http://localhost:8080/v1"
companion_model_name: str = "kai-analyst-v1"
companion_model_timeout: int = 10                # seconds
```

### Factory Extension (Sprint 5)

```python
# app/analysis/factory.py — new branch in create_provider()
case "internal":
    if not settings.companion_model_endpoint:
        return None
    from app.analysis.providers.companion import InternalCompanionProvider
    return InternalCompanionProvider(
        endpoint=settings.companion_model_endpoint,
        model=settings.companion_model_name,
        timeout=settings.companion_model_timeout,
    )
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

### Current (Sprint 4)

```
APP_LLM_PROVIDER env var → create_provider() → provider | None
if None → AnalysisPipeline runs without LLM → RuleAnalyzer result only
```

### Sprint 5 Target (with priority fallback)

```
1. Try configured provider (Tier 3 or Tier 2 via APP_LLM_PROVIDER)
2. If Tier 3 fails or unavailable → try Tier 2 if companion_model_endpoint set
3. If Tier 2 unavailable → Tier 1 (RuleAnalyzer, always available)

Result: always a valid AnalysisResult, never None
```

**Note**: Full fallback chain (step 2) is Sprint 5C scope. Sprint 5A adds companion as standalone option only.

---

## AnalysisSource Tracking (Sprint 5 — planned)

### Enum

```python
# app/analysis/base/interfaces.py  (Sprint 5 addition)
class AnalysisSource(str, Enum):
    RULE = "rule"                  # Tier 1 — RuleAnalyzer
    INTERNAL = "internal"          # Tier 2 — InternalCompanionProvider
    EXTERNAL_LLM = "external_llm"  # Tier 3 — OpenAI / Anthropic / Gemini
```

### AnalysisResult Extension

```python
# AnalysisResult — new optional field (Sprint 5)
analysis_source: AnalysisSource | None = None
```

### DB Column

`canonical_documents.analysis_source VARCHAR(20)` — requires Alembic migration (Sprint 5B).

Enables:
- Filtering LLM-enriched vs companion vs rule-only documents in research outputs
- Distillation corpus selection: only `EXTERNAL_LLM` documents serve as teacher signal
- Quality reporting by tier

---

## Distillation Path (Sprint 6 — planned)

### Overview

```
Tier 3 outputs (teacher)
      │  analysis_source = EXTERNAL_LLM
      ▼
Distillation Corpus  (DB query: analysis_source=EXTERNAL_LLM, is_analyzed=True)
      │
      ▼
Offline Training  (fine-tuning or structured prediction head)
      │
      ▼
Evaluation Gate  (4 metrics, all must pass)
      │
      ├── PASS → promote to app/analysis/providers/companion.py
      └── FAIL → annotate, retry, or escalate
```

### Evaluation Gate

| Metric | Threshold | Description |
|--------|-----------|-------------|
| Sentiment accuracy | ≥ 0.85 | 3-class (BULLISH/BEARISH/NEUTRAL) |
| Relevance MAE | ≤ 0.15 | Mean absolute error vs Tier 3 |
| Impact MAE | ≤ 0.20 | Mean absolute error vs Tier 3 |
| Actionable F1 | ≥ 0.75 | Binary classification |

All four gates must pass. No partial promotions.

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
| 4C | Relax `apply_to_document()` guard — Tier 1 results persisted | ⏳ |
| 4C | `analyze_pending` None-guard — FAILED instead of silent | ⏳ |
| 5A | `InternalCompanionProvider` skeleton + settings fields | ⏳ |
| 5A | Factory `"internal"` branch in `create_provider()` | ⏳ |
| 5B | `AnalysisSource` enum + `analysis_source` DB migration | ⏳ |
| 5B | Companion model first training run (offline, against Tier 3 corpus) | ⏳ |
| 5C | Priority fallback chain (Tier 3 → Tier 2 → Tier 1) | ⏳ |
| 6 | Distillation pipeline + evaluation gate automation | ⏳ |

---

## Invariants

| ID | Rule |
|----|------|
| I-14 | `InternalCompanionProvider` implements `BaseAnalysisProvider` exactly — zero pipeline changes |
| I-15 | Companion model endpoint MUST be localhost or allowlisted — no external inference |
| I-16 | Distillation corpus uses only `analysis_source=EXTERNAL_LLM` documents as teacher signal |
| I-17 | Companion model `impact_score` cap: ≤ 0.8 (conservative, not overconfident) |
| I-18 | `AnalysisSource` is set at result creation time — immutable after `apply_to_document()` |
| I-19 | Rule-only documents (`analysis_source=RULE`) NEVER serve as distillation teacher signal |
