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

### 13. Intelligence Layer Extension Contracts (Sprint 5 — planned)

Defines the exact extension points for introducing the companion model and `AnalysisSource` tracking.
These are **stubs** — not yet implemented. Code must not reference them before Sprint 5.

Full architecture reference: [docs/intelligence_architecture.md](./intelligence_architecture.md)

---

#### 13a. ProviderSettings Extension

```python
# app/core/settings.py — additions to ProviderSettings
companion_model_endpoint: str | None = None      # e.g. "http://localhost:8080/v1"
companion_model_name: str = "kai-analyst-v1"
companion_model_timeout: int = 10                # seconds
```

Security constraint: `companion_model_endpoint` MUST be `localhost` or an explicitly allowlisted
internal address. Validation must reject external URLs at settings load time (Sprint 5A).

---

#### 13b. Factory Extension

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

The companion provider slot in `app/analysis/providers/` is reserved and empty until Sprint 5A.
`APP_LLM_PROVIDER=internal` must not be set in production before Sprint 5A is complete.

---

#### 13c. AnalysisSource Enum

```python
# app/analysis/base/interfaces.py  (Sprint 5B addition)
class AnalysisSource(str, Enum):
    RULE = "rule"                  # Tier 1 — RuleAnalyzer
    INTERNAL = "internal"          # Tier 2 — InternalCompanionProvider
    EXTERNAL_LLM = "external_llm"  # Tier 3 — OpenAI / Anthropic / Gemini
```

`AnalysisResult` extension (Sprint 5B):
```python
analysis_source: AnalysisSource | None = None
```

DB migration required (Sprint 5B):
```sql
ALTER TABLE canonical_documents ADD COLUMN analysis_source VARCHAR(20);
```

Invariants:
- `analysis_source` is set at result creation time — immutable after `apply_to_document()`
- `analysis_source=RULE` documents NEVER serve as distillation teacher signal
- Distillation corpus selects only `analysis_source=EXTERNAL_LLM` documents

---

#### 13d. Companion Model Output Scope

The companion model produces a **subset** of `LLMAnalysisOutput` (same schema, subset of fields trained):

| Field | Trained? | Sprint 5 default if not trained |
|-------|----------|--------------------------------|
| `sentiment_label` | ✅ | — |
| `sentiment_score` | ✅ | — |
| `relevance_score` | ✅ | — |
| `impact_score` | ✅ (cap ≤ 0.8) | — |
| `tags` | ✅ | — |
| `actionable` | ✅ | — |
| `market_scope` | ✅ | — |
| `affected_assets` | ✅ | — |
| `explanation_short` | ✅ | — |
| `novelty_score` | ❌ | `0.5` |
| `spam_probability` | ❌ | `0.0` |
| `confidence_score` | ❌ | `0.7` |

Companion model `impact_score` cap: ≤ 0.8 (conservative, not overconfident — Invariant I-17).

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
| `"internal"` | Companion model (Sprint 5B+) | `"internal"`, `"companion"` | ⚠️ evaluation only |
| `"rule"` | Rule-based / fallback analysis | `None`, `"fallback"`, `"rule"` | ❌ no (I-19) |

**Derivation logic** (implemented in `_analysis_source()`, `app/research/datasets.py`):
```python
_RULE_BASED_PROVIDERS = {"fallback", "rule"}
_INTERNAL_PROVIDERS   = {"internal", "companion"}

if not provider or provider in _RULE_BASED_PROVIDERS:  → "rule"
elif provider in _INTERNAL_PROVIDERS:                   → "internal"
else:                                                   → "external_llm"
```

**Sprint 5B update**: When `AnalysisSource` enum and `doc.analysis_source` land on
`CanonicalDocument`, `_analysis_source()` will be updated to use it directly.
The exported values and their semantics remain identical.

---

#### 14e. Distillation Corpus Filtering

The distillation training pipeline MUST filter the exported JSONL by `analysis_source`:

```python
# Only use external LLM rows as teacher signal (Invariant I-19)
rows = [r for r in jsonl_rows if r["metadata"]["analysis_source"] == "external_llm"]
```

Documents with `analysis_source="rule"` or `"internal"` CAN be included in the full export
for auditing or evaluation purposes, but MUST NOT appear in the fine-tuning training corpus.

---

## Final Rule

These contracts define the system.

If they become inconsistent with the code, the system becomes unstable.

**Protect them. Update them. Never bypass them.**

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
