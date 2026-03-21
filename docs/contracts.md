# Contracts and Core Data Models

## Purpose

This document defines the core shared contracts of the system.

These contracts are the foundation for:
- ingestion
- storage
- analysis
- scoring
- agent collaboration

No agent may modify these lightly.

---

## Core Contracts

### 0. FetchItem

The canonical raw-source type. Produced by adapters **before** normalization.

```python
@dataclass
class FetchItem:
    url: str                        # required — canonical item URL
    external_id: str | None = None  # source-assigned ID (RSS guid, API id, …)
    title: str | None = None        # raw title from source
    content: str | None = None      # raw body text or excerpt
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)  # source extras
```

**Conversion**: `normalize_fetch_item(item, *, source_id, source_name, source_type) -> CanonicalDocument`

Rules:
- **No analysis** — no scores, no sentiment, no priority, no tickers, no entity mentions
- **No persistence state** — no `status`, `is_analyzed`, `is_duplicate`, `content_hash`, `id`
- **No source metadata** — `source_id`, `source_name`, `source_type` are injected by `normalize_fetch_item()`, never by the adapter
- As close to the source as possible — minimal transformation before `normalize_fetch_item()`
- `metadata` is a free-form bag for source-specific extras (image URL, author, feed tags, …)

Implementation: Adapters create `FetchItem` internally, then call `normalize_fetch_item()` to
convert to `CanonicalDocument`. `FetchResult.documents` carries `list[CanonicalDocument]` by design.
Normalization is adapter-owned — it must NOT move into `persist_fetch_result()`, which is a
storage helper and must not contain source-type-specific transformation logic.

---

### 1. FetchResult

Represents raw ingestion output.

```python
@dataclass
class FetchResult:
    source_id: str
    documents: list[CanonicalDocument]  # never None — empty list on failure
    fetched_at: datetime
    success: bool
    error: str | None = None            # set when success=False
    metadata: dict[str, Any] = field(default_factory=dict)
```

Rules:
- adapter must never raise — catch all exceptions internally
- `success=False` + `error=<message>` on any failure
- `documents=[]` on failure (never None)
- every document must have: `url`, `title`, `source_id`, `source_name`, `source_type`
- `content_hash` must not be set by adapter — auto-computed by `CanonicalDocument`
- SSRF check (`validate_url()`) must run before any HTTP request

---

### 2. CanonicalDocument

The central data unit. Every document in the system is represented as a `CanonicalDocument`.

```python
class CanonicalDocument(BaseModel):
    id: UUID                            # primary key — never change after persist
    url: str                            # required — dedup key
    title: str                          # required
    raw_text: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime                # auto-set
    source_id: str | None = None
    source_name: str | None = None
    source_type: SourceType | None = None
    document_type: DocumentType         # ARTICLE / PODCAST_EPISODE / ...
    content_hash: str | None = None     # auto-computed — never set manually
    status: DocumentStatus              # lifecycle state — see below
    is_duplicate: bool                  # sync with status=DUPLICATE
    is_analyzed: bool                   # sync with status=ANALYZED
    # ... analysis scores, entity lists, metadata
```

Rules:
- `content_hash` is auto-computed from `url|title|raw_text` — never set manually
- `word_count` is a `@computed_field` — never stored in DB
- `status`, `is_duplicate`, `is_analyzed` are owned exclusively by:
  `app/storage/document_ingest.py` and `app/storage/repositories/document_repo.py`
- analysis scores are set exclusively by `PipelineResult.apply_to_document()`

---

### 3. AnalysisResult

Represents the output of one analysis run on a document.

```python
class AnalysisResult(BaseModel):
    document_id: str                    # str(CanonicalDocument.id)
    created_at: datetime                # auto-set

    sentiment_label: SentimentLabel
    sentiment_score: float              # [-1.0, 1.0]
    relevance_score: float              # [0.0, 1.0] — blended with keyword hits by apply_to_document()
    impact_score: float                 # [0.0, 1.0]
    novelty_score: float                # [0.0, 1.0]
    confidence_score: float             # [0.0, 1.0] — in-memory only, NOT persisted to DB
                                        # DB stores credibility_score = 1.0 - spam_probability

    market_scope: MarketScope | None
    affected_assets: list[str]
    affected_sectors: list[str]
    event_type: str | None

    explanation_short: str              # required — concise reasoning
    explanation_long: str               # required — full reasoning

    actionable: bool
    tags: list[str]
    spam_probability: float = 0.0       # stored for audit; ALWAYS pass separately to compute_priority()
    recommended_priority: int | None    # set by apply_to_document() after scoring
```

Rules:
- Must be fully populated — all score fields are required (no optional scores)
- Must be schema-validated — all ranges enforced by Pydantic
- Must not contain provider-specific fields (`provider`, `model`, `raw_output` removed)
- `AnalysisResult` is the provider-agnostic analysis contract for deterministic fallback,
  internal companion analysis, and external provider analysis
- `spam_probability` IS stored on `AnalysisResult` for audit — but scoring functions
  (`compute_priority`, `is_alert_worthy`) receive it as an **explicit separate parameter**
- `recommended_priority` is set by `apply_to_document()` after `compute_priority()` runs — not by the LLM
- `AnalysisResult` is in-memory only — no separate DB table
- scores are written back to `canonical_documents` via `repo.update_analysis(document_id, result)`

---

### 4. Document Lifecycle

```
pending → persisted → analyzed
         ↘ failed
         ↘ duplicate
```

| Status | Meaning | Owner |
|---|---|---|
| `pending` | in-memory only — not yet saved to DB | `prepare_ingested_document()` in `document_ingest.py` |
| `persisted` | saved to DB, awaiting analysis | `DocumentRepository.save_document()` |
| `analyzed` | scores written, pipeline complete | `DocumentRepository.update_analysis()` |
| `failed` | non-recoverable error — kept for audit | `repo.update_status(FAILED)` — ingest, `run_rss_pipeline()`, and `analyze_pending` CLI error handlers |
| `duplicate` | blocked at dedup gate — NOT saved | detected in-memory; `repo.mark_duplicate()` for retroactive marking |

Important: `DUPLICATE` and `FAILED` at the ingest stage are **in-memory states**.
Documents detected as duplicates by `persist_fetch_result()` are silently dropped (never saved to DB).
`status=DUPLICATE` is only written to DB when `repo.mark_duplicate()` is called explicitly
on an already-persisted document.

Rules:
- transitions are one-way — no rollback, no recycling
- `is_analyzed=True` must always be set together with `status=analyzed`
- `is_duplicate=True` must always be set together with `status=duplicate` (only when persisted)
- a document's status is always `pending` before any DB operation

---

### 5. Layer Boundaries

Every layer has a defined input and output. No layer may bypass another.

| Boundary | Rule |
|---|---|
| Ingestion → Storage | adapter returns `FetchResult`; only `persist_fetch_result()` persists |
| Storage → Analysis | `repo.get_pending_documents()` feeds the analysis queue — filters `status=PERSISTED` (not just flags) |
| Analysis → Storage | `apply_to_document()` then `repo.update_analysis()` — no other path |
| Analysis → Alerting | `is_alert_worthy()` is the only gate — no direct score access |
| LLM calls | always via `BaseAnalysisProvider.analyze()` — never direct SDK calls |
| Config | always via `AppSettings` — never `os.environ` directly |

---

### 6. Priority Score

```
raw = (relevance × 0.30) + (impact × 0.30) + (novelty × 0.20)
    + (actionable × 0.15) + ((1 - spam) × 0.05)

priority = round(raw × 9) + 1          # maps [0.0, 1.0] → [1, 10]

# Actionability bonus: +1 if result.actionable is True (and priority < 10)
if actionable:
    priority = min(10, priority + 1)
```

Cap: if `spam_probability > 0.7` → `priority = min(priority, 3)` (applied after bonus)

Scale:
- 8–10: high urgency, actionable
- 6–7: notable, alert-worthy
- 4–5: background, low urgency
- 1–3: noise or spam

---

---

### 7. Analysis Pipeline Contract

```python
class AnalysisPipeline:
    async def run(self, doc: CanonicalDocument) -> PipelineResult: ...
    async def run_batch(
        self,
        documents: list[CanonicalDocument],
        # concurrency is bounded by module-level _MAX_CONCURRENT = 5
    ) -> list[PipelineResult]: ...
```

`PipelineResult` carries:

```python
@dataclass
class PipelineResult:
    document: CanonicalDocument
    keyword_hits: list[KeywordHit]
    entity_mentions: list[EntityMention]
    llm_output: LLMAnalysisOutput | None
    analysis_result: AnalysisResult | None
    error: str | None

    def apply_to_document(self) -> None: ...
```

Rules:
- `run()` input is always `CanonicalDocument` — never a raw dict or ORM model
- `run()` output is always `PipelineResult` — never raises (errors surfaced in `result.error`)
- No direct DB writes inside `AnalysisPipeline` or `PipelineResult`
- `apply_to_document()` is the only point where scores and entities are written back to the document
- `llm_output` is optional; `analysis_result` is the required downstream contract for a successful run
- absence or failure of an external provider must degrade to a valid fallback-compatible analysis result,
  not an empty pipeline outcome
- `run_batch()` is concurrency-bounded by `_MAX_CONCURRENT`

---

### 8. Scoring Contract

Scoring is part of the pipeline result — not a separate side-effect.

```python
# Only valid mutation path:
result: PipelineResult = await pipeline.run(doc)
result.apply_to_document()          # writes scores + entities to doc in-place
await repo.update_analysis(str(doc.id), result.analysis_result)  # persists to DB
```

Rules:
- `apply_to_document()` is the **only** score mutation point (Invariant I-4)
- No code outside `PipelineResult.apply_to_document()` may set `relevance_score`, `impact_score`,
  `novelty_score`, `sentiment_label`, `priority_score`, or `spam_probability` on a document
- Scoring is always downstream of `AnalysisResult`; `LLMAnalysisOutput` is optional enrichment,
  not the canonical scoring dependency

---

### 9. Deduplication Contract

```python
class Deduplicator:
    def is_duplicate(self, doc: CanonicalDocument) -> bool: ...
    def score(self, doc: CanonicalDocument) -> DuplicateScore: ...
    def filter(self, docs: list[CanonicalDocument]) -> list[CanonicalDocument]: ...
    def filter_scored(
        self, docs: list[CanonicalDocument]
    ) -> list[tuple[CanonicalDocument, DuplicateScore]]: ...
    def register(self, doc: CanonicalDocument) -> None: ...
```

`DuplicateScore` carries:
```python
@dataclass(frozen=True)
class DuplicateScore:
    score: float          # 0.0 = unique
    is_duplicate: bool    # True when score >= threshold
    reasons: list[str]    # e.g. ['url_match', 'title_hash']
```

Criteria (in order of signal strength):
1. normalized URL match (score 1.0)
2. content hash match (score 1.0)
3. title hash match (score 0.85 — catches same headline across sources)

Rules:
- conservative by default — prefer false negatives over false positives
- `is_duplicate()` never writes to DB — read-only
- dedup is enforced exclusively by `document_ingest.py` before `repo.save_document()`
- `filter_scored()` is used by `persist_fetch_result()` — returns all docs with scores for auditing
- detected duplicates in ingest are dropped in-memory (never saved), not written as status=DUPLICATE

---

### 10. LLM Provider Contract

```python
class BaseAnalysisProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    @abstractmethod
    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput: ...
```

Implementations: `OpenAIAnalysisProvider`, `AnthropicAnalysisProvider`, `GeminiAnalysisProvider`

Rules:
- every provider must return a fully validated `LLMAnalysisOutput` — never a raw dict (I-7)
- `analyze()` never receives a `CanonicalDocument` directly — caller extracts `title` + `text`
- providers are replaceable without touching pipeline logic
- structured output enforcement is provider-specific (OpenAI: `response_format`, Anthropic: tool-use,
  Gemini: `response_schema`) — but the output contract is identical
- factory entry point: `app/analysis/factory.py:create_provider(name, settings)`

---

## Contract Stability Rules

If any contract changes:
- update tests for all affected paths
- update this document
- update `AGENTS.md` if agent collaboration is affected
- report impact on dependent modules before merging

---

## Security Constraints

- no unvalidated external input enters core models
- enforce size limits on content before LLM calls (see `_MAX_TEXT_CHARS` in providers)
- sanitize text before storage
- validate all structured LLM outputs via Pydantic (`LLMAnalysisOutput`)
- never trust provider output blindly — schema validation is mandatory

---

### 11. Sprint 4 — Research & Signal Contracts

These contracts define the Sprint 4 output layer. All three types are **in-memory only** —
never written to DB. They consume `CanonicalDocument` objects that have `status=ANALYZED`.

---

#### 11a. WatchlistRegistry

```python
class WatchlistRegistry:
    @classmethod
    def from_monitor_dir(cls, monitor_dir: Path | str) -> WatchlistRegistry: ...
    @classmethod
    def from_file(cls, path: Path | str) -> WatchlistRegistry: ...

    def get_watchlist(self, tag: str, *, item_type: WatchlistType = "assets") -> list[str]: ...
    def get_watchlist_items(self, tag: str, *, item_type: WatchlistType = "assets") -> list[WatchlistItem]: ...
    def get_all_watchlists(self, *, item_type: WatchlistType = "assets") -> Mapping[str, list[str]]: ...
    def get_symbols_for_category(self, category: str) -> list[str]: ...
    def filter_documents(self, documents, tag, *, item_type: WatchlistType = "assets") -> list[CanonicalDocument]: ...
    def save(self, path: Path | str) -> None: ...
```

`WatchlistType` is one of: `"assets"`, `"persons"`, `"topics"`, `"sources"`

Rules:
- Source: `monitor/watchlists.yml` — loaded via `WatchlistEntry` + `load_watchlist()`
- Sections: `crypto`, `equities`, `etfs`, `macro`, `persons`, `topics`, `domains`
- Tag lookup is case-insensitive
- `filter_documents()` is the primary document-to-watchlist matching path
- `WatchlistRegistry` is read-only after construction — no mutations during runtime
- `load_watchlist()` returns `[]` (not an error) if the file does not exist
- `find_by_text()` — Sprint 4B planned, not yet implemented; use `filter_documents()` instead

---

#### 11b. ResearchBrief

```python
class BriefFacet(BaseModel):
    name: str
    count: int

class BriefDocument(BaseModel):
    document_id: str          # str(CanonicalDocument.id) — traceability
    title: str
    url: str
    priority_score: int       # [1, 10] or 0 if unset
    sentiment_label: str      # SentimentLabel.value
    summary: str
    impact_score: float       # [0.0, 1.0]
    actionable: bool          # priority_score >= _ACTIONABLE_PRIORITY_THRESHOLD (8)
    published_at: datetime | None
    source_name: str | None

class ResearchBrief(BaseModel):
    cluster_name: str
    title: str                # auto-generated: "Research Brief: <cluster_name>"
    summary: str              # auto-generated sentence from metrics + top_assets
    generated_at: datetime
    document_count: int
    average_priority: float
    overall_sentiment: str            # dominant SentimentLabel.value
    top_documents: list[BriefDocument]           # top 10 by (priority, impact, date)
    top_assets: list[BriefFacet]                 # top 5 most-mentioned asset symbols
    top_entities: list[BriefFacet]               # top 5 most-mentioned entities
    top_actionable_signals: list[BriefDocument]  # priority >= 8, max 10
    key_documents: list[BriefDocument]           # priority < 8, max 20

class ResearchBriefBuilder:
    def build(self, documents: list[CanonicalDocument]) -> ResearchBrief: ...
```

Rules:
- Input: `list[CanonicalDocument]` — only `is_analyzed=True` docs are used
- `ResearchBriefBuilder.build()` never raises — returns empty brief on empty/unanalyzed input
- `_ACTIONABLE_PRIORITY_THRESHOLD = 8` — must stay in sync with `ThresholdEngine.min_priority`
- Sorted by (priority_score, impact_score, published_at) descending
- `to_markdown()` and `to_json_dict()` are the only output serialization paths
- `ResearchBrief` is in-memory only — no DB table, no persistence

---

#### 11c. SignalCandidate

```python
class SignalCandidate(BaseModel):
    model_config = ConfigDict(strict=True, validate_assignment=True)

    signal_id: str              # f"sig_{document_id}" — deterministic
    document_id: str            # str(CanonicalDocument.id) — traceability

    target_asset: str           # primary asset ("BTC", "ETH", "General Market")
    direction_hint: str         # "bullish" | "bearish" | "neutral"
                                # NEVER "buy" / "sell" / "hold" — not an execution instruction
    confidence: float           # proxy: doc.relevance_score — [0.0, 1.0]
    supporting_evidence: str    # doc.summary or doc.title
    contradicting_evidence: str # static note — not extracted in primary scan
    risk_notes: str             # spam_prob + market_scope metadata
    source_quality: float       # doc.credibility_score — [0.0, 1.0]
    recommended_next_step: str  # always ends with "— human decision required."

    priority: int = Field(ge=8, le=10)   # enforced: only high-priority signals
    sentiment: SentimentLabel
    affected_assets: list[str]
    market_scope: MarketScope
    published_at: datetime | None
    extracted_at: datetime

def extract_signal_candidates(
    documents: list[CanonicalDocument],
    min_priority: int = 8,
    watchlist_boosts: dict[str, int] | None = None,
) -> list[SignalCandidate]: ...
```

Rules:
- `priority >= 8` is a hard constraint — Pydantic `Field(ge=8)` enforced at construction
- `direction_hint` is research language, NOT trading instruction — "bullish"/"bearish"/"neutral"
- `signal_id` is deterministic: `f"sig_{document_id}"` — idempotent for same document
- `watchlist_boosts`: `{"BTC": 1}` raises effective priority by 1 for watchlist assets;
  capped at 10; never raises above 10
- `confidence_score` from `AnalysisResult` is NOT persisted to DB — `relevance_score` is
  used as the confidence proxy (available in DB)
- `SignalCandidate` is in-memory only — no DB table, no persistence
- `extract_signal_candidates()` never raises — returns `[]` if no candidates qualify

---

#### 11d. Research Layer Boundaries

| Boundary | Rule |
|---|---|
| Input gate | Only `CanonicalDocument` with `is_analyzed=True` enters research layer |
| No DB writes | `ResearchBrief` and `SignalCandidate` are always in-memory — never persisted |
| No LLM calls | Research layer is pure computation — no provider calls, no external I/O |
| Watchlist source | Always from `monitor/watchlists.yml` via `WatchlistRegistry.from_monitor_dir()` |
| CLI entry point | `research` Typer subgroup — `watchlists`, `brief`, `signals` commands |
| API entry point | `GET /research/brief` and `GET /research/signals` |

---

---

### 12. Provider-Independent Intelligence Contract

Defines the stable architecture for deterministic fallback analysis, the future internal
companion model, and external provider analysis.

Full architecture reference: [docs/intelligence_architecture.md](./intelligence_architecture.md)

---

#### 12a. Three Analysis Levels

| Level | Dependency profile | Output at analysis boundary | Operational role |
|---|---|---|---|
| Deterministic fallback | No external services | `AnalysisResult` | Guaranteed baseline coverage |
| Internal companion model | Local/internal runtime only | `AnalysisResult` or lossless normalization into it | Primary local enhancer |
| External provider analysis | External API + credentials | `LLMAnalysisOutput` normalized into `AnalysisResult` | Optional premium enrichment |

Rules:
- the system must remain usable when external providers are unavailable
- OpenAI, Claude, and Antigravity-compatible providers are amplifiers, not hard prerequisites
- downstream layers consume `AnalysisResult` regardless of analysis source

---

#### 12b. Deterministic Fallback Contract

The fallback layer is mandatory and already operational in the shared analysis pipeline.

It may use only conservative, provider-independent inputs:
- keyword hits
- entity mentions
- watchlist-aligned metadata already present on the document
- source metadata and bounded heuristics

Required behavior:
- produce a valid `AnalysisResult` when no external provider is configured
- produce a valid `AnalysisResult` when the provider is disabled
- produce a valid fallback `AnalysisResult` when a provider call fails and the pipeline can degrade safely
- keep explanations traceable as fallback-derived rather than pretending frontier-quality reasoning

Fallback analysis is intentionally conservative:
- it supports persistence, research briefs, and basic prioritization
- it must not create a parallel scoring engine
- it must not silently disappear from the pipeline because an external provider is missing

---

#### 12c. Internal Companion Model Compatibility

The internal companion layer is a specialized local analyst, not an immediate full replacement
for external frontier providers.

Its first supported task set is:
- sentiment
- relevance
- conservative impact estimation
- tags/topics
- short summary generation
- signal preclassification

Compatibility rules:
- the companion layer must emit `AnalysisResult` directly or normalize into it without loss of downstream meaning
- it must reuse the shared scoring path after `AnalysisResult` is created
- signal preclassification is an upstream hint only; it must not bypass `extract_signal_candidates()`
- no downstream consumer may require a separate companion-specific schema

---

#### 12d. External Provider Contract in the Intelligence Stack

External providers remain optional enrichers behind `BaseAnalysisProvider`.

They may improve:
- summary quality
- sentiment calibration
- impact estimation
- richer contextual tagging
- ambiguity reduction

They must not:
- become the only path that makes research outputs usable
- force the rest of the system to branch on provider family at the contract boundary

---

#### 12e. Distillation and Promotion Path

The future companion layer is trained and promoted through a teacher-student process, not by
changing runtime contracts.

Teacher signals may come from:
- external provider outputs
- operator-reviewed corrections
- historically validated analyzed documents

The internal corpus should contain:
- persisted analyzed documents
- normalized `AnalysisResult`
- teacher labels for supported companion tasks
- provenance data in dataset tooling, not in the public runtime contract

Promotion gates before the companion becomes an active default:
- holdout evaluation on teacher-labeled data
- regression checks against the deterministic fallback floor
- calibration review for sentiment, relevance, and impact
- brief-quality review on realistic analyzed-document samples
- failure-mode review for noisy, empty, adversarial, and multilingual inputs

---

### 13. Intelligence Layer Contracts

Defines the architecture for the companion model and `AnalysisSource` tracking.

Full architecture reference: [docs/intelligence_architecture.md](./intelligence_architecture.md)

---

#### 13a. ProviderSettings Extension

**Status: ✅ Implemented** (`app/core/settings.py`)

```python
companion_model_endpoint: str | None = None      # e.g. "http://localhost:11434"
companion_model_name: str = "kai-analyst-v1"
companion_model_timeout: int = 10                # seconds
```

Security constraint: `companion_model_endpoint` MUST be `localhost` or an explicitly allowlisted
internal address. Field validator rejects external URLs at settings load time.

---

#### 13b. Factory Routing

**Status: ✅ Implemented** (`app/analysis/factory.py`)

The factory distinguishes two internal tiers:

| `APP_LLM_PROVIDER` | Provider class | `provider_name` | Notes |
|--------------------|---------------|-----------------|-------|
| `"internal"` | `InternalModelProvider` | `"internal"` | Rule heuristics, no network, always available (Tier 2a) |
| `"companion"` | `InternalCompanionProvider` | `"companion"` | HTTP to local model endpoint (Tier 2b) |
| `"openai"` | `OpenAIAnalysisProvider` | `"openai"` | API key required |
| `"anthropic"` / `"claude"` | `AnthropicAnalysisProvider` | `"anthropic"` | API key required |
| `"gemini"` | `GeminiAnalysisProvider` | `"gemini"` | API key required |

`"internal"` always returns an instance (never `None`).
`"companion"` returns `None` if `companion_model_endpoint` is not set.
All external providers return `None` if the corresponding API key is missing.

**Note**: Earlier contract stubs (pre-Sprint 5A) listed `"internal"` as the companion HTTP provider.
The final implementation uses `"companion"` for the HTTP provider and `"internal"` for the
always-available heuristic model. Code and tests are authoritative.

`EnsembleProvider` (`app/analysis/ensemble/provider.py`) is not a factory target — it wraps
multiple providers directly. Its `provider_name` is a compound string like
`"ensemble(openai,internal)"` (see §13e on EnsembleProvider and analysis_source).

---

#### 13c. AnalysisSource Enum

**Status: ✅ Implemented** (`app/core/enums.py`, `app/core/domain/document.py`)

```python
# app/core/enums.py
class AnalysisSource(StrEnum):
    RULE = "rule"                  # Tier 1 — fallback / rule-based heuristics
    INTERNAL = "internal"          # Tier 2 — InternalModelProvider or InternalCompanionProvider
    EXTERNAL_LLM = "external_llm"  # Tier 3 — OpenAI / Anthropic / Gemini
```

**Current implementation**:
- `CanonicalDocument.analysis_source: AnalysisSource | None` exists as an explicit field
- `AnalysisResult.analysis_source: AnalysisSource | None` exists as an explicit field
- `canonical_documents.analysis_source` is a persisted DB column (migration `0006`)
- `CanonicalDocument.effective_analysis_source` remains the backward-compatible accessor for legacy rows

```python
# app/core/domain/document.py — compatibility accessor
doc.analysis_source                 # explicit persisted field when available
doc.effective_analysis_source       # explicit field first, legacy fallback second
```

**Post Sprint 5C behavior**:
- the pipeline writes `analysis_source` at analysis-result creation time
- winner-traceability writes the actual winning provider name to `doc.provider`
- legacy composite provider strings remain compatibility-only and must not drive new filtering logic

Invariants:
- `analysis_source=RULE` documents NEVER serve as distillation teacher signal (I-19)
- `analysis_source=INTERNAL` documents are evaluation-only, not teacher signal
- Distillation corpus selects ONLY `analysis_source=EXTERNAL_LLM` documents (§14e)

---

#### 13d. Companion Model Output Scope

**Status: ✅ Implemented** (`app/analysis/providers/companion.py`)

The companion model (`InternalCompanionProvider`) produces `LLMAnalysisOutput` via HTTP to a
local OpenAI-compatible endpoint. Impact score is capped at 0.8 client-side (Invariant I-17).

| Field | Source | Notes |
|-------|--------|-------|
| `sentiment_label` | model output | required |
| `sentiment_score` | model output | required |
| `relevance_score` | model output | required |
| `impact_score` | model output | **capped at 0.8** (I-17) |
| `tags` | model output | required |
| `actionable` | `priority >= 7` | alert-worthy threshold, NOT signal threshold |
| `market_scope` | model output | required |
| `affected_assets` | model output | required |
| `short_reasoning` | model output (internal) | stored as `doc.metadata["explanation_short"]` |
| `novelty_score` | hardcoded `0.5` | not trained in Sprint 5 |
| `spam_probability` | hardcoded `0.0` | not trained in Sprint 5 |
| `confidence_score` | hardcoded `0.7` | not trained in Sprint 5 |

**Note on summary/reasoning fields**: The companion prompt prefers `summary` as its structured
short explanation field. Legacy local endpoints may still return `co_thought` or
`short_reasoning`; all three map to `LLMAnalysisOutput.short_reasoning` and are stored in
`doc.metadata["explanation_short"]`. This remains DISTINCT from the removed `co_thought` export
field (§14c). The internal reasoning trace is not part of the training corpus output format.

**Actionable threshold note**: `actionable=(priority >= 7)` matches the alert threshold
(Telegram/Email). The signal threshold (`extract_signal_candidates()`) is `priority >= 8`.
A document can be alert-worthy (`actionable=True`) without being signal-worthy.

---

#### 13e. EnsembleProvider and analysis_source

**EnsembleProvider** (`app/analysis/ensemble/provider.py`) wraps multiple providers in priority
order. Its `provider_name` is a compound string: `"ensemble(openai,internal)"`.

**Problem**: `EnsembleProvider.provider_name` is a compound string. The pipeline cannot know which
inner provider actually won without the persisted `analysis_source` column (Sprint 5B).

**Current mitigation (two-layer)**:

1. `_resolve_analysis_source()` in `app/analysis/pipeline.py` maps any `provider_name` that
   starts with `"ensemble("` to `INTERNAL`. This is the primary guard — it sets
   `AnalysisResult.analysis_source = INTERNAL`, which `apply_to_document()` writes to
   `doc.analysis_source`.

2. `CanonicalDocument.effective_analysis_source` applies the same `startswith("ensemble(")` guard
   as a fallback for legacy rows where `doc.analysis_source` is `None` (pre-pipeline or pre-5B rows).

Both guards are in sync. The property guard is only reached when `doc.analysis_source is None`.

**Sprint 5B goal** (winner traceability): The DB column `analysis_source` already exists (migration
0006). The remaining work is to write the **actual winner's tier** at `apply_to_document()` time
using `EnsembleProvider._active_provider_name` instead of the conservative `INTERNAL` default.
Until then, all ensemble results are classified as `INTERNAL` conservatively.

| EnsembleProvider scenario | `doc.provider` | Current `analysis_source` | Sprint 5B (after winner tracking) |
|--------------------------|----------------|--------------------------|----------------------------------|
| openai won | `"ensemble(openai,internal)"` | `"internal"` ⚠️ (conservative) | `"external_llm"` ✅ |
| internal won | `"ensemble(openai,internal)"` | `"internal"` ✅ (correct) | `"internal"` ✅ |

---

### 14. Dataset Export Contract (`export_training_data`)

Defines the stable format for training corpus export used in companion model fine-tuning.
Implementation: `app/research/datasets.py`.

---

#### 14a. JSONL Row Format

Each row is a JSON object with two top-level keys:

```json
{
  "messages": [
    {"role": "system",    "content": "You are a highly precise financial AI analyst."},
    {"role": "user",      "content": "<title + source + text>"},
    {"role": "assistant", "content": "<JSON target scores — sorted keys>"}
  ],
  "metadata": {
    "document_id":     "<uuid — str>",
    "provider":        "<openai|anthropic|gemini|fallback|internal|unknown>",
    "analysis_source": "<external_llm|internal|rule>"
  }
}
```

**Requirements:**
- Only documents with `is_analyzed=True` are included
- Documents with no text (`cleaned_text` and `raw_text` both empty/None after strip) are skipped
- One row per document
- Assistant target is JSON-serialized with sorted keys (deterministic output)

---

#### 14b. Assistant Target Fields

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `affected_assets` | list[str] | ✅ | deduplicated from `doc.tickers + doc.crypto_assets` |
| `impact_score` | float | ✅ | 0.0 .. 1.0 |
| `market_scope` | str | ✅ | e.g. `"crypto"` / `"etf"` / `"unknown"` |
| `novelty_score` | float | ✅ | 0.0 .. 1.0 |
| `priority_score` | int | ✅ | 1 .. 10 |
| `relevance_score` | float | ✅ | 0.0 .. 1.0 |
| `sentiment_label` | str | ✅ | `"bullish"` / `"bearish"` / `"neutral"` |
| `sentiment_score` | float | ✅ | -1.0 .. 1.0 |
| `spam_probability` | float | ✅ | 0.0 .. 1.0 |
| `summary` | str | ✅ | `doc.summary` or `""` |
| `tags` | list[str] | ✅ | `doc.ai_tags` |

All fields are always present (no optional fields in the assistant target).

---

#### 14c. `co_thought` — Final Decision: REMOVED

**`co_thought` is NOT part of the export format.**

This field was considered during Sprint 5A design. Final rationale for removal:

1. **Contamination risk**: Rule-based analysis sets `explanation_short = "Rule-based fallback
   analysis. ..."` — a heuristic label, not reasoning. Including it as chain-of-thought
   training signal would teach the companion model a placeholder, not financial reasoning.

2. **Inconsistent quality**: Even LLM-sourced `explanation_short` values vary in quality and
   depth. The field is a brief annotation, not a structured reasoning trace.

3. **Schema coupling**: `co_thought` would couple the export to `doc.metadata["explanation_short"]`
   — an implementation detail, not a stable contract field.
   The assistant target must be derived from stable, persisted DB fields only.

4. **Architecture-specific**: Chain-of-thought training formats are model-specific.
   The export targets structured output fine-tuning (labels + scores), not reasoning-trace
   distillation. These are separate training objectives.

**If chain-of-thought distillation is required in the future**, it must use a dedicated export
format with an explicit reasoning corpus, independently of `export_training_data()`.

---

#### 14d. `analysis_source` in Metadata

**Required field.** Enables distillation pipeline to filter by tier.

| Value | Meaning | `doc.provider` values | Use as teacher? |
|-------|---------|----------------------|-----------------|
| `"external_llm"` | External LLM (OpenAI, Anthropic, Gemini) | `"openai"`, `"anthropic"`, `"gemini"`, etc. | ✅ yes |
| `"internal"` | Tier 2 analysis (heuristic or companion HTTP) | `"internal"`, `"companion"` | ⚠️ evaluation only |
| `"rule"` | Rule-based / fallback analysis | `None`, `"fallback"`, `"rule"` | ❌ no (I-19) |

**Source of truth**: exported `metadata["analysis_source"]`, produced from
`doc.effective_analysis_source` (`CanonicalDocument`, `app/core/domain/document.py`).

```python
# Backward-compatible accessor (current implementation):
doc.effective_analysis_source
# → returns doc.analysis_source if explicitly set (Sprint 5B field)
# → falls back to derivation from doc.provider (legacy path for pre-Sprint-5B rows)
```

The export reads `doc.effective_analysis_source.value` — never derives analysis_source inline.

**EnsembleProvider**:
- post Sprint 5C, `doc.provider` stores the winner name, not the composite string
- legacy rows with `doc.provider="ensemble(...)"` remain compatibility-only and map conservatively
  to `"internal"` through `effective_analysis_source`

---

#### 14e. Distillation Corpus Filtering

The distillation training pipeline MUST filter the exported JSONL by `analysis_source`:

```python
# Only use external LLM rows as teacher signal (Invariant I-19)
rows = [r for r in jsonl_rows if r["metadata"]["analysis_source"] == "external_llm"]
```

Documents with `analysis_source="rule"` or `"internal"` CAN be included in the full export
for auditing or evaluation purposes, but MUST NOT appear in the fine-tuning training corpus.

**Prohibited teacher-filter fields** (I-26):
The following fields MUST NOT be used as primary teacher-eligibility criteria:
- `doc.provider` — may be `"openai"` (teacher), `"internal"` (not teacher), or a legacy
  composite `"ensemble(openai,internal)"` — ambiguous without `analysis_source`
- `doc.metadata["ensemble_chain"]` — audit trail only, not a classification signal
- Any other metadata field

The **only valid teacher filter** is `metadata["analysis_source"] == "external_llm"`.
This ensures no ensemble composition detail can bypass I-16 or I-19.

---

#### 14f. provider vs analysis_source — Contract Separation

These are two distinct concepts that must never be conflated:

| Concept | Field | Type | Persistence | Purpose |
|---------|-------|------|-------------|---------|
| `provider` | `doc.provider` | `str \| None` | DB column | Technical engine name. Pre-5C: `"openai"`, `"internal"`, `"ensemble(openai,internal)"`, `"fallback"`. Post-5C: always the **winner name** — never a composite string. |
| `analysis_source` | `doc.analysis_source` | `AnalysisSource` enum | DB column (migration 0006) | Semantic tier: `RULE` / `INTERNAL` / `EXTERNAL_LLM` — stable, use this for filtering. |
| `ensemble_chain` | `doc.metadata["ensemble_chain"]` | `list[str]` | JSON metadata | Full ordered provider list when `EnsembleProvider` was used. Set by Sprint-5C. Legacy rows: absent. |

**Rules:**
- `provider` is a technical string — never use it directly for corpus filtering or tier decisions
- `analysis_source` is the stable semantic value — always use this for filtering and guardrails
- `provider` semantics changed in Sprint-5C: composite `"ensemble(...)"` strings are legacy only
- `analysis_source` must NEVER be set manually — always set by pipeline at result creation time
- Downstream code (ResearchBrief, SignalCandidate, alerts) consumes analysis results via the same
  `CanonicalDocument` contract regardless of which tier produced them — no branching on `provider`

**Companion model in research outputs:**
Companion-analyzed documents (`analysis_source=INTERNAL`) flow through the same research pipeline:
- `ResearchBrief.key_documents` — ✅ included
- `ResearchBrief.top_actionable_signals` — ✅ if `priority >= 8`
- `SignalCandidate` — ✅ if `priority >= 8` (companion can reach 8 with strong output)
- Alert gating — ✅ same `ThresholdEngine.is_alert_worthy()` path

No parallel models, no second result format. Provenance is tracked via `analysis_source` only.

---

## Final Rule

These contracts define the system.

If they become inconsistent with the code, the system becomes unstable.

**Protect them. Update them. Never bypass them.**

---

### 15. Sprint-5C — Winner-Traceability Contract

Defines exactly how the winning provider is identified, stored, and used to derive
`analysis_source` when `EnsembleProvider` is in the pipeline.

---

#### 15a. The Problem: Composite provider_name loses winner identity

`EnsembleProvider.provider_name` is a static composite string built at construction time:

```python
"ensemble(openai,internal)"   # always this, regardless of who won
```

The pipeline (Sprint 5B) calls `_resolve_analysis_source(self._provider)` **before**
`analyze()` runs. At that point the winner is unknown. The only safe option was `INTERNAL`.

This produces a systematic misclassification: when `openai` wins inside an ensemble,
`doc.analysis_source = INTERNAL` and `doc.provider = "ensemble(openai,internal)"` —
both wrong for downstream corpus filtering (I-16, I-19).

---

#### 15b. The Fix: Post-analyze winner resolution (Implemented in Sprint 5C)

`EnsembleProvider` exposes two public properties that enable provider-agnostic winner resolution:

```python
# app/analysis/ensemble/provider.py
@property
def active_provider_name(self) -> str:
    """Return the provider that actually produced the latest result."""
    return self._active_provider_name   # updated after each successful analyze()

@property
def provider_chain(self) -> list[str]:
    """Ordered technical trace of configured providers."""
    return [provider.provider_name for provider in self._providers]
```

**Implementation approach: duck-typing (no isinstance check)**

The pipeline uses two helper functions that inspect providers via `getattr`:

```python
# app/analysis/pipeline.py

def _resolve_runtime_provider_name(provider: BaseAnalysisProvider | None) -> str | None:
    """Return the actual winner name after analyze() — for EnsembleProvider via duck typing."""
    if provider is None:
        return None
    active = getattr(provider, "active_provider_name", None)
    if isinstance(active, str) and active.strip():
        return active.strip()
    return provider.provider_name.strip() or None

def _resolve_trace_metadata(provider: BaseAnalysisProvider | None) -> dict[str, object]:
    """Return ensemble_chain metadata if the provider exposes provider_chain."""
    if provider is None:
        return {}
    chain = getattr(provider, "provider_chain", None)
    if not isinstance(chain, (list, tuple)):
        return {}
    entries = [str(n).strip() for n in chain if str(n).strip()]
    return {"ensemble_chain": entries} if entries else {}
```

**`_resolve_analysis_source` is now string-based (no ensemble composite guard needed):**

```python
def _resolve_analysis_source(provider_name: str | None) -> AnalysisSource:
    if not provider_name:
        return AnalysisSource.RULE
    name = provider_name.strip().lower()
    if name in {"fallback", "rule"}:
        return AnalysisSource.RULE
    if name in {"internal", "companion"}:
        return AnalysisSource.INTERNAL
    return AnalysisSource.EXTERNAL_LLM
```

The composite string `"ensemble(openai,internal)"` never reaches this function in Sprint-5C+
pipelines — `_resolve_runtime_provider_name` returns the winner name instead. The composite
guard remains in `CanonicalDocument.effective_analysis_source` for legacy DB rows only (§15e).

**Pipeline call site** (success path):

```python
# trace_metadata resolved before analyze() — provider_chain doesn't change
trace_metadata = _resolve_trace_metadata(self._provider)   # {"ensemble_chain": [...]} or {}

llm_output = await self._provider.analyze(title=..., text=..., context=...)

# winner name resolved AFTER analyze() — active_provider_name updated by EnsembleProvider
provider_name = _resolve_runtime_provider_name(self._provider) or self._provider.provider_name
analysis_source = _resolve_analysis_source(provider_name)   # I-24
```

**Error path** (except branch):

```python
except Exception as exc:
    # analysis_source = RULE (set by _build_fallback_analysis, always — I-13)
    # provider_name stays "fallback" (initialized at top of run())
    # _resolve_runtime_provider_name() is not called in the error path
    analysis_result = self._build_fallback_analysis(...)
```

The error path never performs winner resolution. `analysis_source=RULE` always when analysis
failed — regardless of which provider was configured.

---

#### 15c. doc.provider — winner name, not composite

`doc.provider` must store the **winning** provider name after Sprint-5C:

| Before Sprint-5C | After Sprint-5C |
|---|---|
| `doc.provider = "ensemble(openai,internal)"` | `doc.provider = "openai"` |
| `doc.analysis_source = "internal"` (wrong) | `doc.analysis_source = "external_llm"` (correct) |

The ensemble membership is preserved in `doc.metadata["ensemble_chain"]` (list of all
provider names in order). This separates traceability from the semantic value.

**`apply_to_document()` after Sprint-5C:**

```python
self.document.provider = self.provider_name          # winner name (e.g. "openai")
self.document.metadata["ensemble_chain"] = ...       # ["openai", "internal"] if ensemble
```

---

#### 15d. analysis_source decision table (post Sprint-5C)

| Scenario | `winning_name` | `doc.analysis_source` | Corpus use |
|---|---|---|---|
| No provider configured | `"fallback"` | `RULE` | ❌ never |
| Provider call failed → fallback | `"fallback"` | `RULE` | ❌ never |
| InternalModelProvider ran | `"internal"` | `INTERNAL` | ⚠️ eval only |
| InternalCompanionProvider ran | `"companion"` | `INTERNAL` | ⚠️ eval only |
| OpenAI ran (direct) | `"openai"` | `EXTERNAL_LLM` | ✅ teacher |
| Ensemble: openai won | `"openai"` (from `ensemble.model`) | `EXTERNAL_LLM` | ✅ teacher |
| Ensemble: internal fallback | `"internal"` (from `ensemble.model`) | `INTERNAL` | ⚠️ eval only |
| Ensemble: companion fallback | `"companion"` (from `ensemble.model`) | `INTERNAL` | ⚠️ eval only |

---

#### 15e. Backward compatibility

- Pre-Sprint-5C rows: `doc.provider` may be `"ensemble(openai,internal)"`.
  `effective_analysis_source` maps `startswith("ensemble(")` → `INTERNAL` (conservative).
  These rows are NOT upgraded automatically. The conservative mapping is intentional.
- New rows (Sprint-5C+): `doc.provider` is always the winner name. The `ensemble_chain`
  metadata key is present if an `EnsembleProvider` was used.

---

#### 15f. Non-ensemble providers: no change

For `OpenAIAnalysisProvider`, `AnthropicAnalysisProvider`, `GeminiAnalysisProvider`,
`InternalModelProvider`, `InternalCompanionProvider` used directly (not via ensemble):

- `provider.model` is the **model identifier** (e.g. `"gpt-4o"`, `"rule-heuristic-v1"`),
  **not** the provider name.
- The pipeline uses `provider.provider_name` for `doc.provider` — unchanged.
- `_resolve_analysis_source()` logic (provider-object-based, pre-analyze) — unchanged.

Only `EnsembleProvider` triggers post-analyze winner resolution (I-24).

---

#### 15g. End-to-End Provenance Flow (post Sprint-5C)

This trace documents the full lifecycle of provenance from ingestion to research outputs.
Every downstream consumer relies on `doc.analysis_source` — never on `doc.provider`.

```
1. Ingestion
   doc.provider       = None
   doc.analysis_source = None
   doc.status          = PERSISTED

2. analyze_pending → AnalysisPipeline.run(doc)
   Pre-analyze:
     trace_metadata = _resolve_trace_metadata(ensemble)
       → {"ensemble_chain": ["openai", "internal"]}  (provider_chain property)

3. await ensemble.analyze(title, text, context)
   EnsembleProvider iterates providers in order:
     → tries openai.analyze()   ← succeeds
     → (internal.analyze() never called)
   ensemble._active_provider_name = "openai"   ← winner recorded (I-23)

   Fallback scenario (openai fails):
     → tries openai.analyze()   ← raises RuntimeError
     → tries internal.analyze() ← succeeds
     ensemble._active_provider_name = "internal"
     analysis_source (post-5C) → INTERNAL ✅ (correct, internal ran)

4. Post-analyze (Sprint-5C, success path)
   provider_name   = _resolve_runtime_provider_name(ensemble)
                   = ensemble.active_provider_name → "openai"
   analysis_source = _resolve_analysis_source("openai")  → EXTERNAL_LLM (I-24)

5. AnalysisResult created
   analysis_result.analysis_source = EXTERNAL_LLM
   analysis_result.document_id     = str(doc.id)

6. PipelineResult.apply_to_document()
   doc.provider                     = "openai"           # winner name (I-25)
   doc.analysis_source              = EXTERNAL_LLM
   doc.metadata["ensemble_chain"]   = ["openai", "internal"]  # from trace_metadata

7. document_repo.update_analysis(doc_id, analysis_result)
   DB: analysis_source = "external_llm"   # persisted
   DB: provider        = "openai"

8. Reload from DB (_from_model)
   doc.analysis_source = AnalysisSource.EXTERNAL_LLM  (explicit field)
   doc.effective_analysis_source → returns doc.analysis_source  → EXTERNAL_LLM

9. export_training_data(doc)
   metadata["analysis_source"] = effective_analysis_source.value  → "external_llm"
   → teacher-eligible ✅ (I-16, I-19 satisfied)

10. extract_signal_candidates(doc)
    signal.analysis_source = effective_analysis_source.value  → "external_llm"

11. ResearchBriefBuilder._to_brief_document(doc)
    brief_doc.analysis_source = effective_analysis_source.value  → "external_llm"
```

**Consistency invariant**: All consumers in steps 9–11 read `doc.effective_analysis_source`.
If `doc.analysis_source` is set (post-pipeline), that value is returned directly.
If not set (legacy pre-5B row), the property derives from `doc.provider` conservatively.
This guarantees no consumer ever branches on `provider` for tier decisions.

**Error-path scenario** (all ensemble providers fail → RuntimeError re-raised):
```
3'. All providers fail → pipeline except branch → _build_fallback_analysis()
    analysis_source  = RULE   (set by fallback builder, always)
    provider_name    = "ensemble(openai,internal)"  (unchanged, composite — pre-5C legacy)
    doc.analysis_source = RULE after apply_to_document()
    → teacher-ineligible ✅ (RULE never teacher — I-19)
    → effective_analysis_source returns RULE (analysis_source is set)
```
`_resolve_analysis_source_from_winner()` is never called in the error path (I-24).

**Legacy rows** (pre-Sprint-5C, where `doc.provider = "ensemble(openai,internal)"`):
- `doc.analysis_source` may be `None` or `INTERNAL`
- `effective_analysis_source` returns `INTERNAL` (conservative)
- These rows are NOT corpus-eligible even if `openai` had won — intentional tradeoff (I-26)

---

---

### 16. Sprint-6 — Distillation Corpus Safety + Evaluation Baseline

**Status: ✅ Implemented (Sprint 6)**

Implemented in this sprint:
- `export_training_data(teacher_only=True)` — function-level teacher guard (I-27) ✅
- `compare_datasets()` - JSONL-based offline evaluation harness with actionable metrics and promotion gate support
- `EvaluationMetrics` / `EvaluationReport` dataclasses ✅
- `load_jsonl()` helper ✅
- 19 new tests covering all modes and edge cases ✅

---

#### 16a. Teacher-Eligibility at Function Level ✅

After Sprint-5C, `analysis_source=EXTERNAL_LLM` is written correctly for all new analyzed documents.
Sprint-6 closes the direct-API-caller gap by adding `teacher_only=True` at function level.

**Current safety coverage:**

| Call path | Teacher filter applied? |
|---|---|
| `research dataset-export --source-type external_llm` (CLI) | ✅ before calling `export_training_data()` |
| `export_training_data(docs, path)` (direct API call, default) | ✅ no filter — caller responsible (unchanged) |
| `export_training_data(docs, path, teacher_only=True)` | ✅ function-level strict guard (I-27) |

---

#### 16b. Fix: `teacher_only` Parameter at Function Level (I-27)

`export_training_data()` must enforce I-16/I-19 when called with `teacher_only=True`:

```python
def export_training_data(
    documents: list[CanonicalDocument],
    output_path: Path,
    *,
    teacher_only: bool = False,
) -> int:
    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for doc in documents:
            if not doc.is_analyzed:
                continue
            # I-27: enforce teacher eligibility at function level
            if teacher_only and doc.analysis_source != AnalysisSource.EXTERNAL_LLM:
                continue
            ...
```

**Default `teacher_only=False`** — backward-compatible. Existing callers unaffected.

**Current repo status**:
- `export_training_data(..., teacher_only=True)` is implemented and is the canonical strict guard
- CLI hook-up via `dataset-export --teacher-only` is implemented in
  [app/cli/main.py](C:/Users/sasch/.local/bin/ai_analyst_trading_bot/app/cli/main.py)

---

#### 16c. Corpus Integrity Guarantee (post Sprint-5D)

With `teacher_only=True`, the following must hold for any input:

| Input document | Exported? |
|---|---|
| `analysis_source=EXTERNAL_LLM`, `is_analyzed=True`, has text | ✅ yes |
| `analysis_source=INTERNAL`, `is_analyzed=True` | ❌ no |
| `analysis_source=RULE`, `is_analyzed=True` | ❌ no |
| `analysis_source=None` (legacy), `doc.provider="openai"` | ❌ no (conservative — explicit field required) |
| `is_analyzed=False` | ❌ no (existing guard) |
| no text | ❌ no (existing guard) |

The last case is intentionally conservative: legacy rows without an explicit `analysis_source` field
are NOT teacher-eligible even if their `provider` would imply `EXTERNAL_LLM`. Callers who need
legacy rows must pre-filter using `effective_analysis_source` and pass `teacher_only=False`.

This prevents silent corpus contamination from pre-5B rows.

---

#### 16d. Corpus Export Modes (Sprint-6 ready)

Three distinct export modes serve three distinct purposes:

| Mode | CLI flag | `teacher_only` | `source_type` filter | Purpose |
|---|---|---|---|---|
| Teacher corpus | `--teacher-only` | `True` | `external_llm` (enforced) | Companion fine-tuning |
| Internal benchmark | (default) | `False` | `internal` | Evaluate companion vs rule baseline |
| Rule baseline | (default) | `False` | `rule` | Floor metrics, spam/novelty calibration |

The CLI `--source-type` filter and `--teacher-only` are separate concerns:
- `--source-type` narrows WHICH documents are loaded from DB before calling the function
- `--teacher-only` is an additional safety guardrail inside the function

Both can be combined: `--source-type external_llm --teacher-only` = maximum safety.

---

#### 16e. Evaluation Baseline Contract (I-28)

`research evaluate` (CLI) and `compare_outputs()` (evaluation.py) compare:
- **Teacher scores** (`analysis_source=EXTERNAL_LLM`): ground truth per document
- **Rule-baseline scores** (`AnalysisPipeline(run_llm=False)`): deterministic fallback re-run

This establishes the **floor gap** — how far the rule-based fallback diverges from external LLM output.
Sprint-6 will add a second comparison: rule-baseline vs companion model (real Ollama inference).

`EvaluationResult` fields defined and stable — no changes needed for Sprint-5D.
`compare_outputs()` signature is stable — no changes needed for Sprint-5D.

The evaluation command MUST NOT call any external API. All inference in Sprint-5D is offline.

---

#### 16f. Sprint-6 Acceptance Criteria ✅

All conditions satisfied in Sprint 6:

1. ✅ `export_training_data(docs, path, teacher_only=True)` skips RULE and INTERNAL docs
2. ✅ `export_training_data(docs, path)` (default, no flag) — unchanged behavior
3. ✅ `export_training_data(docs, path, teacher_only=True)` with legacy row (`analysis_source=None`) → skipped (strict mode)
4. ✅ CLI `dataset-export --teacher-only` flag passes `teacher_only=True`
5. ✅ `pytest` passes (22 tests in test_datasets.py + test_evaluation.py, 547 total)
6. ✅ `ruff check .` clean
7. ✅ `compare_outputs()` and `research evaluate` CLI unchanged and passing
8. ✅ `research evaluate-datasets` wraps `load_jsonl()` + `compare_datasets()` and handles missing or empty files defensively

---

### 17. Sprint-6 — Dataset Construction, Evaluation Harness, Distillation Readiness

**Status: ✅ Architecture, core harness, and CLI hooks complete.**

Core implementation in `app/research/datasets.py` and `app/research/evaluation.py`.
Full CLI spec: [docs/dataset_evaluation_contract.md](./dataset_evaluation_contract.md).

Sprint 6 defines three dataset roles and one offline evaluation harness:
- teacher-only dataset export (`teacher_only=True`) ✅
- internal benchmark export (CLI `--source-type internal`) ✅
- rule baseline export (CLI `--source-type rule`) ✅
- dataset-to-dataset evaluation by `document_id` (`compare_datasets()` + `research evaluate-datasets`) ✅
- companion benchmark CLI wrapper (`research benchmark-companion`) ✅
- structured benchmark persistence hooks (`save_evaluation_report()` + `save_benchmark_artifact()`) ✅

Mandatory role mapping:
- `analysis_source=external_llm` → teacher-only dataset (fine-tuning eligible)
- `analysis_source=internal` → internal benchmark (evaluation only, never teacher)
- `analysis_source=rule` → rule baseline (floor metrics only, never teacher)

Mandatory metric set (all implemented in `EvaluationMetrics`):
- `sentiment_agreement` — fraction of rows with matching sentiment_label
- `priority_mae` — mean absolute error on priority_score (1–10 scale)
- `relevance_mae` — mean absolute error on relevance_score (0.0–1.0)
- `impact_mae` — mean absolute error on impact_score (0.0–1.0)
- `tag_overlap_mean` - average Jaccard similarity of tags lists
- `actionable_accuracy` - paired-row agreement on actionable status
- `false_actionable_rate` - paired-row fraction where candidate is actionable and teacher is not

Operational benchmark hook:
- `research benchmark-companion <teacher.jsonl> <candidate.jsonl>` reuses `compare_datasets()`
  and may optionally persist a JSON report plus a small benchmark artifact manifest.
- The benchmark artifact is a trace record only; it is not a model, not a checkpoint, and not a
  second analysis schema.

Sprint-6 guardrails (all enforced):
- no training on `rule` as teacher (I-19, I-30)
- no training on `internal` as teacher (I-30)
- teacher-only filtering uses `doc.analysis_source` directly (strict, not `effective_analysis_source`) (§16b, I-31)
- evaluation matches rows by `metadata["document_id"]` only (I-32)

Remaining non-runtime task: contract acceptance / commit flow.

---

### 18. Sprint-7 — Companion Benchmark Harness, Promotion Gate, Artifact Contract

**Status: ✅ Sprint 7 — Implemented (benchmark, report, and promotion-gate path)**

Full spec: [docs/benchmark_promotion_contract.md](./benchmark_promotion_contract.md)

Runtime stubs already in `app/research/evaluation.py` (unverified, untested):
- `PromotionValidation` — per-gate pass/fail dataclass
- `validate_promotion(metrics)` - checks 6 quantitative promotion thresholds
- `save_evaluation_report()` — persists `EvaluationReport` as structured JSON
- `save_benchmark_artifact()` — writes companion benchmark manifest

**Three explicit separations (non-negotiable):**

| Concept | What it is | What it is NOT |
|---------|-----------|----------------|
| Benchmark | Run harness, produce report + artifact | Not training, not inference tuning |
| Evaluation | Measure metric gap (MAE / agreement) | Not a promotion decision |
| Promotion | Manual Gated validation (G1-G6) | Not an automated switch/deployment |

---

### 21. Sprint-10 — Companion Shadow Run

**Status: ✅ Sprint 10 — Implemented**

Shadow Run allows running a candidate model (usually `companion`) concurrently with the primary LLM provider in production to capture side-by-side inference outputs without affecting downstream consumers.

#### Shadow Concurrency & Constraints
- Shadow execution (`shadow_task`) runs alongside primary execution (`primary_task`) via `asyncio.create_task`.
- If the primary path is already on deterministic fallback (`provider=None` / `run_llm=False`), shadow may still run as a sidecar, but never replaces the primary result.
- The shadow output is strictly isolated to `PipelineResult.shadow_llm_output` and optional JSONL audit rows written by the CLI.

#### Immutable Invariants

| ID | Rule |
|----|------|
| I-51 | **Shadow Non-Mutation**: Shadow analysis NEVER mutates `doc.priority_score`, `doc.analysis_source`, or `doc.provider`. Any shadow payload is audit-only and must not be written as the primary analysis result. |
| I-52 | **Shadow Error Isolation**: Shadow provider HTTP timeouts, parsing failures, or internal exceptions MUST NOT crash or interrupt the primary execution pipeline. |
| I-53 | **Shadow Audit Contract**: The operator-facing shadow trace captures `document_id`, primary `provider` / `analysis_source`, companion summary + scores, and structured deviations in a sidecar JSONL artifact. Teacher eligibility and promotion routing MUST ignore these shadow rows. |
| Promotion | Human-reviewed gate, all 6 quantitative gates must pass | Not automatic, never pipeline-triggered |

**Promotion gates (all six must pass, implemented in `validate_promotion()`):**

| Gate | Metric | Threshold | Status |
|------|--------|-----------|--------|
| G1 | `sentiment_agreement` | ≥ 0.85 | ✅ Sprint 7 |
| G2 | `priority_mae` | ≤ 1.5 | ✅ Sprint 7 |
| G3 | `relevance_mae` | ≤ 0.15 | ✅ Sprint 7 |
| G4 | `impact_mae` | ≤ 0.20 | ✅ Sprint 7 |
| G5 | `tag_overlap_mean` | ≥ 0.30 | ✅ Sprint 7 |
| G6 | `false_actionable_rate` | <= 0.05 | implemented |

**I-34 (automated gate)**: `false_actionable_rate` is computed by `compare_datasets()` on paired teacher/candidate rows and enforced by `validate_promotion()` as gate G6. `actionable_accuracy` remains an audit metric for operator review, but it is not itself a promotion gate.

**Sprint-7 deliverables:**
1. Tests for `validate_promotion()`, `save_evaluation_report()`, `save_benchmark_artifact()` (task 7.1)
2. CLI: `evaluate-datasets --save-report <path> [--save-artifact <path>]` (task 7.2)
3. CLI: `research check-promotion <report.json>` — per-gate table, exit 0/1 (task 7.3)

**Constraints (all sprint-7):**
- No training pipeline, no fine-tuning, no weight updates
- No new provider or analysis tier
- No automatic promotion
- Companion remains `analysis_source=INTERNAL` until operator promotion (I-39)

---

### 19. Sprint-8 — Controlled Companion Inference, Tuning Artifact Flow, Manual Promotion

**Status: ✅ Sprint 8 — controlled companion inference and artifact path implemented**

Full spec: [docs/tuning_promotion_contract.md](./tuning_promotion_contract.md)

New module: `app/research/tuning.py` — `TuningArtifact`, `PromotionRecord`,
`save_tuning_artifact()`, `save_promotion_record()`.

**Four explicit separations (non-negotiable):**

| Concept | What it is | What it is NOT |
|---------|-----------|----------------|
| Benchmark | Run harness, produce EvaluationReport | Not tuning, not training |
| Tuning | Record dataset + model base manifest | Not training, not weights |
| Training | External gradient descent (operator runs) | Not in this platform |
| Promotion | Immutable audit record of operator decision | Not automatic, not routing change |

**Sprint-8 deliverables:**
1. `app/research/tuning.py` — `TuningArtifact`, `PromotionRecord`, `save_tuning_artifact()`,
   `save_promotion_record()` (task 8.1 + tests)
2. CLI: `research prepare-tuning-artifact <teacher_file> <model_base>` (task 8.2)
3. CLI: `research record-promotion <report_file> <model_id> --endpoint <url> --operator-note <text>` (task 8.3)
4. CLI: `research benchmark-companion-run <teacher.jsonl> <candidate.jsonl>` - local companion inference plus benchmark/report/artifact flow

**Constraints (all sprint-8):**
- No training engine, no external training API calls
- No new provider, no analysis tier change
- No automatic promotion or routing change
- Promotion is reversible by env var only (I-44)
- `operator_note` required — operators must explicitly acknowledge (I-43)

---

### 20. Sprint-9 — Promotion Audit Hardening

**Status: ✅ Implemented — G6, `gates_summary`, and artifact-linkage validation are live**

Full spec: [docs/sprint9_promotion_audit_contract.md](./sprint9_promotion_audit_contract.md)

Already implemented (Sprint-8 Codex extension, formalized in Sprint 9):
- `compare_datasets()` computes `actionable_accuracy` and `false_actionable_rate` on paired rows
- G6: `validate_promotion()` enforces `false_actionable_rate <= 0.05` as `false_actionable_pass`
- CLI `check-promotion` and `evaluate-datasets` surface both metrics

Implemented in Sprint 9B (Codex):
- `PromotionRecord.gates_summary: dict[str, bool] | None` is persisted as a self-documenting gate snapshot (I-47)
- `record-promotion` passes `gates_summary` from `validate_promotion()` to `save_promotion_record()` (I-48)
- `--tuning-artifact` now validates strict artifact-to-report linkage and fails closed on mismatch or missing linkage (I-49)

**Constraints (all sprint-9):**
- No training engine, no external training API calls
- No new provider, no analysis tier change
- No automatic promotion or routing change
- All existing CLI commands continue to work unchanged (`gates_summary=None` is the default)

---

### 21. Sprint-10 — Companion Shadow Run

**Status: ✅ Implemented — offline shadow, live shadow, and audit persistence are live**

Full spec: [docs/sprint10_shadow_run_contract.md](./sprint10_shadow_run_contract.md)

New module: `app/research/shadow.py` — `ShadowRunRecord`, `DivergenceSummary`,
`compute_divergence()`, `write_shadow_record()`, `run_shadow_batch()`.

**Core principle:** Shadow remains purely auditing. The primary result stays authoritative,
and shadow never owns production persistence or routing decisions.

**Five explicit separations (non-negotiable):**

| Concept | What it is | What it is NOT |
|---------|-----------|----------------|
| Primary analysis | `AnalysisPipeline` → `apply_to_document()` → DB | Not shadow |
| Shadow companion result | `InternalCompanionProvider.analyze()` → JSONL only | Not pipeline result |
| Divergence summary | Computed diff, informational | Not a gate, not a signal |
| Shadow JSONL | Standalone audit file | Not EvaluationReport, not training corpus |
| Shadow report CLI | Offline reader for operator review | Not a promotion gate |

**Sprint-10 deliverables:**
1. `app/research/shadow.py` — `ShadowRunRecord`, `DivergenceSummary`, `compute_divergence()`,
   `write_shadow_record()`, `run_shadow_batch()` + unit tests
2. `DocumentRepository.get_recent_analyzed(limit)` — new query method, no schema change
3. CLI: `research shadow-run [--count N] [--output PATH]` — audit run on recent analyzed docs
4. CLI: `research shadow-report <path>` — divergence table + aggregate summary

**Constraints (all sprint-10):**
- No second production pipeline and no shadow-owned mutation path
- No new analysis tier, no factory change, no DB migration
- No routing change — `APP_LLM_PROVIDER` is never modified by shadow paths
- Shadow JSONL is not an evaluation report, not a training corpus (I-53)
- Shadow run exits 0 on companion errors — non-fatal by design (I-54)

---

### 22. Sprint-11 — Distillation Harness und Evaluation Engine

**Status: ✅ Implemented — distillation harness and readiness manifest are live**

Full spec: [docs/sprint11_distillation_contract.md](./sprint11_distillation_contract.md)

New module: `app/research/distillation.py` — `DistillationInputs`, `ShadowCoverageReport`,
`DistillationReadinessReport`, `compute_shadow_coverage()`, `build_distillation_report()`,
`save_distillation_manifest()`.

**Core principle:** Distillation readiness is a reporting layer only. It combines existing
`compare_datasets()` + `validate_promotion()` with optional shadow coverage stats
into one structured manifest. No training, no routing, no promotion bypass.

**Dataset role invariants (non-negotiable):**

| Role | `analysis_source` | Sprint 11 usage |
|------|-------------------|-----------------|
| Teacher | `EXTERNAL_LLM` | `inputs.teacher_path` only |
| Candidate | `INTERNAL` | `inputs.candidate_path` only |
| Shadow | `record_type=companion_shadow_run` | `inputs.shadow_path` — context only |

**Sprint-11 deliverables:**
1. `app/research/distillation.py` + `tests/unit/test_distillation.py` (task 11.1)
2. CLI: `research distillation-check` + CLI tests (task 11.2)

**Known inconsistency (Sprint 10 shadow schema):**
- `shadow.py` produces: `divergence.priority_diff`, `divergence.relevance_diff`
- `evaluation.py` produces: `deviations.priority_delta`, `deviations.relevance_delta`
- `compute_shadow_coverage()` normalizes both; canonical fix deferred to Sprint 12.

**Constraints (all sprint-11):**
- No training engine, no weight updates, no external training API
- No new provider, no analysis tier change, no DB migration
- No routing change — `APP_LLM_PROVIDER` never modified
- `build_distillation_report()` is pure computation (I-62)
- Shadow data NEVER used as teacher or candidate input (I-59)

---

### 23. Sprint-12 — Training Job Record und Post-Training Evaluation

**Status: ✅ Implemented — training.py, prepare-training-job, link-training-evaluation, record-promotion --training-job, shadow schema canonicalization (I-69), 667+ Tests**

Full spec: [docs/sprint12_training_job_contract.md](./sprint12_training_job_contract.md)

New module: `app/research/training.py` — `TrainingJobRecord`, `PostTrainingEvaluationSpec`,
`save_training_job_record()`, `save_post_training_eval_spec()`.

Extension: `app/research/tuning.py` — `PromotionRecord.training_job_record` (optional field, additive).

Extension: `app/research/shadow.py` — canonical `deviations.*_delta` output (I-69).

**Core principle:** Training is exclusively an external operator process. The platform records
intent (TrainingJobRecord) and links job to evaluation (PostTrainingEvaluationSpec) — nothing more.
`record-promotion` remains the sole promotion gate.

**Minimal artifact chain:**
`teacher.jsonl` → `prepare-training-job` → `training_job_record.json`
→ [operator trains externally] → `evaluate-datasets` → `evaluation_report.json`
→ `link-training-evaluation` → `post_training_eval_spec.json`
→ `check-promotion` → `record-promotion` → `promotion_record.json`
→ [operator sets APP_LLM_PROVIDER]

**Constraints (Sprint 12):**
- No training engine, no fine-tuning API calls, no weight updates (I-63)
- No auto-routing, no auto-deploy (I-66, I-42)
- No INTERNAL/RULE/Shadow records as training input (I-67)
- record-promotion remains sole gate — TrainingJobRecord does not bypass it (I-68)
- Shadow schema canonicalization: `deviations.*_delta` as canonical (I-69)

---

### 24. Sprint-13 — Evaluation Comparison und Regression Guard

**Status: ✅ Implemented (Sprint 13C) — `compare-evaluations --out`, `EvaluationComparisonReport`,
`save_evaluation_comparison_report()` in `evaluation.py`, `PromotionRecord.comparison_report_path`,
and `record-promotion --comparison` are live.**

Full spec: [docs/sprint13_comparison_contract.md](./sprint13_comparison_contract.md)

**Canonical location**: `app/research/evaluation.py` — all comparison types and functions live here.
No separate `comparison.py` module. (See Sprint 13C architecture note in sprint13_comparison_contract.md.)

Implemented in `app/research/evaluation.py`:
- `EvaluationComparisonReport` — comparison between two evaluation reports
- `compare_evaluation_reports(baseline_report, candidate_report)` — takes `EvaluationReport` objects
- `save_evaluation_comparison_report(report, path, *, baseline_report, candidate_report)` — writes JSON
- `RegressionSummary` — `has_regression`, `regressed_metrics`, `improved_metrics`, `regressed_gates`, `improved_gates`
- `CountComparison`, `EvaluationMetricDeltas`, `PromotionGateChanges` — supporting types
- `report_type: "evaluation_report_comparison"` in persisted JSON (added by `save_evaluation_comparison_report()`)

Implemented extension: `app/research/tuning.py` — `PromotionRecord.comparison_report_path`
(optional, additive audit link).

**Core principle:** Comparison report is audit context only — not a promotion trigger. Regression
visibility is mandatory; promotion remains exclusively a manual operator decision. `check-promotion`
(G1–G6 via `validate_promotion()`) remains the sole quantitative promotion gate.

**Hard regression thresholds (R1–R6) — deferred to post-Sprint-13C:**
These are not yet implemented. The existing `regression_summary.has_regression` (any metric worsening)
and `regression_summary.regressed_metrics` (which metrics regressed) provide sufficient regression
visibility for Sprint 13C. Explicit per-metric thresholds (R1–R6) may be added to `evaluation.py` in
a future sprint without breaking existing contracts.

**Constraints (Sprint 13):**
- No auto-block on any regression — operator decides (I-72)
- G1–G6 gates unchanged — regression visibility is additive audit context (I-73)
- No training, no routing, no auto-deploy (I-70)
- `compare_evaluation_reports()` is pure computation — no DB, no LLM, no network (I-71)
- Baseline and candidate must share same `dataset_type` (I-74)

---

## Immutable Invariants

These may never be broken without a new spec:

| # | Rule |
|---|---|
| I-1 | `content_hash` is auto-computed — never set manually |
| I-2 | `word_count` is never stored in DB |
| I-3 | `repo.save()` is idempotent on hash collision |
| I-4 | `apply_to_document()` is the only score mutation point |
| I-5 | `update_analysis()` always sets `is_analyzed=True` and `status=analyzed` |
| I-6 | `AnalysisResult` has no DB table — scores are denormalized |
| I-7 | LLM output always arrives as validated `LLMAnalysisOutput` — never raw dict |
| I-8 | `spam_probability > 0.7` → `priority_score ≤ 3` |
| I-9 | status transitions are one-way |
| I-10 | `is_analyzed` and `status=analyzed` are set together, atomically |
| I-11 | `AnalysisResult.confidence_score` is in-memory only — NOT written to DB. The DB column `credibility_score` is computed as `1.0 - spam_probability` inside `update_analysis()` |
| I-12 | A document with `analysis_result=None` MUST NOT have `status=ANALYZED` set. `update_analysis(doc_id, None)` is a contract violation — caller must check for None and mark FAILED |
| I-13 | Deterministic fallback analysis must remain conservative and must not bypass the shared signal thresholding path |
| I-14 | `InternalCompanionProvider` implements `BaseAnalysisProvider` exactly — zero pipeline changes required for companion introduction |
| I-15 | Companion model endpoint MUST be localhost or allowlisted internal address — no external inference calls |
| I-16 | Distillation corpus uses only `analysis_source=EXTERNAL_LLM` documents as teacher signal |
| I-17 | Companion model `impact_score` cap: ≤ 0.8 (conservative, not overconfident) |
| I-18 | `AnalysisSource` is set at result creation time — immutable after `apply_to_document()` |
| I-19 | Rule-only documents (`analysis_source=RULE`) NEVER serve as distillation teacher signal |
| I-20 | `InternalModelProvider.provider_name` is always `"internal"`, `recommended_priority` ≤ 5, `actionable=False`, `sentiment_label=NEUTRAL` — these are hard invariants, not configurable |
| I-21 | `InternalCompanionProvider.provider_name` is always `"companion"` — distinct from `"internal"` (heuristic). factory.py routes `"internal"` → `InternalModelProvider`, `"companion"` → `InternalCompanionProvider` |
| I-22 | `EnsembleProvider` requires at least one provider. InternalModelProvider MUST be the last entry to guarantee a fallback result. If all providers fail, raises `RuntimeError` |
| I-23 | `EnsembleProvider.model` MUST return the winning provider's `provider_name` (not the composite string) immediately after `analyze()` completes. This is the canonical winner signal for pipeline source resolution. |
| I-24 | `_resolve_runtime_provider_name(provider)` resolves the winner name AFTER `analyze()` succeeds using duck-typed `active_provider_name`. `_resolve_analysis_source(winner_name)` then derives the tier. Neither is called in the error/fallback path — only `RULE` is valid when analysis failed. |
| I-25 | `doc.provider` stores the **winning** provider name (e.g. `"openai"`, `"internal"`) — never the composite ensemble string. `doc.metadata["ensemble_chain"]` records the full ordered list for traceability. |
| I-26 | Teacher eligibility is determined exclusively by `analysis_source=EXTERNAL_LLM`. `doc.provider`, `doc.metadata["ensemble_chain"]`, and all other metadata fields MUST NOT be used as teacher-eligibility criteria. No ensemble composition detail may bypass I-16 or I-19. |
| I-27 | `export_training_data()` MUST enforce teacher-eligibility at the function level when `teacher_only=True`. Uses `doc.analysis_source` directly (not `effective_analysis_source`) — legacy rows without an explicit field are excluded. ✅ Implemented. |
| I-28 | The `evaluate` CLI command compares teacher-labeled scores against rule-baseline scores (no LLM calls). This is the Sprint-6 baseline only — it does NOT represent companion-model accuracy until a real companion inference endpoint is configured. |
| I-29 | Sprint-6 dataset roles are determined exclusively by `analysis_source`: `EXTERNAL_LLM` = teacher-only, `INTERNAL` = benchmark-only, `RULE` = baseline-only. |
| I-30 | `INTERNAL` and `RULE` rows MUST NEVER be used as teacher labels for distillation, even when other metadata appears favorable. |
| I-31 | Teacher-only filtering MUST use `doc.analysis_source` directly (strict mode, not `effective_analysis_source`) — never `provider`, `ensemble_chain`, source name, title, or URL. |
| I-32 | `compare_datasets()` joins datasets by `metadata["document_id"]` only. No fuzzy matching by URL, title, or publish time is allowed. |
| I-33 | The evaluation metric set is mandatory: `sentiment_agreement`, `priority_mae`, `relevance_mae`, `impact_mae`, `tag_overlap_mean`, `actionable_accuracy`, and `false_actionable_rate`. All are implemented in `EvaluationMetrics`. |
| I-34 | Before companion promotion, `false_actionable_rate` MUST be evaluated on paired teacher/candidate rows only and remain `<= 0.05`. `actionable_accuracy` is reported for audit but is not a gate by itself. |
| I-35 | `research check-promotion` reads a saved `evaluation_report.json` only. It MUST NOT trigger analysis, DB reads, or model inference. |
| I-36 | Promotion is never automatic. `check-promotion` exiting 0 does NOT change any system state. A human operator must act on the result explicitly. |
| I-37 | `--save-report` / `--save-artifact` flags are audit-trail only. They do NOT change evaluation semantics or metric values. |
| I-38 | Benchmark artifacts are read-only once written. A re-run MUST produce a new file, never overwrite in-place. |
| I-39 | Companion remains `analysis_source=INTERNAL` until an operator explicitly reconfigures the provider. Passing promotion gates does NOT change provider routing. |
| I-40 | No Sprint-8 code path trains a model, modifies weights, or calls an external training API. Training is exclusively an external operator process. |
| I-41 | `promotion_record.json` is an audit artifact only — it does NOT change provider routing. Routing is controlled exclusively by env vars. |
| I-42 | Provider routing is controlled exclusively by `APP_LLM_PROVIDER` and `companion_model_endpoint` env vars. No platform code writes to these. |
| I-43 | `save_promotion_record()` requires a non-empty `operator_note`. Blank notes raise `ValueError`. Operators must acknowledge the promotion decision explicitly. |
| I-44 | Promotion is reversible by setting `APP_LLM_PROVIDER` to the previous value. No migration or code change required. |
| I-45 | `record-promotion` and `save_promotion_record()` require the evaluation report to exist and pass all 6 quantitative gates (G1–G6). Non-passing reports block record creation. |
| I-46 | `false_actionable_rate` is the 6th automated promotion gate (G6, threshold <= 0.05). Computed by `compare_datasets()`, enforced by `validate_promotion()` as `false_actionable_pass`. Supersedes the original I-34 "manual, deferred" note. |
| I-47 | `PromotionRecord` MUST embed `gates_summary: dict[str, bool]` — a snapshot of all 6 gate pass/fail results at record creation time. A promotion record without gate evidence is incomplete. |
| I-48 | `record-promotion` MUST call `validate_promotion()` and pass the result as `gates_summary` to `save_promotion_record()`. This makes the record self-documenting. |
| I-49 | When `--tuning-artifact` is provided to `record-promotion`, the artifact's `evaluation_report` field MUST resolve to the same path as the provided `report_file`. Mismatch blocks record creation (Exit 1). |
| I-50 | Sprint 9 changes no routing. No new provider, no analysis tier change. All routing remains operator-controlled via env vars (I-42). |
| I-51 | Shadow run MUST NEVER call `apply_to_document()` or `repo.update_analysis()`. Zero DB writes to `canonical_documents`. Shadow result is JSONL-only. |
| I-52 | Shadow run calls `InternalCompanionProvider.analyze()` directly and explicitly — independent of `APP_LLM_PROVIDER`. Shadow run is a separate, explicit audit call, never a routing override. |
| I-53 | Shadow JSONL is a standalone audit artifact. It MUST NOT be used as evaluation report input, training teacher data, or promotion gate input. |
| I-54 | Shadow run requires `companion_model_endpoint` to be configured. If absent, the command exits 0 with an informational message — not an error. |
| I-55 | Divergence summary is informational only. It MUST NOT be used for routing decisions, promotion gating, alert filtering, or research output modification. |
| I-56 | Live shadow (inline `--shadow` flag in `analyze-pending`/`pipeline run`): Shadow provider runs concurrent to Primary inside `AnalysisPipeline.run()`. Both launched as `asyncio.create_task()`; Primary is awaited first. Shadow exception is caught non-blocking — `shadow_error` set, primary unaffected. |
| I-57 | Live shadow persistence: `update_analysis()` receives `metadata_updates=res.document.metadata` (after `apply_to_document()`) — NOT `res.trace_metadata`. This ensures `shadow_analysis` and `shadow_provider` written by `apply_to_document()` reach the DB `document_metadata` column. Enforced in both `run_rss_pipeline()` and `analyze-pending`. |
| I-58 | `DistillationReadinessReport` is a readiness assessment only. It MUST NOT trigger training, weight updates, or provider routing changes. `promotion_validation.is_promotable=True` is informational — the operator must still use `record-promotion` explicitly (I-36, I-39). |
| I-59 | Shadow JSONL MUST NEVER be passed as `DistillationInputs.teacher_path` or `candidate_path`. Shadow records are audit artifacts only (I-16, I-53). |
| I-60 | `compute_shadow_coverage()` reads shadow records for aggregate divergence stats only. It MUST NOT call `compare_datasets()` or treat shadow data as candidate baseline input. |
| I-61 | `DistillationReadinessReport.shadow_coverage` is optional. Absent shadow data does not invalidate or block a distillation readiness assessment. |
| I-62 | `build_distillation_report()` is pure computation — no DB reads, no LLM calls, no network. All I/O is JSONL/JSON file reads via `load_jsonl()` and `json.loads()`. |
| I-63 | `TrainingJobRecord` is a platform-side pre-training manifest only. No platform code runs training jobs, calls fine-tuning APIs, or modifies model weights. Training is exclusively an external operator process. |
| I-64 | A `TrainingJobRecord` with `status="pending"` does not represent a trained model. The operator must run training externally before post-training evaluation can begin. |
| I-65 | Post-training evaluation MUST use the same promotion gates G1–G6 as pre-promotion evaluation. `validate_promotion()` is the canonical gate — no Sprint-12 bypass is permitted. |
| I-66 | A trained model is not active until the operator reconfigures `APP_LLM_PROVIDER` and `companion_model_endpoint`. No Sprint-12 code changes routing (I-42 extends here). |
| I-67 | The teacher dataset used in `TrainingJobRecord` MUST contain only `analysis_source=EXTERNAL_LLM` rows. `INTERNAL`, `RULE`, and Shadow records MUST NOT be used as training input (I-16, I-19, I-53 extend here). |
| I-68 | `record-promotion` remains the sole promotion gate. `TrainingJobRecord` and `PostTrainingEvaluationSpec` are audit artifacts only — they do not trigger or substitute promotion. |
| I-69 | Sprint-12 canonicalizes shadow JSONL schema: `shadow.py` MUST write `"deviations"` field (with `priority_delta`, `relevance_delta`, `impact_delta`) as canonical — matching `evaluation.py`. `"divergence"` remains as deprecated backward-compat alias. `compute_shadow_coverage()` continues to normalize both formats until old shadow files are migrated. |
| I-70 | `EvaluationComparisonReport` is a comparison artifact only — no routing change, no promotion trigger, no G1–G6 gate bypass. |
| I-71 | `compare_evaluation_reports(baseline_report, candidate_report)` takes `EvaluationReport` objects — it is pure computation. No DB reads, no LLM calls, no network. The CLI `compare-evaluations` handles file loading via `load_saved_evaluation_report()` before calling this function. (I-62 extends here.) |
| I-72 | When `regression_summary.has_regression=True` in the comparison report and `--comparison` is provided to `record-promotion`, a prominent WARNING is printed. Promotion is NOT automatically blocked — the operator must explicitly decide to proceed. `PromotionRecord.comparison_report_path` is set for the audit trail. Hard regression per-metric thresholds (R1–R6) are deferred; `has_regression` (any worsening) is the current operative flag. |
| I-73 | `compare-evaluations` exit code 0 does NOT imply the candidate is promotable. `check-promotion` on the candidate report remains required (I-36, I-65). The comparison is additional audit context only. |
| I-74 | Baseline and candidate evaluation reports MUST share the same `dataset_type`. Different `dataset_type` values raise `ValueError` in `compare_evaluation_reports()`. |
| I-75 | `UpgradeCycleReport` is a pure read/summarize artifact. `build_upgrade_cycle_report()` MUST NOT trigger training, evaluation reruns, promotions, or routing changes. The only I/O is JSON file reads via `json.loads()`. (I-62, I-70 extend here.) |
| I-76 | `UpgradeCycleReport.status` is derived exclusively from artifact presence (`Path.exists()`) — never auto-advanced by the platform. No platform code advances `status` without the operator supplying a new artifact path. |
| I-77 | `UpgradeCycleReport.promotion_readiness=True` is informational only. No platform code calls `record-promotion` or changes `APP_LLM_PROVIDER` based on this field. The operator must run `record-promotion` explicitly (I-36, I-68 extend here). |
| I-78 | `UpgradeCycleReport.promotion_record_path` is set ONLY when the operator explicitly supplies this path to `build_upgrade_cycle_report()` or the CLI. It MUST NOT be auto-populated from env vars or settings. |
| I-79 | Each `UpgradeCycleReport` represents one upgrade cycle attempt. Parallel or sequential cycles (e.g. v1→v2, v2→v3) produce separate files. A cycle report MUST NOT be overwritten in-place — re-runs produce new files (I-38 extends here). |
| I-80 | Sprint-14 route profiles are declarative only. Loading, saving, or distributing a route profile MUST NOT change `APP_LLM_PROVIDER`, active provider selection, scheduler behavior, or any live routing state. |
| I-81 | Path `A` is the only production-owning path. Only `A` may update `CanonicalDocument`, `analysis_source`, research outputs, or persisted signal surfaces. `B` and `C` remain audit/comparison paths only. |
| I-82 | Shadow (`B`) and control (`C`) outputs MUST NOT overwrite, replace, or silently mutate the primary result. Any sidecar output must be stored as a separate artifact or envelope reference. |
| I-83 | Distribution is not decision. Emitting outputs to research, signal, comparison, upgrade-cycle, or promotion audit channels MUST NOT imply promotion, routing change, alert trigger, or trading action. |
| I-84 | Routing configuration is not activation by itself. A configured `InferenceRouteProfile` remains inert until an explicit future runtime command or service hook is introduced by spec. |
| I-85 | Every distributed A/B/C artifact MUST remain audit-traceable by `document_id`, path label, provider, and `analysis_source`. No Sprint-14 output may omit provenance for any included path. |
| I-86 | Comparison summaries inside Sprint-14 envelopes are additive audit context only. They may reference shadow deviations or `EvaluationComparisonReport` artifacts, but they MUST NOT auto-block, auto-promote, or auto-reroute. |
| I-87 | The control path is rule-bound in Sprint 14. `C` MUST remain `analysis_source=RULE` and must stay available without any external provider dependency. |
| I-88 | `ABCInferenceEnvelope` is a pure composition artifact. Creating or saving an envelope MUST NOT call `analyze()`, `apply_to_document()`, `update_analysis()`, or any DB mutation. All inputs come from already-persisted artifacts. |
| I-89 | `create-inference-profile` CLI produces a declarative `InferenceRouteProfile` JSON file only. It MUST NOT trigger analysis, routing changes, provider instantiation, DB calls, or any modification to `APP_LLM_PROVIDER`. |
| I-90 | `route-activate` writes an `ActiveRouteState` to a dedicated state file only (`artifacts/active_route_profile.json` by default). It MUST NOT write to `.env`, `settings.py`, or `APP_LLM_PROVIDER`. |
| I-91 | `route-activate` and `route-deactivate` do NOT change `APP_LLM_PROVIDER`. Primary provider selection remains the operator's sole responsibility. |
| I-92 | When `analyze-pending` runs with an active shadow route, primary results are written to DB only. Shadow and control outputs go to audit JSONL only (I-51, I-82). |
| I-93 | `ABCInferenceEnvelope` produced during a shadow-enabled analyze-pending run is written per-document to audit JSONL only — no DB writes, no routing changes. |
| I-94 | The MCP server is a controlled external interface. No MCP tool may enumerate filesystem paths, auto-discover artifacts, or infer state beyond what the caller explicitly provides. |
| I-95 | MCP read tools (`get_*`) MUST NOT trigger analysis, model inference, DB mutation, routing changes, or any side effects beyond the declared return value. |
| I-96 | MCP guarded write tools (`create_route_profile`, `activate_route`) produce exactly one artifact file per call. They MUST NOT change `APP_LLM_PROVIDER`, write to DB, trigger analysis, or produce any side effect beyond the declared output file. |
| I-97 | Every MCP write action returns a complete audit record. The key `"app_llm_provider_unchanged": true` MUST always be present in the audit record of any MCP write tool. |
| I-98 | No MCP tool exposes trading execution, position management, order submission, or live market interaction. These are permanently out of scope for the MCP surface. |
| I-99 | No MCP tool performs auto-promotion, auto-routing, or any state advancement. Read results (scores, route status, cycle status) are informational only and carry no implicit action weight. |
| I-100 | Dataset export, training job submission, promotion recording, alert configuration, and provider key management remain CLI-only operator actions. MCP MUST NOT add these surfaces. |
| I-101 | The external signal-consumption surface is read-only. Building or retrieving an execution handoff MUST NOT submit orders, mutate DB state, change routing, or call any broker/exchange API. |
| I-102 | `ExecutionHandoffReport` MUST be derived exclusively from existing `SignalCandidate` outputs plus persisted document provenance. No second signal qualification, rescoring, or execution heuristic is allowed. |
| I-103 | Every execution handoff row MUST include `signal_id`, `document_id`, direction, priority, score/confidence, provider, `analysis_source`, route metadata, source metadata, and timestamps for audit traceability. |
| I-104 | Every execution handoff artifact MUST declare `execution_enabled=false` and `write_back_allowed=false`. There is no fill-report, execution callback, or core-state write-back channel in-platform. Audit-only acknowledgements MAY be recorded as append-only artifacts. |
| I-105 | `SignalHandoff` (Sprint 16) MUST be frozen (`frozen=True`). No field may be mutated after construction. Every `create_signal_handoff()` call generates a unique `handoff_id` (UUID). |
| I-106 | `SignalHandoff.evidence_summary` MUST be truncated to `_MAX_EVIDENCE_CHARS` (500). Full document text MUST NOT be forwarded to external consumers. |
| I-107 | `SignalHandoff` MUST NOT include `recommended_next_step`. That field is internal to KAI and MUST NOT appear in any externally delivered artifact or its JSON serialization. |
| I-108 | Every `SignalHandoff` MUST carry a `consumer_note` stating that signal delivery is not execution and consumption does not confirm trade intent. `provenance_complete` MUST be `False` if any of `signal_id`, `document_id`, or `analysis_source` is empty. |


---

### 25. Sprint-13 Extension — Companion Upgrade Cycle Report

**Status: ✅ Implemented — `upgrade-cycle-status` and `UpgradeCycleReport` are live**

Full spec: [docs/sprint13_comparison_contract.md Part 2](./sprint13_comparison_contract.md)

New module: `app/research/upgrade_cycle.py` — `UpgradeCycleReport`,
`build_upgrade_cycle_report()`, `derive_cycle_status()`, `save_upgrade_cycle_report()`.

New CLI command: `research upgrade-cycle-status` — displays current cycle state and
next-step guidance. Does NOT replace individual step commands.

**Status phases** (hierarchical, derived from artifact presence only):

| Phase | Condition |
|-------|-----------|
| `prepared` | teacher_dataset_path exists |
| `training_recorded` | + training_job_record.json exists |
| `evaluated` | + evaluation_report.json exists |
| `compared` | + comparison_report.json exists (optional step) |
| `promotable` | evaluated + candidate passes G1–G6 via `validate_promotion()` |
| `promoted_manual` | + promotion_record.json exists |

**Core principle:** The orchestrator reads, chains, and summarizes — never auto-advances,
never auto-promotes, never changes routing. Simple but powerful audit surface.

**Constraints (Sprint 13, Task 13.5):**
- No auto-routing, no auto-deploy, no auto-promotion (I-76, I-77)
- `build_upgrade_cycle_report()` is pure computation — JSON reads only (I-75)
- `promotion_readiness=True` is informational only (I-77)
- `promotion_record_path` is operator-supplied only, never auto-populated (I-78)
- Each cycle = one file; no in-place overwrite (I-79)

---

### 26. Sprint-14 — Controlled A/B/C Inference Profiles and Signal Distribution

**Status: ✅ Contract-defined — runtime implementation intentionally deferred**

Full spec: [docs/sprint14_inference_distribution_contract.md](./sprint14_inference_distribution_contract.md)

Sprint 14 defines a controlled A/B/C inference layer above the existing primary, shadow,
comparison, promotion, and upgrade-cycle artifacts.

Path semantics:

| Path | Meaning | Tier expectation |
|------|---------|------------------|
| `A` | Primary production path | `EXTERNAL_LLM`, `INTERNAL`, or `RULE` |
| `B` | Shadow / trained companion path | `INTERNAL` |
| `C` | Control path | `RULE` |

Core principles:

- `A` is the only path allowed to own persisted production analysis.
- `B` and `C` are audit/comparison paths only in Sprint 14.
- Distribution is an output concern, not a routing or promotion decision.
- Route configuration is declarative only until an explicit future runtime hook is added.

#### 26a. Route profile contract

```python
@dataclass
class InferenceRouteProfile:
    profile_name: str
    route_profile: str                    # primary_only | primary_with_shadow | primary_with_control | primary_with_shadow_and_control
    active_primary_path: str              # e.g. "A.external_llm"
    enabled_shadow_paths: list[str]       # e.g. ["B.companion"]
    control_path: str | None = None       # "C.rule" or None
    distribution_targets: list[DistributionTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
```

#### 26b. Distribution target contract

```python
@dataclass
class DistributionTarget:
    channel: str                          # research_brief | signal_candidates | shadow_audit_jsonl | comparison_report_json | upgrade_cycle_report_json | promotion_audit_json
    include_paths: list[str]              # subset of ["A", "B", "C", "comparison"]
    mode: str                             # primary_only | audit_only | comparison_only | audit_appendix
    artifact_path: str | None = None
```

Rules:
- `research_brief` and `signal_candidates` remain primary-only channels
- audit channels may include `B`, `C`, or comparison artifacts
- every distributed output must keep `document_id`, provider, `analysis_source`, and path label traceable

#### 26c. A/B/C envelope contract

```python
@dataclass
class PathResultEnvelope:
    path_id: str
    provider: str
    analysis_source: str
    result_ref: str | None = None
    summary: str | None = None
    scores: dict[str, object] = field(default_factory=dict)


@dataclass
class PathComparisonSummary:
    compared_path: str
    sentiment_match: bool | None = None
    actionable_match: bool | None = None
    tag_overlap: float | None = None
    deviations: dict[str, float] = field(default_factory=dict)
    comparison_report_path: str | None = None


@dataclass
class DistributionMetadata:
    route_profile: str
    active_primary_path: str
    distribution_targets: list[DistributionTarget]
    decision_owner: str = "operator"
    activation_state: str = "audit_only"


@dataclass
class ABCInferenceEnvelope:
    document_id: str
    route_profile: str
    primary_result: PathResultEnvelope
    shadow_results: list[PathResultEnvelope] = field(default_factory=list)
    control_result: PathResultEnvelope | None = None
    comparison_summary: list[PathComparisonSummary] = field(default_factory=list)
    distribution_metadata: DistributionMetadata | None = None
```

Rules:
- `primary_result` is mandatory and references the active production outcome
- `shadow_results` and `control_result` are optional and never overwrite the primary result
- `comparison_summary` is additive audit context only
- `distribution_metadata.activation_state` is informational and must not activate routing by itself

---

### 27. Sprint-17 — Route Integration in analyze-pending

**Status: ✅ Implemented — primary/shadow/control route runner live**

Full spec: [docs/sprint17_route_integration_contract.md](./sprint17_route_integration_contract.md)

Sprint 17 wires `ActiveRouteState` (Sprint 14C) into `analyze-pending`:
- Phase 2.5: shadow/control inference via `run_route_provider()` — no DB writes
- Phase 3: primary results → DB only (I-92)
- Phase ABC: `ABCInferenceEnvelope` per document → audit JSONL only (I-93)

New module: `app/research/route_runner.py` — `map_path_to_provider_name()`,
`build_path_result_from_llm_output()`, `build_path_result_from_analysis_result()`,
`build_comparison_summaries()`, `build_abc_envelope()`, `run_route_provider()`.

**Core constraints (I-90–I-93):**
- Primary result is the sole DB write — shadow/control never touch DB (I-92)
- `ABCInferenceEnvelope` is audit JSONL only — no DB, no routing change (I-93)
- `APP_LLM_PROVIDER` and route state unchanged by analyze-pending (I-90, I-91)
- Active route profile suppresses `--shadow-companion` (I-84)
- `run_route_provider()` never raises — failure is isolated to shadow/control path

**`DistributionMetadata.activation_state`:**
- `"active"` — set by `route_runner.build_abc_envelope()` (live route run)
- `"audit_only"` — set by Sprint 14 `abc-run` CLI (post-hoc artifact construction)

---

### 29. Sprint-18 — Controlled MCP Server Integration

**Status: ✅ Implemented — read surface + guarded write surface**

Full spec: [docs/sprint18_mcp_contract.md](./sprint18_mcp_contract.md)

Sprint 18 defines and implements a controlled MCP server (`app/agents/mcp_server.py`) that
exposes KAI's research surface to AI-capable tools (e.g. Claude Desktop) with strict guardrails.

**Surface layers:**

| Layer | Tools | DB? | File writes? |
|-------|-------|-----|--------------|
| Read | `get_watchlists`, `get_research_brief`, `get_signal_candidates`, `get_route_profile_report`, `get_inference_route_profile`, `get_active_route_status`, `get_upgrade_cycle_status`, `get_mcp_capabilities` | read-only | none |
| Guarded write | `create_inference_profile`, `activate_route_profile`, `deactivate_route_profile` | none | one artifact JSON per call |

**Core constraints (I-94–I-100):**
- MCP is a controlled ingress point — not an admin panel, not a trading interface
- Read tools are side-effect free (I-95)
- Write tools produce exactly one artifact file, return audit JSON with `app_llm_provider_unchanged: true`,
  and MUST NOT change `APP_LLM_PROVIDER` (I-96, I-97)
- All file paths validated via `_resolve_workspace_path()` to prevent path traversal (I-94)
- Trading execution, auto-promotion, auto-routing, dataset export, training submission,
  promotion recording remain permanently out of MCP scope (I-98, I-99, I-100)

---

### 30. Sprint-16 — Controlled External Signal Consumption Layer

**Status: ✅ Implemented — read-only execution handoff surface**

Sprint 19 defines a controlled signal-consumption layer for external systems that need
qualified signals with provenance and audit metadata, without granting any execution or
write-back capability.

**Artifact contract:**

```python
@dataclass(frozen=True)
class SignalHandoff:
    signal_id: str
    document_id: str
    target_asset: str
    direction_hint: str
    priority: int
    score: float
    confidence: float
    analysis_source: str
    provider: str
    route_path: str
    path_type: str
    delivery_class: str
    consumer_visibility: str
    audit_visibility: str
    source_name: str | None
    source_type: str | None
    source_url: str | None
    published_at: str | None
    extracted_at: str
    handoff_at: str
    provenance_complete: bool


@dataclass
class ExecutionHandoffReport:
    signal_count: int
    signals: list[SignalHandoff]
    generated_at: str
    interface_mode: str = "read_only"
    execution_enabled: bool = False
    write_back_allowed: bool = False
```

**Core constraints (I-101–I-104):**
- The handoff is advisory only and read-first — never an execution hook
- The batch report in `distribution.py` wraps canonical immutable `SignalHandoff` rows from `execution_handoff.py`
- Provenance must remain explicit per row: provider, `analysis_source`, route, source, timestamps
- No reverse control channel exists: no fills, no execution callback, no strategy feedback, no trading writes. Audit-only receipt logging is allowed only as append-only acknowledgement artifacts.

**Sprint 16 — Immutable `SignalHandoff` artifact (I-105–I-108):**

Sprint 16 adds `app/research/execution_handoff.py` with a frozen `SignalHandoff` dataclass
as the canonical external delivery artifact:

- `frozen=True` — immutable after construction, new UUID `handoff_id` per call (I-105)
- `evidence_summary` — truncated to 500 chars; no full document text forwarded (I-106)
- `recommended_next_step` excluded — internal KAI field, never serialized (I-107)
- `consumer_note` always present; `provenance_complete` based on `signal_id`/`document_id`/`analysis_source` (I-108)

CLI: `research signal-handoff [--out batch.jsonl] [--out-json single.json]`

---

### 31. Sprint-19 — Route-Aware Signal Distribution Classification

**Status: ✅ Implemented — primary handoff stays productive, shadow/control stay audit-only**

Sprint 20 extends the existing external-consumption surface without introducing a second
signal or handoff stack. Route-aware delivery is derived from canonical route metadata only.

**Classification rules (I-109–I-112):**
- `A.*` routes are classified as `path_type=primary` and `delivery_class=productive_handoff`
- `B.*` routes are classified as `path_type=shadow` and `delivery_class=audit_only`
- `C.*` routes are classified as `path_type=control` and `delivery_class=comparison_only`
- Unknown route prefixes fail closed: hidden from consumers, visible only in audit surfaces

**Read-only report surfaces:**
- `ExecutionHandoffReport` remains the productive external handoff wrapper for qualified primary signals only
- `DistributionClassificationReport` composes that primary handoff report with audit-only
  shadow/control outputs derived from persisted `ABCInferenceEnvelope` artifacts
- `research distribution-classification-report <abc_output.jsonl>` is read-only and requires
  an explicit ABC artifact path
- MCP exposes `get_distribution_classification_report(...)` as a read-only report builder

**Core constraints (I-109–I-112):**
- I-109: Route-aware delivery classification MUST be derived from `route_path` only. No new
  signal qualification or rescoring is allowed.
- I-110: `SignalHandoff` MAY expose `path_type`, `delivery_class`, `consumer_visibility`,
  and `audit_visibility`, but these values are derived-only metadata, never operator inputs.
- I-111: `DistributionClassificationReport` MUST reuse existing `ExecutionHandoffReport`
    and persisted `ABCInferenceEnvelope` artifacts. Shadow/control outputs MUST NOT be promoted
  into consumer-visible signal handoffs.
- I-112: Route-aware delivery reports are read-only. They MUST NOT write back, submit trades,
    auto-switch routes, or auto-promote models.

---

### 32. Sprint-20 — External Consumer Collector & Acknowledgement Orchestration

**Status: ✅ Implemented — audit-only consumer acknowledgement surface**

Sprint 20 defines the controlled consumer acknowledgement layer on top of the existing
SignalHandoff-based handoff surface. Consumers may read and acknowledge signal handoffs.
Acknowledgement is audit-only and has no operational effect.

**Core principle (I-116): Acknowledgement is AUDIT ONLY.**
- Acknowledgement ≠ execution
- Acknowledgement ≠ approval
- Consumer state ≠ routing decision (I-117, I-121, I-122)
- No reverse channel into KAI core analysis (I-118, I-120)

**Invariants (I-116–I-122):**
- I-116: Consumer acknowledgement is AUDIT ONLY. The record is a receipt, not an approval.
- I-117: Acknowledgement does not confirm trade intent, execution, or routing eligibility.
- I-118: Acknowledgement MUST NOT write back to KAI core DB or modify any SignalHandoff.
- I-119: Acknowledgement exists only when receipt occurred. There is no pending write-back state in core models.
- I-120: Consumer acknowledgements are stored append-only in `consumer_acknowledgements.jsonl`.
  Existing records are never overwritten or deleted.
- I-121: Consumer state (who acknowledged what) is NEVER a routing decision input.
- I-122: Aggregate collector surfaces are read-only count summaries. They contain no execution
  state, no routing mutation, and no write-back capability.

**Surface (Sprint 20C — kanonisch):**
- Canonical runtime path: `app/research/execution_handoff.py` + `app/research/distribution.py`
- Audit artifact type: `HandoffAcknowledgement` (execution_handoff.py, frozen=True)
- Rehydration helper: `handoff_acknowledgement_from_dict(payload)` — fail-closed parser for persisted JSONL rows
- Collector report: `HandoffCollectorSummaryReport` (distribution.py)
- Append-only audit file: `HANDOFF_ACK_JSONL_FILENAME = "consumer_acknowledgements.jsonl"`
- `create_handoff_acknowledgement(handoff, *, consumer_agent_id, notes="")` — validates visibility, creates immutable audit record; raises PermissionError for non-visible handoffs
- `append_handoff_acknowledgement_jsonl(ack, path)` — append-only JSONL write
- `load_handoff_acknowledgements(path)` — read-only load, skips malformed lines
- `build_handoff_collector_summary(handoffs, acks)` — combined handoff + ack counts → HandoffCollectorSummaryReport
- MCP write: `acknowledge_signal_handoff(handoff_path, handoff_id, consumer_agent_id, notes="")` — audit-only, PermissionError on hidden handoffs
- MCP read: `get_handoff_collector_summary(handoff_path, acknowledgement_path)` — read-only collector
- MCP compatibility alias only: `get_handoff_summary(handoff_path, acknowledgement_path)` — not the canonical name
- CLI write: `research handoff-acknowledge <handoff_file> --handoff-id ... --consumer-agent-id ...`
- CLI read: `research handoff-collector-summary <handoff_file> [--ack-file ...]`
- CLI compatibility aliases only: `research handoff-summary <handoff_file> [--ack-file ...]`, `research consumer-ack <handoff_file> <handoff_id> --consumer-agent-id ...`
- Superseded/removed runtime module only: `app/research/consumer_collection.py`

**What is explicitly excluded:**
- No DB mutation in the acknowledgement path
- No auto-escalation of acknowledged signals to trading
- No order semantics in any acknowledgement artifact
- No broker access, no execution engine interface
- Collector surface ≠ execution engine


---

### 33. Sprint-21 — Operational Readiness Surface

**Status: ✅ Implemented — observational-only readiness surface**

Sprint 21/22 defines a small operational readiness layer for route health, provider health,
distribution drift, collector backlog, artifact freshness, and shadow/control visibility.
The report is derived from existing handoff, acknowledgement, route-state, ABC-envelope,
and alert-audit artifacts only.

**Core principle (I-123): Readiness is OBSERVATIONAL ONLY.**
- Readiness ≠ execution trigger
- Readiness ≠ auto-remediation (I-124)
- Readiness ≠ routing decision (I-128)
- Readiness ≠ auto-promotion (I-129)
- No readiness report modifies any signal, handoff, route profile, or KAI state (I-126)

**Invariants (I-123–I-130):**
- I-123: Readiness reports are OBSERVATIONAL ONLY. No report triggers execution, routing, or state change.
- I-124: Readiness generation ≠ auto-remediation. Operator must act manually.
- I-125: Alert severity does NOT map to trade execution priority.
- I-126: No readiness report modifies any SignalHandoff, HandoffAcknowledgement, or route profile.
- I-127: Readiness reports are written as structured JSON snapshots only.
- I-128: Auto-routing is NEVER triggered by any readiness issue.
- I-129: Auto-promotion of signals or models is NEVER triggered by any readiness issue.
- I-130: Readiness issues are derived from existing artifacts only. No second monitoring or remediation stack is allowed.

**Surface:**
- Canonical module: `app/research/operational_readiness.py`
- Report types: `OperationalReadinessReport`, `ReadinessIssue`, `RouteReadinessSummary`, `AlertDispatchSummary`, `ProviderHealthSummary`, `DistributionDriftSummary`, `OperationalArtifactRefs`
- Builder: `build_operational_readiness_report(...)`
- Persistence: `save_operational_readiness_report(report, path)`
- MCP read: `get_operational_readiness_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours)` — read-only
- CLI: `research readiness-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--out ...]`
- Embedded summaries: `provider_health_summary` and `distribution_drift_summary` live inside the canonical readiness report
- Existing helper remains: `alerts audit-summary` summarizes the append-only alert audit log
- Derived read views only: MCP `get_provider_health(...)` and `get_distribution_drift(...)`, CLI `research provider-health` and `research drift-summary`, all computed from the canonical readiness stack
- Superseded from MCP/CLI surface: `app/research/operational_alerts.py` (module exists, not imported by MCP/CLI; standalone check library only), `get_operational_alerts(...)` MCP tool (never shipped), `research operational-alerts` CLI shim (raises Exit(1))

**What is explicitly excluded:**
- No readiness report triggers execution, order placement, or broker access
- No auto-remediation or self-healing
- No auto-routing based on alert state
- No auto-promotion of signals or models
- No silent state mutation


---

### 34. Sprint-22 — Provider Health & Distribution Drift Monitoring Surface

**Status: ✅ Implemented — observational-only provider and drift monitoring**

Sprint 22 consolidates the Monitoring/Readiness stack and provides a dedicated operator-facing surface for provider health and distribution drift. No new parallel architecture is introduced. The canonical backend is `operational_readiness.py`; provider health and drift are derived read views only.

**Core principle (I-131): Provider health and distribution drift monitoring is OBSERVATIONAL ONLY.**

**Invariants:**

- I-131: Provider health and drift observations NEVER trigger execution, order placement, or broker access.
- I-132: No auto-routing based on provider health or drift status. Operator intervention is always required.
- I-133: No auto-promotion of signals, models, or routes based on health status.
- I-134: `get_provider_health` and `get_distribution_drift` are read-only MCP tools. No state is mutated.
- I-135: All health and drift artifacts derive from existing runtime artifacts only. No second monitoring stack is introduced.
- I-136: Provider-health and drift outputs expose issue context only. Guidance is advisory only — it never implies or enables execution.
- I-137: Monitoring outputs remain read-only at all times. No remediation, routing, or promotion flags are introduced.
- I-138: CLI commands `research provider-health` and `research drift-summary` produce human-readable operator output only. No write-back or DB mutation.

**Canonical modules:**
- `app/research/operational_readiness.py` — canonical readiness report: `OperationalReadinessReport`, `ProviderHealthSummary` (per-path health rows), `DistributionDriftSummary` (aggregate drift indicators), `build_operational_readiness_report`, `save_operational_readiness_report`
- `app/research/operational_alerts.py` — standalone check library (exists, not in MCP/CLI path); superseded as production surface by Sprint 22

**MCP surface (read-only):**
- `get_operational_readiness_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours)` — canonical full readiness report
- `get_provider_health(handoff_path, state_path, abc_output_path)` — returns the readiness-derived `provider_health_summary` slice
- `get_distribution_drift(handoff_path, state_path, abc_output_path)` — returns the readiness-derived `distribution_drift_summary` slice
- All tools validate workspace path confinement (I-95); provider/drift views are bounded subsets of the canonical readiness report

**CLI surface:**
- `research readiness-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--out ...]` — canonical operator-facing monitoring command
- `research provider-health [--handoff-file ...] [--state ...] [--abc-output ...] [--out ...]` — human-readable provider health view derived from readiness
- `research drift-summary [--handoff-file ...] [--state ...] [--abc-output ...] [--out ...]` — human-readable distribution drift view derived from readiness

**Monitoring artifact contract fields:**
- `ProviderHealthEntry` (in readiness report): `provider`, `path_id`, `path_type` (primary|shadow|control), `status` (healthy|degraded|unavailable), `sample_count`, `success_count`, `failure_count`, `expected`
- `DistributionDriftSummary` (in readiness report): `status` (nominal|warning|critical), `classification_mismatch_count`, `visibility_mismatch_count`, `unexpected_visible_audit_count`, `unknown_path_count`
- Provider health slice output: `report_type="provider_health_summary"`, `derived_from="operational_readiness"`, `generated_at`, `readiness_status`, `highest_severity`, `provider_count`, `healthy_count`, `degraded_count`, `unavailable_count`, `entries`, `issues`
- Distribution drift slice output: `report_type="distribution_drift_summary"`, `derived_from="operational_readiness"`, `generated_at`, `readiness_status`, `highest_severity`, plus all `DistributionDriftSummary` fields, `issues`

**What is explicitly excluded:**
- No execution trigger of any kind from health or drift alerts
- No automated route switching or failover
- No broker or exchange access from the monitoring surface
- No trading semantics: monitoring output describes system observation state only

---

### 35. Sprint-23 — Protective Gates & Remediation Recommendations Surface

**Status: ✅ Implemented — readiness-derived protective gates and advisory-only remediation**

Sprint 23 extends the canonical `operational_readiness.py` stack with a small
protective gate layer. The gate surface is purely observational and adapts
existing readiness, provider-health, drift, backlog, and artifact-state data.
No second monitoring or remediation engine is introduced.

**Core principle (I-139): Protective gates are READ-ONLY operator guidance.**

**Invariants:**

- I-139: Protective gates NEVER trigger execution, order placement, or broker access.
- I-140: Protective gates NEVER mutate core DB state, route state, handoff state, or acknowledgements.
- I-141: Remediation recommendations are hints only. No auto-remediation, auto-routing, or auto-promotion is allowed.
- I-142: Gate classification derives only from canonical readiness issues and summaries. No parallel gate-calculation stack is permitted.
- I-143: `get_protective_gate_summary` and `get_remediation_recommendations` are read-only MCP tools.
- I-144: CLI commands `research gate-summary` and `research remediation-recommendations` are operator-facing read views only.
- I-145: `app/research/protective_gates.py` is superseded. The canonical gate contract lives in `app/research/operational_readiness.py`.

**Canonical module:**

- `app/research/operational_readiness.py` — internal gate model: `ProtectiveGateSummary` (gate_status, blocking_count, warning_count, advisory_count, items), `ProtectiveGateItem` (gate_status, severity, category, summary, subsystem, blocking_reason, recommended_actions, evidence_refs), embedded in `OperationalReadinessReport`

**Gate contract fields:**

- `gate_status` — `clear`, `blocking`, `warning`, or `advisory`
- `severity` — inherited readiness severity (`info`, `warning`, `critical`)
- `blocking_reason` — explicit blocking explanation for blocking items only
- `subsystem` — `handoff`, `artifacts`, `providers`, `distribution`, `routing`, or `monitoring`
- `recommended_actions` — ordered operator-only hints
- `evidence_refs` — source/category/path/provider references tied to existing artifacts only

**MCP surface (read-only):**

- `get_protective_gate_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours)` — returns readiness-derived gate counts and items
- `get_remediation_recommendations(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours)` — returns read-only recommendation rows derived from gate items

**CLI surface:**

- `research gate-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--stale-after-hours N] [--out ...]`
- `research remediation-recommendations [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--stale-after-hours N] [--out ...]`

**What is explicitly excluded:**

- No write-back to `CanonicalDocument`, signal handoffs, route profiles, or collector state
- No acknowledgement side effects beyond existing append-only audit flows
- No trading semantics or execution enablement
- No open remote-superuser or remediation control plane

---

### 36. Sprint-24 — Artifact Lifecycle Management Surface

**Status: ✅ Implemented — operator-triggered inventory and safe archival of stale artifacts**

Sprint 24 closes the operational loop established in Sprints 21–23.
The protective gate surface (Sprint 23) detects stale artifacts and issues advisory recommendations.
Sprint 24 provides the operator tool to act on those recommendations: a read-only inventory and a
dry-run-safe rotation command. No auto-remediation, no deletion, no execution enablement.

**Core principle (I-146): Artifact lifecycle management is OPERATOR-TRIGGERED ONLY.**

**Invariants:**

- I-146: `artifact_lifecycle.py` is the sole canonical artifact lifecycle management layer. No second stack.
- I-147: `rotate_stale_artifacts()` MUST default to `dry_run=True`. No filesystem writes when `dry_run=True`.
- I-148: Rotation archives to `artifacts/archive/<YYYYMMDD_HHMMSS>/` ONLY. Never deletes, never overwrites source files.
- I-149: `get_artifact_inventory` MCP tool is strictly read-only. No filesystem mutations.
- I-150: `ArtifactInventoryReport.execution_enabled` MUST always be `False`.
- I-151: Stale detection uses file `mtime` only — no content inspection of artifact files.
- I-152: CLI `artifact-rotate` defaults to `--dry-run`. Operator must pass `--no-dry-run` for actual archival.

**Canonical module:**

- `app/research/artifact_lifecycle.py` — `ArtifactEntry` (frozen), `ArtifactInventoryReport` (frozen, execution_enabled=False), `ArtifactRotationSummary` (frozen), `build_artifact_inventory(artifacts_dir, stale_after_days=30.0)`, `rotate_stale_artifacts(artifacts_dir, stale_after_days=30.0, *, dry_run=True)`, `save_artifact_inventory()`, `save_artifact_rotation_summary()`

**Managed file types:** `.json` and `.jsonl` only. Directories (including `archive/`) always skipped.

**MCP surface (read-only):**

- `get_artifact_inventory(artifacts_dir, stale_after_days)` — workspace-confined read-only inventory (I-149)

**CLI surface:**

- `research artifact-inventory [--artifacts-dir DIR] [--stale-after-days N] [--out FILE]` — read-only inventory report
- `research artifact-rotate [--artifacts-dir DIR] [--stale-after-days N] [--dry-run/--no-dry-run] [--out FILE]` — dry-run-safe rotation (default: dry-run, I-152)

**Archive contract:**

- Archive location: `artifacts/archive/YYYYMMDD_HHMMSS/` (one subdir per rotation run)
- Files are moved (`shutil.move`), never deleted, never overwritten (I-148)
- `archive/` subdir itself is never inventoried or rotated

**What is explicitly excluded:**

- No automatic/scheduled rotation (must be operator-triggered)
- No deletion of any artifact file (move-only)
- No content inspection of artifact files (mtime-based only, I-151)
- No write-back to `CanonicalDocument`, route state, or signal handoffs
- No trading semantics, no execution enablement

---

### 37. Sprint-25 — Safe Artifact Retention & Cleanup Policy

**Status: ✅ Implemented — read-only retention classification surface**

Sprint 25 extends `artifact_lifecycle.py` with explicit retention policy classification.
Each artifact is assigned an `artifact_class` and `retention_class` to guide operator decisions
about what is safe to archive. Retention policy is advisory only — no auto-cleanup, no auto-deletion.

**Core principle (I-153): Retention policy is classification only. No cleanup is triggered automatically.**

**Invariants:**

- I-153: Retention policy is classification only. No cleanup triggered automatically.
- I-154: `ArtifactRetentionEntry.delete_eligible` MUST always be `False`. Deletion is never platform-initiated.
- I-155: `protected=True` artifacts MUST NOT appear as rotation candidates.
- I-156: AUDIT_TRAIL artifacts always protected: `mcp_write_audit.jsonl`, `consumer_acknowledgements.jsonl`, `alert_audit.jsonl`, and canonical signal handoff artifacts such as `handoffs.jsonl`, `handoff.json`, and `execution_signal_handoff*.json`.
- I-157: PROMOTION_RECORD artifacts always protected: `promotion_record.json`.
- I-158: TRAINING_DATA artifacts always protected: `teacher.jsonl`, `candidate.jsonl`, `tuning_manifest.json`.
- I-159: ACTIVE_STATE artifacts (`active_route_profile.json`) protected when route is active.
- I-160: `build_retention_report()` is pure computation — no DB reads, no LLM calls, no network, no filesystem writes.
- I-161: `ArtifactRetentionReport.execution_enabled` and `write_back_allowed` MUST always be `False`.
- I-162: Cleanup eligibility is archive-only and advisory. `dry_run_default=True` for every cleanup summary surface.
- I-163: Protected artifact summaries are read-only projections derived from the canonical retention report only.
- I-164: `rotate_stale_artifacts()` may archive only `rotatable=True` artifacts; protected and review-required artifacts are skipped fail-closed.

**Artifact classes:**

| Class | Constant | Default retention | Examples |
|---|---|---|---|
| `audit_trail` | `ARTIFACT_CLASS_AUDIT_TRAIL` | protected (I-156) | mcp_write_audit, consumer_ack, alert_audit, signal handoff artifacts |
| `promotion` | `ARTIFACT_CLASS_PROMOTION` | protected (I-157) | promotion_record.json |
| `training_data` | `ARTIFACT_CLASS_TRAINING_DATA` | protected (I-158) | teacher, candidate, tuning_manifest |
| `active_state` | `ARTIFACT_CLASS_ACTIVE_STATE` | protected if active (I-159), else rotatable/review |active_route_profile.json |
| `evaluation` | `ARTIFACT_CLASS_EVALUATION` | rotatable when stale | benchmark.json, report.json |
| `operational` | `ARTIFACT_CLASS_OPERATIONAL` | rotatable when stale | readiness/gate/remediation/inventory/cleanup summaries |
| `unknown` | `ARTIFACT_CLASS_UNKNOWN` | review_required | any unrecognised filename |

**Retention classes:**

- `protected` — operator MUST NOT archive; critical audit/training/state data
- `rotatable` — stale and safe to archive via `artifact-rotate`
- `review_required` — operator must confirm classification before any action

**Canonical module:**

- `app/research/artifact_lifecycle.py` (extended Sprint 25):
  - `ArtifactRetentionEntry` (frozen): name/path/size_bytes/modified_at/age_days/status/artifact_class/retention_class/protected/rotatable/delete_eligible=False/retention_rationale/operator_guidance
  - `ArtifactRetentionReport` (frozen): execution_enabled=False, write_back_allowed=False, delete_eligible_count=0
  - `ArtifactCleanupEligibilitySummary` (frozen): cleanup_eligible_count, dry_run_default=True, candidates, delete_eligible_count=0
  - `ProtectedArtifactSummary` (frozen): protected_count, entries, delete_eligible_count=0
  - `classify_artifact_retention(entry, *, active_route_active=False)` — pure classification
  - `build_retention_report(artifacts_dir, stale_after_days=30.0, *, active_route_active=False)`
  - `build_cleanup_eligibility_summary(report)` / `build_protected_artifact_summary(report)` — pure report projections only
  - `save_retention_report(report, path)`
  - `save_cleanup_eligibility_summary(summary, path)` / `save_protected_artifact_summary(summary, path)`

> **Sprint 26 extension (→ §38):** `ReviewRequiredArtifactSummary`, `build_review_required_summary()`, `save_review_required_summary()`, `get_review_required_summary`, `research review-required-summary` wurden in Sprint 26 ergänzt. Kanonische Dokumentation: §38.

**MCP surface (read-only):**

- `get_artifact_retention_report(artifacts_dir, stale_after_days, state_path)` — workspace-confined, read-only (I-153/I-160)
- `get_cleanup_eligibility_summary(artifacts_dir, stale_after_days, state_path)` — advisory archive eligibility only
- `get_protected_artifact_summary(artifacts_dir, stale_after_days, state_path)` — protected entries only

> **Sprint 26 extension (→ §38):** `get_review_required_summary` in §38 dokumentiert.

**CLI surface:**

- `research artifact-retention [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` — read-only classification view
- `research cleanup-eligibility-summary [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` — archive-eligibility summary, dry-run-first
- `research protected-artifact-summary [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` — protected artifact summary only

> **Sprint 26 extension (→ §38):** `research review-required-summary` in §38 dokumentiert.

**What is explicitly excluded:**

- No automatic cleanup or deletion (I-153/I-154)
- No write-back to routing, handoffs, or DB state
- No trading semantics or execution enablement
- No second classification stack alongside this one


---

## §38 Sprint 26/26C — Artifact Governance/Review Surface (Canonical)

**Module:** `app/research/artifact_lifecycle.py` (canonical, extended from Sprint 25)

**Canonical governance/review surface:**

| Class | Purpose |
|---|---|
| `ArtifactRetentionReport` | Single classification source for protected / rotatable / review_required |
| `ArtifactCleanupEligibilitySummary` | Advisory archive-eligibility view, dry-run-first |
| `ProtectedArtifactSummary` | Protected artifact visibility for operators |
| `ReviewRequiredArtifactSummary` | Operator review queue with `retention_rationale` and `operator_guidance` per entry |

**Canonical functions:**

- `build_retention_report(artifacts_dir, stale_after_days=30.0, *, active_route_active=False)` — single classification source
- `build_cleanup_eligibility_summary(report)` / `build_protected_artifact_summary(report)` / `build_review_required_summary(report)` — pure projections only
- `save_retention_report(report, path)` / `save_cleanup_eligibility_summary(summary, path)` / `save_protected_artifact_summary(summary, path)` / `save_review_required_summary(summary, path)` — JSON persistence

**Canonical MCP surface (all read-only, workspace-confined via I-95):**

| Tool | Returns |
|---|---|
| `get_artifact_retention_report(artifacts_dir, stale_after_days, state_path)` | `ArtifactRetentionReport` |
| `get_cleanup_eligibility_summary(artifacts_dir, stale_after_days, state_path)` | `ArtifactCleanupEligibilitySummary` |
| `get_protected_artifact_summary(artifacts_dir, stale_after_days, state_path)` | `ProtectedArtifactSummary` |
| `get_review_required_summary(artifacts_dir, stale_after_days, state_path)` | `ReviewRequiredArtifactSummary` |

**Canonical CLI surface:**

| Command | Output |
|---|---|
| `research artifact-retention [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` | Full retention classification view |
| `research cleanup-eligibility-summary [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` | Cleanup/archive eligibility summary |
| `research protected-artifact-summary [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` | Protected artifact summary |
| `research review-required-summary [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` | Review queue with rationale/guidance |

**Superseded surface names:**

- `ArtifactPolicyRationaleSummary`
- `ArtifactGovernanceSummary`
- `build_policy_rationale_summary(...)`
- `build_governance_summary(...)`
- `save_policy_rationale_summary(...)`
- `save_governance_summary(...)`
- `get_policy_rationale_summary(...)`
- `get_governance_summary(...)`
- `research governance-summary`

**What is explicitly excluded:**

- No automatic cleanup, deletion, or remediation
- No write-back to routing, handoffs, or DB state
- No trading semantics or execution enablement
- No second governance/lifecycle stack alongside this one


---

## §39 Sprint 27 - Safe Operational Escalation Surface (Canonical)

**Status: ✅ Implemented - read-only escalation surface on the canonical readiness and governance stacks**

Sprint 27 adds a small operator-facing escalation layer on top of the existing
readiness, protective-gate, and artifact-governance surfaces. It does not
introduce a second monitoring or gate architecture. Escalation is purely a
projection of existing canonical reports.

**Invariants:**

- I-169: `OperationalEscalationSummary.execution_enabled` MUST always be `False`.
- I-170: `OperationalEscalationSummary.write_back_allowed` MUST always be `False`.
- I-171: Escalation classification is derived only from `ProtectiveGateSummary` plus `ReviewRequiredArtifactSummary`.
- I-172: Blocking escalation rows MUST come only from canonical blocking gate items; escalation MUST NOT invent new blocking reasons.
- I-173: Review-required escalation rows MUST remain advisory and operator-facing only; they MUST NOT trigger cleanup, archival, or deletion.
- I-174: `BlockingSummary` and `OperatorActionSummary` are read-only projections of the canonical escalation summary only.
- I-175: CLI and MCP escalation surfaces MUST expose only `escalation-summary`, `blocking-summary`, and `operator-action-summary` / `get_escalation_summary`, `get_blocking_summary`, and `get_operator_action_summary` as the canonical names.
- I-176: No escalation surface may mutate route state, handoffs, acknowledgements, artifact retention classes, or any core DB state.

**Canonical module:**

- `app/research/operational_readiness.py`
  - `OperationalEscalationItem`
  - `OperationalEscalationSummary`
  - `BlockingSummary`
  - `OperatorActionSummary`
  - `build_operational_escalation_summary(readiness_report, *, review_required_summary=None)`
  - `build_blocking_summary(summary)`
  - `build_operator_action_summary(summary)`
  - `save_operational_escalation_summary(summary, path)`

**Canonical payload fields:**

- `escalation_status`
- `severity`
- `blocking`
- `subsystem`
- `operator_action_required`
- `blocking_reason`
- `evidence_refs`
- `advisory_notes`

**Canonical MCP surface (read-only, workspace-confined via I-95):**

| Tool | Returns |
|---|---|
| `get_escalation_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `OperationalEscalationSummary` |
| `get_blocking_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `BlockingSummary` |
| `get_operator_action_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `OperatorActionSummary` |

**Canonical CLI surface:**

| Command | Output |
|---|---|
| `research escalation-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Full escalation view |
| `research blocking-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Blocking-only view |
| `research operator-action-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Operator-action-required and review-required view |

**What is explicitly excluded:**

- No auto-remediation
- No auto-routing or auto-promotion
- No trading execution
- No write-back into readiness, gate, lifecycle, or core-state artifacts

**Sprint 27C CLI invariant (added post-Sprint-27):**

CLI commands (`research escalation-summary`, `research blocking-summary`, `research operator-action-summary`) call MCP server tools via asyncio and pass `--artifacts-dir` as a workspace-relative path resolved through the MCP workspace guard (I-95). The artifact-lifecycle CLI commands (`research artifact-retention`, `research cleanup-eligibility-summary`, `research protected-artifact-summary`, `research review-required-summary`, `research artifact-rotate`) call `artifact_lifecycle` functions directly — the MCP workspace guard (I-95) applies to the MCP protocol context only, not to CLI invocations.

> **Note:** The `research escalation-summary` CLI command had a pre-existing `state` parameter bug (duplicate `out` replacing `state`) corrected in Sprint 27C. Canonical CLI parameter list: `--handoff-file`, `--ack-file`, `--state`, `--abc-output`, `--alert-audit-dir`, `--stale-after-hours`, `--artifacts-dir`, `--retention-stale-after-days`, `--out`.

---

## §40 Sprint 28 - Safe Operator Action Queue (Canonical)

**Status: ✅ Implemented - read-only operator action queue projected from the canonical escalation surface**

Sprint 28 adds a small operator-facing action queue on top of the canonical
Sprint-27 escalation stack. The queue does not compute gates or readiness a
second time. It adapts existing escalation rows into stable, prioritised
operator work items only.

**Invariants:**

- I-177: `ActionQueueSummary.execution_enabled` MUST always be `False`.
- I-178: `ActionQueueSummary.write_back_allowed` MUST always be `False`.
- I-179: Action queue formation derives only from `OperationalEscalationSummary`; no second escalation or gate stack is permitted.
- I-180: Blocking queue entries MUST come only from canonical blocking escalation rows.
- I-181: Review-required queue entries MUST stay advisory and operator-facing only; they MUST NOT trigger cleanup, archival, or deletion.
- I-182: `BlockingActionsSummary`, `PrioritizedActionsSummary`, and `ReviewRequiredActionsSummary` are read-only projections of the canonical action queue only.
- I-183: CLI and MCP action-queue surfaces MUST expose only `action-queue-summary`, `blocking-actions`, `prioritized-actions`, and `review-required-actions` / `get_action_queue_summary`, `get_blocking_actions`, `get_prioritized_actions`, and `get_review_required_actions` as canonical names.
- I-184: No action queue surface may mutate route state, handoffs, acknowledgements, artifact retention classes, or any core DB state.

**Canonical module:**

- `app/research/operational_readiness.py`
  - `ActionQueueItem`
  - `ActionQueueSummary`
  - `BlockingActionsSummary`
  - `PrioritizedActionsSummary`
  - `ReviewRequiredActionsSummary`
  - `build_action_queue_summary(summary)`
  - `build_blocking_actions(summary)`
  - `build_prioritized_actions(summary)`
  - `build_review_required_actions(summary)`

**Canonical payload fields:**

- `action_id`
- `severity`
- `priority`
- `subsystem`
- `operator_action_required`
- `evidence_refs`
- `queue_status`

**Canonical MCP surface (read-only, workspace-confined via I-95):**

| Tool | Returns |
|---|---|
| `get_action_queue_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `ActionQueueSummary` |
| `get_blocking_actions(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `BlockingActionsSummary` |
| `get_prioritized_actions(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `PrioritizedActionsSummary` |
| `get_review_required_actions(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `ReviewRequiredActionsSummary` |

**Canonical CLI surface:**

| Command | Output |
|---|---|
| `research action-queue-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Full operator action queue |
| `research blocking-actions [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Blocking-only queue slice |
| `research prioritized-actions [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Priority-ordered queue slice |
| `research review-required-actions [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Review-required queue slice |

**What is explicitly excluded:**

- No auto-remediation
- No auto-routing or auto-promotion
- No trading execution
- No write-back into readiness, escalation, governance, lifecycle, or core-state artifacts

---

## §41 Sprint 29 - Read-Only Operator Decision Pack (Canonical)

**Status: ✅ Implemented - read-only operator decision pack bundling canonical summaries only**

Sprint 29 adds a small operator-facing decision pack on top of the existing
readiness, escalation, action-queue, and governance surfaces. The pack does
not recompute readiness, gates, or governance. It bundles existing summaries
into one read-only operator snapshot only.

**Invariants:**

- I-185: `OperatorDecisionPack.execution_enabled` MUST always be `False`.
- I-186: `OperatorDecisionPack.write_back_allowed` MUST always be `False`.
- I-187: Decision-pack formation MUST reuse existing canonical readiness, blocking, action-queue, and review-required summaries; no second readiness, gate, escalation, or governance stack is permitted.
- I-188: `overall_status` MUST be derived from the bundled summaries only.
- I-189: `blocking_count`, `review_required_count`, and `action_queue_count` MUST reflect bundled summary state only.
- I-190: `affected_subsystems`, `operator_guidance`, and `evidence_refs` MUST be aggregate read-only projections from existing summaries only.
- I-191: CLI and MCP decision-pack surfaces MUST expose only `decision-pack-summary` / `operator-decision-pack` and `get_decision_pack_summary` / `get_operator_decision_pack` as canonical names.
- I-192: No decision-pack surface may mutate route state, handoffs, acknowledgements, retention classes, archived artifacts, or any trading/execution state.

**Canonical module:**

- `app/research/operational_readiness.py`
  - `OperatorDecisionPack`
  - `build_operator_decision_pack(...)`
  - `save_operator_decision_pack(...)`

**Canonical payload fields:**

- `overall_status`
- `blocking_count`
- `review_required_count`
- `action_queue_count`
- `affected_subsystems`
- `operator_guidance`
- `evidence_refs`
- `readiness_summary`
- `blocking_summary`
- `action_queue_summary`
- `review_required_summary`

**Canonical MCP surface (read-only, workspace-confined via I-95):**

| Tool | Returns |
|---|---|
| `get_decision_pack_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `OperatorDecisionPack` |
| `get_operator_decision_pack(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | Backward-compatible alias of `OperatorDecisionPack` |

**Canonical CLI surface:**

| Command | Output |
|---|---|
| `research decision-pack-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Canonical operator decision pack |
| `research operator-decision-pack [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--retention-stale-after-days N] [--out FILE]` | Alias of canonical operator decision pack |

**What is explicitly excluded:**

- No auto-remediation
- No auto-routing or auto-promotion
- No trading execution
- No destructive cleanup or deletion
- No decision-pack overview/focus/affected-subsystem side-stack

---

## §42 Sprint 30/30C — Read-Only Operator Runbook (Kanonisch)

**Status: ✅ Sprint 30C final — read-only operator runbook with validated command refs**

Sprint 30 adds a small operator-facing runbook surface on top of the canonical
decision pack. The runbook does NOT recompute readiness, escalation, governance,
or queue state. It derives ordered next steps from the existing `OperatorDecisionPack`
and validates every referenced CLI command against the actually registered
`research` command set (fail-closed).

**Invariants (I-193–I-200):**

- I-193: `OperatorRunbookSummary.execution_enabled` MUST always be `False`.
- I-194: `OperatorRunbookSummary.write_back_allowed` MUST always be `False`.
- I-195: `OperatorRunbookSummary.auto_remediation_enabled` MUST always be `False`. No auto-remediation of any kind.
- I-196: `OperatorRunbookSummary.auto_routing_enabled` MUST always be `False`. No auto-routing or auto-promotion.
- I-197: Runbook formation MUST derive ONLY from the canonical `OperatorDecisionPack`. No second readiness, escalation, queue, or governance stack is permitted.
- I-198: Every `RunbookStep.command_refs` entry MUST point to a real registered `research` sub-command. Superseded, removed, or hypothetical command names MUST fail closed.
- I-199: `OperatorRunbookSummary.report_type` MUST always be `"operator_runbook_summary"`.
- I-200: No runbook surface may trigger trade execution, auto-routing, auto-promotion, DB mutation, or artifact deletion.

**Canonical module:**

- `app/research/operational_readiness.py`
  - `RunbookStep` (frozen dataclass)
  - `OperatorRunbookSummary` (frozen dataclass)
  - `build_operator_runbook(*, decision_pack: OperatorDecisionPack) -> OperatorRunbookSummary`
  - `save_operator_runbook(runbook, output_path) -> Path`
  - `RUNBOOK_COMMAND_*` constants (canonical command strings)

**Canonical payload fields (`OperatorRunbookSummary.to_json_dict()`):**

- `report_type` — always `"operator_runbook_summary"`
- `overall_status`, `blocking_count`, `review_required_count`, `action_queue_count`
- `affected_subsystems`, `operator_guidance`, `evidence_refs`, `command_refs`
- `steps` (all `RunbookStep` objects, ordered by priority then queue_status)
- `next_steps` (first ≤3 steps)
- `generated_at`, `interface_mode`, `execution_enabled`, `write_back_allowed`

**Canonical MCP surface (read-only, workspace-confined via I-95):**

| Tool | Returns |
|---|---|
| `get_operator_runbook(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `OperatorRunbookSummary.to_json_dict()` |

**Canonical CLI surface (drei eigenständige Kommandos — kein Alias):**

| Command | Output |
|---|---|
| `research operator-runbook [--handoff-path ...] [--state-path ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--stale-after-days N] [--out FILE]` | Vollständiger Runbook mit allen Steps und Guidance |
| `research runbook-summary [--handoff-path ...] [--state-path ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--stale-after-days N]` | Kompakter Status-Überblick (kein --out) |
| `research runbook-next-steps [--handoff-path ...] [--state-path ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--stale-after-days N]` | Nur next_steps slice |

**Command Safety Guardrail:**

- `get_registered_research_command_names()` in `app/cli/main.py` liefert die Referenzmenge
- `get_invalid_research_command_refs()` validiert fail-closed beim MCP-Call
- Superseded Commands (`governance-summary`, `operator-decision-pack`) dürfen NICHT in `command_refs` erscheinen

**What is explicitly excluded:**

- No auto-remediation
- No auto-routing or auto-promotion
- No trading execution
- No destructive cleanup or deletion
- No superseded command refs inside the runbook payload

---

## §43 Sprint 31 — CLI Contract Lock & MCP Surface Lock (Canonical)

**Ziel**: Den kanonischen CLI- und MCP-Surface nach Sprint 30/30C einzufrieren, Coverage-Lücken zu schließen und Drift-Prävention dauerhaft sicherzustellen. Keine neuen Business-Features — ausschließlich Stabilisierung, Coverage und Contract-Klarheit.

**Invarianten: I-201–I-210**

### Kanonische CLI-Oberfläche (44 Commands, eingefroren nach Sprint 31)

| App | Count |
|---|---|
| `query_app` | 4 |
| `research_app` | 40 |

**Autoritative Referenzmenge:** `get_registered_research_command_names()` in `app/cli/main.py`

**Coverage-Pflicht (I-203):** Jeder kanonische CLI-Command MUSS mindestens einen targeted Test haben. Nach Sprint 31: 0 ungetestete Commands.

**6 Coverage-Lücken geschlossen in Sprint 31:**

| Command | Neue Tests |
|---|---|
| `research signals` | in-help + no-candidates (DB mock) |
| `research benchmark-companion-run` | in-help + missing-teacher-file |
| `research check-promotion` | in-help + missing-file + all-pass + gate-fail |
| `research prepare-tuning-artifact` | in-help + missing-teacher-file |
| `research record-promotion` | in-help + missing-file + gates-blocked |
| `research evaluate` | in-help + no-teacher-docs (DB mock) |

### Kanonische MCP-Oberfläche (38 registrierte Tools, konsolidiert nach Sprint 32)

| Kategorie | Count |
|---|---|
| `canonical_read_tools` | 30 |
| `guarded_write_tools` | 4 |
| `workflow_helpers` | 1 |
| `compatibility_aliases` | 2 |
| `superseded_aliases` | 1 |

**Autoritative Referenzmenge:** `get_mcp_tool_inventory()` in `app/agents/mcp_server.py`

**Capability-Projektion:** `get_mcp_capabilities()` exponiert dieselbe Inventarlogik als:
- `read_tools` = nur kanonische read-only MCP-Tools
- `guarded_write_tools` / `write_tools` = dieselben vier guarded-write Tools
- `aliases` = kompatible Alias-Namen mit Ziel-Tool und Klassifikation
- `superseded_tools` = registrierte, aber superseded Alias-Namen mit Replacement

**Compatibility aliases (explizit ausgeschlossen aus `read_tools`):**
- `get_handoff_summary` = Alias von `get_handoff_collector_summary`, read-only
- `get_operator_decision_pack` = Alias von `get_decision_pack_summary`, read-only

**Superseded alias (explizit ausgeschlossen aus `read_tools`):**
- `get_operational_escalation_summary` = Alias von `get_escalation_summary`, superseded

**`get_narrative_clusters` (I-205):** Registriertes `@mcp.tool()` und kanonisches read-only Tool — MUSS in `read_tools` erscheinen.

### Command Drift Prevention

- `get_registered_research_command_names()` in `app/cli/main.py` ist die autoritative CLI-Referenzmenge
- `get_invalid_research_command_refs()` validiert fail-closed bei jedem MCP-Runbook-Call
- `get_mcp_tool_inventory()` ist die autoritative MCP-Referenzmenge
- `test_research_command_inventory_matches_registration_and_help` sichert CLI-Surface gegen Drift ab
- `test_mcp_tool_inventory_matches_registered_tools` sichert MCP-Inventar gegen den real registrierten `FastMCP`-Stand ab
- Jeder neue CLI-Command MUSS vor dem Merge einen targeted Test haben (I-206)
- Jedes neue `@mcp.tool()` MUSS entweder in `read_tools` oder explizit als deprecated klassifiziert sein (I-207)

**What is explicitly excluded:**

- No auto-routing, no auto-promotion, no auto-remediation
- No trading execution
- No DB mutation from read-only CLI/MCP commands
- No destructive side effects from coverage tests

---

## §44 Sprint 32 — MCP Contract Lock & Coverage Completion (Canonical)

**Ziel**: Den MCP-Surface vollständig klassifizieren, Coverage auf 100% bringen und Drift-Prävention durch maschinenlesbare Klassifikation dauerhaft absichern. Keine neuen Business-Features.

**Invarianten: I-211–I-220**

### MCP Tool Classification Schema

Jedes registrierte `@mcp.tool()` ist genau einer der folgenden Klassen zugeordnet:

| Klasse | Bedeutung |
|---|---|
| `canonical` | Primäre, autorisierte Surface-Funktion |
| `active_alias` | Backward-kompatibler Alias mit stabilem Verhalten; erscheint in `read_tools` |
| `superseded` | Durch kanonische Funktion ersetzt; NICHT in `read_tools`; bleibt registriert für Kompatibilität |
| `workflow_helper` | Meta-Funktion (get_mcp_capabilities); erscheint in `workflow_helpers`, nicht in `read_tools` |

Tool-Mode Klassen:

| Mode | Bedeutung |
|---|---|
| `read_only` | Kein Schreiben, keine Routing-Änderung, kein Auto-anything |
| `guarded_write` | Workspace-confined, Write-Audit JSONL zwingend (I-94/I-95) |
| `workflow_helper` | Gibt nur Capabilities zurück |

### Kanonische MCP Tool Inventory (38 registrierte Tools)

| tool_name | classification | mode | subsystem |
|---|---|---|---|
| `get_watchlists` | canonical | read_only | signals/watchlists |
| `get_research_brief` | canonical | read_only | signals/watchlists |
| `get_signal_candidates` | canonical | read_only | signals |
| `get_narrative_clusters` | canonical | read_only | signals/narratives |
| `get_signals_for_execution` | canonical | read_only | signals/handoff |
| `get_distribution_classification_report` | canonical | read_only | route_profiles |
| `get_route_profile_report` | canonical | read_only | route_profiles |
| `get_inference_route_profile` | canonical | read_only | route_profiles |
| `get_active_route_status` | canonical | read_only | route_profiles |
| `get_upgrade_cycle_status` | canonical | read_only | upgrade_cycle |
| `create_inference_profile` | canonical | guarded_write | route_profiles |
| `activate_route_profile` | canonical | guarded_write | route_profiles |
| `deactivate_route_profile` | canonical | guarded_write | route_profiles |
| `acknowledge_signal_handoff` | canonical | guarded_write | signals/handoff |
| `get_handoff_collector_summary` | canonical | read_only | handoff |
| `get_handoff_summary` | active_alias → `get_handoff_collector_summary` | read_only | handoff |
| `get_operational_readiness_summary` | canonical | read_only | readiness |
| `get_mcp_capabilities` | workflow_helper | workflow_helper | meta |
| `get_provider_health` | canonical | read_only | readiness |
| `get_distribution_drift` | canonical | read_only | readiness |
| `get_protective_gate_summary` | canonical | read_only | readiness/gates |
| `get_remediation_recommendations` | canonical | read_only | readiness/gates |
| `get_artifact_inventory` | canonical | read_only | artifacts |
| `get_artifact_retention_report` | canonical | read_only | artifacts |
| `get_cleanup_eligibility_summary` | canonical | read_only | artifacts |
| `get_protected_artifact_summary` | canonical | read_only | artifacts |
| `get_review_required_summary` | canonical | read_only | artifacts |
| `get_operational_escalation_summary` | superseded → `get_escalation_summary` | read_only | escalation |
| `get_escalation_summary` | canonical | read_only | escalation |
| `get_blocking_summary` | canonical | read_only | escalation |
| `get_operator_action_summary` | canonical | read_only | escalation |
| `get_action_queue_summary` | canonical | read_only | action_queue |
| `get_blocking_actions` | canonical | read_only | action_queue |
| `get_prioritized_actions` | canonical | read_only | action_queue |
| `get_review_required_actions` | canonical | read_only | action_queue |
| `get_decision_pack_summary` | canonical | read_only | decision_pack |
| `get_operator_decision_pack` | active_alias → `get_decision_pack_summary` | read_only | decision_pack |
| `get_operator_runbook` | canonical | read_only | runbook |

**Zusammenfassung:**
- Canonical: 34
- Active alias (in read_tools): 2 (`get_handoff_summary`, `get_operator_decision_pack`)
- Superseded (NOT in read_tools): 1 (`get_operational_escalation_summary`)
- Workflow helper: 1 (`get_mcp_capabilities`)
- **Total: 38 registered `@mcp.tool()`**

**read_tools Zählung:** 32 (34 canonical − 4 guarded_write − 1 workflow_helper + 2 active_alias + 1 superseded_not_in_read = 32 canonical_read + 2 alias = 32 total in list)

### Coverage Completion nach Sprint 32

| Tool | Status |
|---|---|
| `get_narrative_clusters` | ✅ targeted test (Sprint 32) |
| `get_operational_escalation_summary` | ✅ targeted test (Sprint 32) |
| Alle übrigen 36 Tools | ✅ bereits getestet (Sprint 1–31) |

### Safety Guardrails (unveränderlich)

- Keine Auto-Routing, keine Auto-Promotion, keine Auto-Remediation
- Kein direkter Trading-Execution-Hook
- Guarded-write tools: write-confined zu `workspace/artifacts/`, Write-Audit JSONL
- Superseded tools bleiben registriert (Kompatibilität), aber NICHT in `read_tools`
- `get_mcp_capabilities()` bleibt die autoritative, maschinenlesbare Surface-Beschreibung (I-217)

**What is explicitly excluded:**

- No new business logic, no new monitoring architecture
- No trading execution, no DB mutation from read-only tools
- No auto-deletion, no auto-remediation, no auto-routing

---

## §45 Sprint 33 — Append-Only Operator Review Journal & Resolution Tracking (Canonical)

**Status: ✅ canonical append-only operator review surface on top of the existing runbook / decision-pack / governance stack**

Sprint 33 adds a minimal operator review journal that documents human review and
resolution state without mutating any KAI core models. The journal is an audit
surface only. It does NOT introduce a second governance, action-queue, or
decision stack.

**Invarianten: I-221–I-230**

- I-221: `ReviewJournalEntry` MUST be immutable and append-only once written.
- I-222: Persistence MUST append JSONL rows only. Existing rows are never edited or deleted.
- I-223: Journal entries MUST reference existing operator-facing artifacts or steps via `source_ref`; they do not create new control state.
- I-224: Valid `review_action` values are strictly `note`, `defer`, `resolve`.
- I-225: Journal and resolution summaries MUST always be non-executing: `execution_enabled=False`.
- I-226: Journal writes MUST NOT mutate KAI core DB state, route state, gate state, decision-pack state, or action-queue state.
- I-227: `ReviewJournalSummary` and `ReviewResolutionSummary` are derived read-only projections only.
- I-228: `operator_review_journal.jsonl` is a protected audit-trail artifact and MUST NOT be auto-rotated or deleted.
- I-229: `review_id` MUST be deterministic from normalized entry content.
- I-230: No journal surface may trigger trading execution, auto-routing, auto-promotion, or auto-remediation.

### Canonical Models and Functions

Implementation lives in `app/research/operational_readiness.py`.

- `ReviewJournalEntry`
- `ReviewJournalSummary`
- `ReviewResolutionSummary`
- `create_review_journal_entry(...)`
- `append_review_journal_entry_jsonl(entry, path)`
- `load_review_journal_entries(path)`
- `build_review_journal_summary(entries, journal_path=...)`
- `build_review_resolution_summary(summary)`

### Canonical Payload Fields

`ReviewJournalEntry`:
- `review_id`
- `source_ref`
- `operator_id`
- `review_action`
- `review_note`
- `evidence_refs`
- `created_at`
- `journal_status`

`ReviewJournalSummary`:
- `journal_status`
- `total_count`
- `source_ref_count`
- `open_count`
- `resolved_count`
- `latest_created_at`
- `entries`
- `latest_entries`

`ReviewResolutionSummary`:
- `journal_status`
- `total_count`
- `source_ref_count`
- `open_count`
- `resolved_count`
- `open_source_refs`
- `resolved_source_refs`

### MCP Surface

The canonical MCP inventory extends Sprint 32 by three tools and now totals 41 registered `@mcp.tool()` surfaces.

| Tool | Mode | Zweck |
|---|---|---|
| `append_review_journal_entry(source_ref, operator_id, review_action, review_note, evidence_refs=None, journal_output_path="artifacts/operator_review_journal.jsonl")` | guarded_write | Append-only audit write inside `workspace/artifacts/` |
| `get_review_journal_summary(journal_path="artifacts/operator_review_journal.jsonl")` | read_only | Read-only journal overview |
| `get_resolution_summary(journal_path="artifacts/operator_review_journal.jsonl")` | read_only | Latest per-source resolution state |

Rules:
- MCP write path is confined to `workspace/artifacts/` and audited via `mcp_write_audit.jsonl`
- `append_review_journal_entry` is audit-only and returns `core_state_unchanged=True`
- Neither read surface may trigger any write-back

### CLI Surface

Implementation lives in `app/cli/main.py`.

| Command | Zweck |
|---|---|
| `research review-journal-append <source_ref> --operator-id ... --review-action ... --review-note ... [--evidence-ref ...] [--journal-path ...]` | Append-only operator review entry |
| `research review-journal-summary [--journal-path ...]` | Read-only journal summary |
| `research resolution-summary [--journal-path ...]` | Read-only latest resolution state |

### Artifact Lifecycle Integration

Implementation lives in `app/research/artifact_lifecycle.py`.

- `operator_review_journal.jsonl` is classified as `audit_trail`
- retention class is always `protected`
- the journal is never delete-eligible
- the journal is never a rotation candidate

**What is explicitly excluded:**

- No second governance architecture
- No second action queue
- No route, gate, or decision-pack mutation
- No trading execution
- No auto-remediation or auto-routing

## §46 Sprint 35 — KAI Backtest Engine: Signal→Risk→Paper Loop (Canonical)

**Status: ✅ canonical paper-only backtest surface — Signal→RiskEngine→PaperExecution loop**

Sprint 35 closes the core KAI execution loop: SignalCandidates from the research
surface are routed through all RiskEngine gates and, if approved, executed in
PaperExecutionEngine. The backtest is simulation-only, audit-safe, and kill-switch-aware.

**Invarianten: I-231–I-240**

- I-231: BacktestEngine MUST use `PaperExecutionEngine(live_enabled=False)`. No live path.
- I-232: Every signal MUST pass through all RiskEngine gates. No gate bypass permitted.
- I-233: `BacktestResult` MUST be immutable (frozen dataclass).
- I-234: Market data MUST be provided via `dict[str, float]` — no hidden data fetches inside run().
- I-235: Signal→Order mapping MUST be deterministic given identical inputs.
- I-236: `direction_hint=="neutral"` MUST be skipped. `direction_hint=="bearish"` MUST be skipped when `long_only=True` (A-012).
- I-237: A triggered kill switch MUST halt all further fill attempts for remaining signals.
- I-238: `BacktestResult.kill_switch_triggered` MUST accurately reflect kill switch state.
- I-239: `BacktestResult.to_json_dict()` MUST NOT expose internal paths, live flags, or sensitive data.
- I-240: Every `BacktestEngine.run()` call MUST write one append-only row to `artifacts/backtest_audit.jsonl`.

### Canonical Models

Implementation lives in `app/execution/backtest_engine.py`.

- `BacktestConfig` (frozen): initial_equity, fee_pct, slippage_pct, stop_loss_pct,
  take_profit_multiplier, min_signal_confidence, max_open_positions, long_only, ...
- `SignalExecutionRecord` (frozen): per-signal disposition record (outcome, violations, fill_price, ...)
- `BacktestResult` (frozen): aggregate result with all metrics and execution records

### Canonical Outcome Values

| outcome | Meaning |
|---|---|
| `filled` | Signal passed all gates and was executed as a paper fill |
| `risk_rejected` | Signal failed one or more RiskEngine gates |
| `skipped_neutral` | direction_hint=="neutral" — always skipped (I-236) |
| `skipped_bearish` | direction_hint=="bearish" with long_only=True — skipped (I-236, A-012) |
| `no_price` | No price found for target_asset in prices dict |
| `no_quantity` | Position size calculated as zero or fill rejected by paper engine |
| `kill_switch_halted` | Kill switch was active before this signal was processed (I-237) |

### CLI Surface

| Command | Zweck |
|---|---|
| `research backtest-run [--signals-path ...] [--out ...] [--initial-equity ...] [--stop-loss-pct ...] [--min-confidence ...] [--audit-path ...]` | Paper backtest from signal JSONL |

### Assumptions

- A-012: long_only=True by default — bearish signals skipped
- A-013: max_leverage=1.0 always in BacktestEngine
- A-014: SL/TP derived mechanically from config (not from signal risk notes)
- A-015: signal_confluence_count=1 per signal in backtest

**What is explicitly excluded:**

- No live execution path
- No gate bypass under any condition
- No trading PnL guarantee or performance claim
- No short-selling without explicit long_only=False
- No external market data fetch inside BacktestEngine.run()


## §47 Sprint 36 — Decision Journal & TradingLoop CLI/MCP Surface (Canonical)

### Purpose

Expose the fully-implemented `DecisionRecord` journal and `TradingLoop` audit trail
through typed CLI commands and MCP tools. Both surfaces are read-only or append-only
(no mutation, no live execution). The journal compatibility layer MUST project onto
the canonical `DecisionRecord` runtime contract and bind to `DECISION_SCHEMA.json`
fail-closed.

### New CLI Commands

| Command | Zweck |
|---|---|
| `research decision-journal-append <symbol> --thesis <text> [--mode ...] [--confidence ...] [--journal-path ...]` | Append a validated canonical `DecisionRecord` to the append-only decision journal |
| `research decision-journal-summary [--journal-path ...]` | Read-only summary of the decision journal (totals, by_mode, by_approval, avg_confidence) |
| `research loop-cycle-summary [--audit-path ...] [--last-n ...]` | Read-only table of recent TradingLoop cycle records from the JSONL audit log |

### New MCP Tools

| Tool | Class | Zweck |
|---|---|---|
| `get_decision_journal_summary` | canonical_read | Read-only summary of the append-only decision journal |
| `get_loop_cycle_summary` | canonical_read | Read-only summary of recent TradingLoop JSONL audit cycles |
| `append_decision_instance` | guarded_write | Append one validated canonical `DecisionRecord` to the journal (audit-only, no trade triggered) |

**Total MCP surface after Sprint 36: 36 canonical_read + 6 guarded_write + 1 workflow_helper + 2 aliases + 1 superseded = 46 tracked tools.**

### Security Invariants

- `execution_enabled=False` and `write_back_allowed=False` on all responses.
- `append_decision_instance` is workspace-confined and artifacts/-restricted (I-95 family).
- No decision record can trigger a trade. Recording is not executing.
- Legacy journal rows MAY be normalized on load, but the stored runtime backbone is always `DecisionRecord`.
- Malformed or schema-invalid journal rows MUST fail closed; silent skips are forbidden.
- `get_loop_cycle_summary` is strictly read-only — no state change.
- All new MCP tools appear in `get_mcp_tool_inventory()` with correct classification.
- `test_mcp_tool_inventory_matches_registered_tools` enforces registered == classified.

### Assumptions Referenced

- A-014: Evidence Before Action — decision records are advisory only.
- A-019: Decision Records Are Immutable, Append-Only, and Live-Incompatible by default.
- A-020: Next phase defaults to strictest runtime decision contract.

**What is explicitly excluded:**

- No live trading path
- No decision-to-order bridge
- No automatic approval state changes
- No loop cycle replay or re-execution

---

## §48 Sprint 37 — Runtime Schema Binding & Decision Backbone Convergence

**Sprint**: 37 | **Datum**: 2026-03-21 | **Status**: Kanonisch

### Konvergenz-Entscheidung

`DecisionInstance` ist jetzt ein `TypeAlias` für `DecisionRecord`.
`DecisionRecord` (in `app/execution/models.py`) ist das einzige kanonische Datenmodell.
Die `journal.py`-API bleibt für CLI/MCP-Kompatibilität, delegiert aber vollständig auf `DecisionRecord`.

### Zwei-Schichten-Architektur (kanonisch)

| Schicht | Modul | Zweck |
|---|---|---|
| **Schema-Integrität** | `app/core/schema_binding.py` | Prüft, ob die Schema-DATEI selbst korrekt ist (Struktur, Safety-Consts, Feld-Alignment). Boot-time check. Raises nie — gibt `SchemaValidationResult` zurück. |
| **Payload-Validierung** | `app/schemas/runtime_validator.py` | Prüft, ob ein DATA-Payload das Schema einhält. Runtime check. Raises `SchemaValidationError` (fail-closed). |

Diese zwei Schichten sind komplementär, nicht konkurrierend.
`app/core/settings.py::validate_json_schema_payload()` ist eine Kompatibilitäts-Wrapper-Funktion, die an `runtime_validator.py` delegiert.

### Runtime Schema Binding

| Schema | Kanonischer Validator | Wann aufgerufen |
|---|---|---|
| `DECISION_SCHEMA.json` | `app/schemas/runtime_validator.py::validate_json_schema_payload()` | `DecisionRecord._validate_safe_state()` — bei jeder Instanziierung |
| `CONFIG_SCHEMA.json` | `app/schemas/runtime_validator.py::validate_runtime_config_payload()` | `AppSettings.validate_runtime_contract()` — beim Settings-Startup |

### Public API — Payload-Validierung (`app/schemas/runtime_validator.py`)

| Funktion / Typ | Zweck |
|---|---|
| `validate_json_schema_payload(payload, *, schema_filename, label)` | Generische Payload-Validierung gegen beliebige bundled JSON Schema — raises `SchemaValidationError` |
| `validate_runtime_config_payload(payload)` | Config payload gegen CONFIG_SCHEMA.json — raises `SchemaValidationError` |
| `validate_decision_schema_payload(payload)` | Decision payload gegen DECISION_SCHEMA.json — raises `SchemaValidationError` |
| `validate_config_payload(payload)` | Alias für `validate_runtime_config_payload()` |
| `validate_decision_payload(payload)` | Alias für `validate_decision_schema_payload()` |
| `load_schema_document(schema_filename)` | Schema-Datei laden (lru_cache) — raises `SchemaValidationError` bei Fehler |
| `SchemaValidationError` | Subclass von `ValueError` — fail-closed Fehlertyp |

### Public API — Schema-Integrität (`app/core/schema_binding.py`)

| Funktion / Typ | Zweck |
|---|---|
| `validate_config_schema(schema_path)` | Prüft CONFIG_SCHEMA.json: Struktur + Safety-Consts |
| `validate_decision_schema(schema_path)` | Prüft DECISION_SCHEMA.json: 26+ Pflichtfelder + Mode-Enum |
| `validate_decision_schema_alignment(schema_path)` | Prüft Feld-Deckung: Schema-Required ⊆ DecisionRecord.model_fields |
| `run_all_schema_validations(...)` | Führt alle drei Checks aus — gibt Liste von `SchemaValidationResult` zurück |
| `SchemaValidationResult` | Frozen dataclass: `valid`, `required_fields`, `errors`, `safety_const_checks` |

### Safety-Const-Checks in CONFIG_SCHEMA.json (10 Pflicht-Consts)

| Feld | Erwarteter Const-Wert |
|---|---|
| `risk.require_stop_loss` | `true` |
| `risk.allow_averaging_down` | `false` |
| `risk.allow_martingale` | `false` |
| `risk.allow_unbounded_loss` | `false` |
| `risk.kill_switch_enabled` | `true` |
| `execution.live_execution_enabled` | `false` |
| `execution.approval_required_for_live_actions` | `true` |
| `security.audit_log_immutable` | `true` |
| `messaging_ux.voice_interface_enabled` | `false` |
| `messaging_ux.avatar_interface_enabled` | `false` |

### Enum-Konvergenz (Sprint 37 Breaking Change)

| Legacy-Wert | Kanonischer Wert | Kontext |
|---|---|---|
| `auto_approved_paper` | `not_required` | `approval_state` — gelöscht aus VALID_APPROVAL_STATES |
| `submitted` | `queued` | `execution_state` — Legacy-Mapping beim Laden |
| `filled` | `executed` | `execution_state` — Legacy-Mapping beim Laden |
| `partial` | `blocked` | `execution_state` — Legacy-Mapping beim Laden |
| `cancelled` | `failed` | `execution_state` — Legacy-Mapping beim Laden |
| `error` | `failed` | `execution_state` — Legacy-Mapping beim Laden |

### DECISION_SCHEMA.json: report_type-Regel

`report_type` ist in `properties` als optionales String-Feld definiert (nicht in `required`).
Grund: Legacy-Journal-Rows können `report_type: "decision_instance"` enthalten.
`_normalize_legacy_decision_payload()` strippt `report_type` vor der Validierung.
`DecisionRecord.to_json_dict()` (`model_dump(mode="json")`) emittiert kein `report_type`.

### Security Invariants

- `app/schemas/runtime_validator.py` ist die einzige kanonische Implementierung des Validators.
- `app/core/settings.py::validate_json_schema_payload()` ist ein Kompatibilitäts-Wrapper — kein zweiter Validator.
- `DecisionRecord._validate_safe_state()` ruft den Validator über `settings.py` → `runtime_validator.py` auf.
- `AppSettings.validate_runtime_contract()` ruft `validate_runtime_config_payload()` direkt aus `runtime_validator.py` auf.
- `SchemaValidationError` ist Subclass von `ValueError` — alle bestehenden `except ValueError`-Handler greifen.
- Legacy-Rows werden beim Laden normalisiert, nicht beim Schreiben.
- Neue Rows werden immer im kanonischen Format gespeichert.
- Safety-Consts in CONFIG_SCHEMA.json: 10 Felder mit `const`-Constraints; `validate_config_schema()` verifiziert alle.

### Tests

- `tests/unit/test_schema_binding.py` — 14 Tests (Schema-Integrität, Safety-Consts, Alignment, Immutability)
- `tests/unit/test_schema_runtime_binding.py` — 25 Tests (Payload-Validierung, invalid enums, missing fields)
- `tests/unit/test_decision_journal.py` — 20 Tests (Konvergenz, Legacy-Normalisierung, Summary)
- `tests/unit/test_decision_record.py` — 9 Tests (Runtime-Schema-Binding, Safe-State-Validator)

---

## §49 Sprint 38+38C — Telegram Command Hardening & Canonical Read Surfaces

**Sprint**: 38+38C | **Datum**: 2026-03-21 | **Status**: Kanonisch und abgeschlossen

### Leitprinzip

Telegram ist First-Class-Operator-Surface, niemals Execution-Surface.
Alle Telegram-Kommandos sind auf kanonische MCP-Read-Surfaces oder append-only Audit-Pfade gebunden.
Keine neuen Live-, Routing-, Promotion- oder Trading-Funktionen wurden eroeffnet.

### Kanonische Telegram-Command-Surface (final)

| command | surface_class | source_of_truth | cli_ref | forbidden_side_effects |
|---|---|---|---|---|
| `/status` | read_only | `get_operational_readiness_summary()` (MCP) | `research readiness-summary` | none |
| `/health` | read_only | `get_provider_health()` (MCP) | `research provider-health` | none |
| `/positions` | read_only | `get_handoff_collector_summary()` (MCP, provisional proxy) | `research handoff-collector-summary` | none; kein Live-Positions-Pfad |
| `/exposure` | read_only | static stub | — | none |
| `/risk` | read_only | `get_protective_gate_summary()` (MCP) | `research gate-summary` | none |
| `/signals` | read_only | `get_signals_for_execution(limit=5)` (MCP) | `research signal-handoff` | kein Routing, keine Execution, kein Promote |
| `/journal` | read_only | `get_review_journal_summary()` (MCP) | `research review-journal-summary` | none |
| `/daily_summary` | read_only | `get_decision_pack_summary()` (MCP) | `research decision-pack-summary` | none |
| `/approve <dec_ref>` | guarded_audit | audit-only: `artifacts/operator_commands.jsonl` | `research review-journal-append` | kein Live-Execution, kein Routing, kein State-Change |
| `/reject <dec_ref>` | guarded_audit | audit-only: `artifacts/operator_commands.jsonl` | `research review-journal-append` | kein Live-Execution, kein Routing, kein State-Change |
| `/pause` | guarded_write | `RiskEngine.pause()` — dry_run gated | — | kein Trading-Trigger |
| `/resume` | guarded_write | `RiskEngine.resume()` — dry_run gated | — | kein Trading-Trigger |
| `/kill` | guarded_write | `RiskEngine.trigger_kill_switch()` — 2-Step + dry_run gated | — | Notfall-Only |
| `/incident <note>` | guarded_audit | `get_escalation_summary()` (MCP) + audit-append | `research escalation-summary` | keine State-Mutation, kein Auto-Remediation |
| `/help` | read_only | static | — | none |

### Surface-Klassen (kanonisch)

| Klasse | Bedeutung |
|---|---|
| `read_only` | Kein Schreiben, kein State-Wechsel; via MCP canonical read tools |
| `guarded_audit` | Schreibt nur append-only Audit-Log — kein Execution-Seiteneffekt |
| `guarded_write` | Mutiert Risk-Engine-State — explizit dry_run gated |

**Hinweis zu `/incident`**: `guarded_audit` — liest zusaetzlich `get_escalation_summary()` (MCP) fuer Kontext.
Audit-Eintrag wird **immer** per `_audit()` vor dem Handler geschrieben — MCP-Fehler wird fail-closed abgefangen.

### Kanonische Inventory-Funktion

`get_telegram_command_inventory()` in `app/messaging/telegram_bot.py` ist die maschinenlesbare Vertragsdefinition.
Sie liefert `read_only_commands`, `guarded_audit_commands`, `canonical_research_refs`.
`test_telegram_command_inventory_references_registered_cli_research_commands` MUSS gruen sein.

### Klassifikations-Invarianten (Sprint 38C)

- `_READ_ONLY_COMMANDS` = `{status, health, positions, risk, signals, journal, daily_summary}` — 7 Eintraege
- `_GUARDED_AUDIT_COMMANDS` = `{approve, reject, incident}` — 3 Eintraege
- `incident` ist NICHT in `_READ_ONLY_COMMANDS` — Klassifikationskonflikt Sprint 38 bereinigt (Sprint 38C)
- Disjunkte Sets: kein Command darf in beiden Sets erscheinen
- `exposure` und `help` sind static stubs — kein Canonical-Ref, kein Set-Eintrag notwendig

### decision_ref Format

`/approve` und `/reject` akzeptieren nur: `dec_` + 12 Hex-Zeichen (`^dec_[0-9a-f]{12}$`).
Ungueltige Refs: fail-closed Fehlermeldung. Implementierung: `_DECISION_REF_PATTERN` + `_validate_decision_ref()`.

### Telegram Safety Boundary (nicht verhandelbar)

- Telegram = Operator-Surface, NICHT Execution-Surface
- `/approve` und `/reject` = audit-only — kein Live-Execution-Pfad
- Kein Trading ueber Telegram
- Kein Auto-Routing ueber Telegram
- Kein Auto-Promote ueber Telegram
- Keine ungepruefte Telegram-Aktion mit Core-State-Wirkung
- Kein Auto-Remediation via `/incident`
- Alle read_only MCP-Antworten muessen `execution_enabled=False` und `write_back_allowed=False` enthalten

### Security Invariants

- I-266 bis I-277 in `docs/intelligence_architecture.md` (Sprint 38)
- Kanonische Command-Surface-Definition in `TELEGRAM_INTERFACE.md`
- Alle guarded_write Kommandos dry_run gated — default safe
- Alle Kommandos audit-geloggt vor Handler-Ausfuehrung
- Admin-Gating fail-closed — Unauthorized = logged + generic response

### Assumptions Referenced

- A-004: Telegram Bot Commands are Admin-Gated
- A-027 bis A-031 (Sprint 38) in `ASSUMPTIONS.md`

### Gelieferte Dateien (Sprint 38+38C)

- `app/messaging/telegram_bot.py` — `_READ_ONLY_COMMANDS`/`_GUARDED_AUDIT_COMMANDS`, alle MCP-Bindings, `_validate_decision_ref()`, `get_telegram_command_inventory()`
- `tests/unit/test_telegram_bot.py` — 28 Tests (admin gating, MCP surface bindings, fail-closed, guarded_write, approve/reject audit-only, inventory)
- `TELEGRAM_INTERFACE.md` — kanonischer Operator-Surface-Contract
- `docs/contracts.md §49` — final
- `docs/intelligence_architecture.md` I-266–I-277
- `ASSUMPTIONS.md` A-027–A-031

### Tests (Sprint 38+38C)

- `tests/unit/test_telegram_bot.py` — 28 Tests (alle gruen)
  - Admin gating (authorized vs. unauthorized)
  - Unknown command → fail-closed
  - `/kill` Zwei-Schritt-Confirm
  - dry_run: `/pause` → kein State-Wechsel
  - Audit-Log-Eintrag pro Command
  - Alle 8 read_only Commands → korrekter MCP-Loader aufgerufen
  - `/incident` → guarded_audit + MCP + Audit-Log
  - fail-closed bei MCP-Surface-Fehler
  - fail-closed bei ungueltigen CLI-Refs
  - `/approve` und `/reject` → audit-only, kein Execution-Seiteneffekt
  - Read-only commands mutieren keinen Runtime-State
  - `/help` listet alle 14 gehärteten Commands
  - `get_telegram_command_inventory()` → alle CLI-refs valid

---

## §50 — Market Data Layer: Read-Only Adapter Contract (Sprint 39)

### Zweck

Definiert den einzigen kanonischen read-only Market-Data-Contract, auf dem Signale, Backtests und Operator-Surfaces sicher aufbauen können. Kein Execution-Pfad, keine Routing-Entscheidung, keine Order-Submission darf aus diesem Layer entstehen.

---

### §50.1 — Kanonisches Datenmodell: `MarketDataPoint`

**Implementierung**: `app/market_data/models.py`

```python
@dataclass(frozen=True)
class MarketDataPoint:
    symbol: str               # Kanonisches Symbol (z.B. "BTC/USDT", "AAPL")
    timestamp_utc: datetime   # UTC-aware Zeitstempel des Datenpunkts
    price: float              # Aktueller Preis (letzter bekannter)
    volume_24h: float         # 24h-Handelsvolumen in Quote-Currency
    change_pct_24h: float     # 24h-Preisaenderung in Prozent
    source: str               # Provider-Identifier (z.B. "mock", "binance", "alpaca")
    is_stale: bool = False    # True wenn Datenpunkt ausserhalb der Freshness-Schwelle
    freshness_seconds: float = 0.0  # Alter des Datenpunkts in Sekunden seit Abruf
```

**Invarianten**:
- `frozen=True` — unveraenderlich nach Erstellung
- `timestamp_utc` MUSS UTC-aware sein — naive datetimes sind ungueltig
- `source` MUSS durch den Adapter gesetzt werden — niemals durch den Consumer inferiert
- `is_stale=True` signalisiert Degradation — Consumer MUSS fail-closed reagieren
- `freshness_seconds` ist informativ — Stale-Entscheidung liegt beim Adapter, nicht beim Consumer

---

### §50.2 — Unterstuetzende Datenmodelle

```python
@dataclass(frozen=True)
class Ticker:
    symbol: str
    timestamp_utc: datetime
    bid: float
    ask: float
    last: float
    volume_24h: float
    change_pct_24h: float

@dataclass(frozen=True)
class OHLCV:
    symbol: str
    timestamp_utc: datetime
    timeframe: str            # z.B. "1h", "1d"
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class OrderBook:
    symbol: str
    timestamp_utc: datetime
    bids: list[tuple[float, float]]   # [(price, qty), ...]
    asks: list[tuple[float, float]]
    spread_pct: float
```

Diese Modelle werden von spezialisierten Adapter-Methoden geliefert. `MarketDataPoint` ist der kanonische Einstiegspunkt fuer den TradingLoop.

---

### §50.3 — Adapter-Interface: `BaseMarketDataAdapter`

**Implementierung**: `app/market_data/base.py`

```python
class BaseMarketDataAdapter(ABC):
    @property
    @abstractmethod
    def adapter_name(self) -> str: ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker | None: ...

    @abstractmethod
    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]: ...

    @abstractmethod
    async def get_price(self, symbol: str) -> float | None: ...

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        # Default: abgeleitet von get_ticker()
        ...

    async def health_check(self) -> bool:
        # Default: BTC/USDT Ticker-Abruf als Liveness-Test
        ...
```

**Never-Raise-Contract**:
- Alle Methoden DUERFEN NIEMALS unkontrolliert eine Exception werfen
- Bei Transient-Fehlern: `None` zurueckgeben (nicht `raise`)
- Bei leeren OHLCV-Ergebnissen: `[]` zurueckgeben (nicht `raise`)
- Interner Fehler MUSS als `WARNING` geloggt werden vor `None`-Return
- `health_check()` gibt `False` zurueck bei Fehler — wirft nie

**Read-Only-Invariante** (nicht verhandelbar):
- Keine Adapter-Methode darf Orders senden, Positionen oeffnen, oder Execution-State mutieren
- Adapter sind passive Daten-Quellen — sie haben keine Schreibrechte auf Broker-Systeme
- Kein Adapter-Konstruktor darf Broker-Credentials fuer Schreibzugriff initialisieren

---

### §50.4 — Default-Adapter: `MockMarketDataAdapter`

**Implementierung**: `app/market_data/mock_adapter.py`

- **Deterministische sinusoidale Preise**: Kein `random()`, kein Zufall, kein externes Netzwerk
- **Hash-basierte Phase**: Jedes Symbol hat eine stabile, reproduzierbare Preisbewegung
- **Basis-Preise**: BTC/USDT (43000), ETH/USDT (2600), BNB/USDT (380), SOL/USDT (105), AAPL (185), MSFT (415), SPY (510)
- **24h-Periode**: Sinusoid mit konfigurierbarer Amplitude (`amplitude_pct`)
- **`adapter_name`**: `"mock"`
- **Verwendung**: Pflicht-Default fuer Paper-Trading und alle Unit-Tests ohne externe Abhaengigkeiten

**Invariante**: Tests, die spezifische Preise erwarten, MUESSEN `MockMarketDataAdapter` verwenden. Der Mock darf nicht durch echte Marktdaten ersetzt werden, ohne Tests zu aktualisieren.

---

### §50.5 — Freshness-Semantik

| Feld | Bedeutung | Wer setzt es |
|---|---|---|
| `is_stale` | `True` = Datenpunkt ausserhalb der konfigurierten Freshness-Schwelle | Adapter |
| `freshness_seconds` | Alter in Sekunden seit API-Abruf | Adapter |
| `timestamp_utc` | UTC-Zeitstempel des Datenpunkts (nicht des Abrufs) | Adapter |

**Consumer-Regeln**:
- `is_stale=True` → TradingLoop ueberspringt den Zyklus fuer dieses Symbol (`no_market_data:symbol`)
- `None`-Return → TradingLoop ueberspringt den Zyklus (identische Behandlung wie stale)
- Consumer DARF `is_stale` nicht ueberschreiben oder ignorieren
- Consumer DARF NICHT automatisch auf einen anderen Provider umschalten (kein Auto-Routing)

---

### §50.6 — Provenance-Semantik

- `MarketDataPoint.source` ist ein Provider-Identifier (z.B. `"mock"`, `"binance"`, `"alpaca"`)
- Der Adapter setzt `source` — der Consumer liest nur
- Signale, die aus einem `MarketDataPoint` abgeleitet werden, SOLLEN `source` im Signal-Kontext propagieren (Traceability)
- `source` ist KEIN Routing-Signal und KEIN Permission-Check — es ist ein Provenance-Tag

---

### §50.7 — Failure- und Degradations-Semantik

| Szenario | Adapter-Verhalten | Consumer-Verhalten |
|---|---|---|
| Transient-Netzwerkfehler | `None` zurueckgeben, intern loggen | Zyklus ueberspringen |
| Symbol unbekannt | `None` zurueckgeben | Zyklus ueberspringen |
| Datenpunkt veraltet | `MarketDataPoint(is_stale=True)` | Zyklus ueberspringen |
| Provider down | `health_check()` → `False` | Kein Auto-Routing |
| OHLCV leer | `[]` zurueckgeben | Keine Analyse, kein Signal |
| Exception intern | Fangen, loggen, `None`/`[]` | Zyklus ueberspringen |

**Fail-Closed-Invariante**: Fehlende oder veraltete Marktdaten fuehren NIEMALS zu einer Execution-Entscheidung. Ein Zyklus ohne valide Marktdaten ist ein uebersprungener Zyklus — kein Fehler, kein Alarm.

---

### §50.8 — TradingLoop-Integration

**Implementierung**: `app/orchestrator/trading_loop.py`

```python
# TradingLoop-Konstruktor nimmt adapter explizit entgegen:
def __init__(self, ..., market_data_adapter: BaseMarketDataAdapter): ...

# Pro Zyklus:
data = await self._market_data.get_market_data_point(symbol)
if data is None or data.is_stale:
    # Zyklus als no_market_data:symbol aufzeichnen
    return
```

- `TradingLoop` bekommt den Adapter per Dependency Injection
- Kein internes Adapter-Lookup, kein Auto-Routing zwischen Adaptern
- Provider-Wechsel erfordert explizite Konfigurationsaenderung + Neustart

---

### §50.9 — BacktestEngine-Integration

**Implementierung**: `app/execution/backtest_engine.py`

```python
# BacktestEngine erhaelt Preise als pre-fetched dict:
def run(self, signals: list[SignalCandidate], prices: dict[str, float]) -> BacktestResult: ...
```

- `BacktestEngine` hat keine interne Adapter-Abhaengigkeit (I-234)
- Preise werden ausserhalb des BacktestEngine vorgeladen und uebergeben
- Determinismus des Backtests ist garantiert: kein Adapter-Aufruf innerhalb `run()`
- `MockMarketDataAdapter` wird fuer Backtest-Testdaten empfohlen, ist aber nicht zwingend

---

### §50.10 — Adapter-Auswahl und Konfiguration

- Die Auswahl des Adapters ist **explizite Konfiguration** (Settings / Dependency Injection)
- Kein Auto-Routing zwischen Adaptern (keine Fallback-Kette)
- `MockMarketDataAdapter` ist der Default fuer alle Nicht-Live-Umgebungen (A-003 bestaetigt)
- Ein echter externer Adapter (z.B. Binance, Alpaca) MUSS `BaseMarketDataAdapter` vollstaendig implementieren
- Unvollstaendige Implementierungen MUESSEN `NotImplementedError` werfen — kein Silent-None

---

### §50.11 — Provider Health und Routing

- `health_check()` returning `False` bedeutet: Provider nicht erreichbar
- `health_check()` ist ein **Liveness-Signal** fuer Monitoring — kein Routing-Trigger
- `False` darf NICHT automatisch einen anderen Provider aktivieren
- `False` darf NICHT als "stop trading"-Signal interpretiert werden (das ist Aufgabe des RiskEngine Kill-Switch)
- Health-Check-Ergebnis KANN in Operator-Surface (`/health` → MCP `get_provider_health()`) surfaced werden

---

### §50.12 — Tests (Sprint 39 Ziele)

- `tests/unit/test_mock_adapter.py` — MockAdapter-Tests: Determinismus, None-Handling, health_check, MarketDataPoint-Felder
- `tests/unit/test_market_data_models.py` — Modell-Frozen-Tests, is_stale-Semantik, Timestamp-UTC-Validierung
- `tests/unit/test_base_adapter.py` — ABC-Konformitaet, health_check-Default-Verhalten
- Gesamtziel: >= 15 neue Tests im Market-Data-Layer

---

### Assumptions Referenced

- A-003: MockMarketDataAdapter ist Default fuer Paper-Trading
- A-032 bis A-036 (Sprint 39) in `ASSUMPTIONS.md`

### Intelligence Invariants

- I-281 bis I-290 in `docs/intelligence_architecture.md` (Sprint 39)

### Gelieferte Dateien (Sprint 39 — Definition)

- `docs/contracts.md §50` — kanonischer Market-Data-Layer-Contract (dieses Dokument)
- `docs/intelligence_architecture.md` I-281–I-290 — Market-Data-Invarianten
- `ASSUMPTIONS.md` A-032–A-036 — Market-Data-Annahmen
- `AGENTS.md` P45 — Sprint-39-Pattern
- `TASKLIST.md` Sprint-39-Block

### §50.13 - Sprint 39C Runtime Consolidation (implemented)

The canonical Sprint 39 runtime path is now implemented as a single read-only
adapter stack:

- External adapter: `app/market_data/coingecko_adapter.py` (`CoinGeckoAdapter`)
- Shared read service: `app/market_data/service.py`
- Canonical snapshot model: `MarketDataSnapshot` in `app/market_data/models.py`
- CLI read surfaces:
  - `research market-data-quote`
  - `research market-data-snapshot`
- MCP read surface:
  - `get_market_data_quote`

Security and scope boundaries:

- Read-only only (price/market data); no order/account/portfolio endpoints
- No trading execution path opened
- No routing/promotion/live feature extension
- Fail-closed responses on unsupported provider, timeout, missing price, and invalid payload
- Snapshot contract always carries:
  - `symbol`, `provider`, `retrieved_at`, `source_timestamp`, `price`
  - `is_stale`, `freshness_seconds`, `available`, `error`
  - `execution_enabled=False`, `write_back_allowed=False`

Sprint 39 tests (implemented):

- `tests/unit/test_market_data_coingecko.py`
- `tests/unit/test_cli_market_data.py`
- `tests/unit/test_mcp_market_data.py`
