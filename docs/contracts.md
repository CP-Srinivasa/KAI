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

Migration note: `FetchResult.documents` currently holds `list[CanonicalDocument]`.
Target state: adapters produce `list[FetchItem]`, normalization runs in `persist_fetch_result()`.

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
    confidence_score: float             # [0.0, 1.0]

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
| `failed` | non-recoverable error — kept for audit | `repo.update_status(FAILED)` in pipeline error handler |
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
| Storage → Analysis | `repo.list(is_analyzed=False, is_duplicate=False)` feeds the analysis queue |
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
- Scoring is always downstream of LLM output — no standalone scoring without analysis context

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
