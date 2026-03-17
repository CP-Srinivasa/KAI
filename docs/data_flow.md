# Data Flow — End-to-End Contract

> **Verbindliches Referenzdokument** für alle Agenten (Claude Code, Codex, Antigravity).
> Vor jeder Interface-Änderung lesen. Kein Agent darf diesen Fluss ohne Spec brechen.

---

## Overview

```
Source (RSS / API / ...)
       │
       ▼
 [1] FETCH          BaseSourceAdapter.fetch() → FetchResult
       │
       ▼
 [2] NORMALIZE      CanonicalDocument (auto content_hash, word_count)
       │
       ▼
 [3] DEDUPLICATE    Deduplicator.filter_scored() → batch duplicate check
       │
       ▼
 [4] PERSIST        DocumentRepository.save() → canonical_documents table
       │
       ▼
 [5] ANALYZE        AnalysisPipeline.run() → PipelineResult
       │
       ▼
 [6] APPLY          PipelineResult.apply_to_document() → scores on CanonicalDocument
       │
       ▼
 [7] UPDATE         DocumentRepository.update_analysis() → DB scores + is_analyzed=True
       │
       ▼
 [8] ALERT/QUERY    QueryExecutor / is_alert_worthy() / priority_score threshold
```

---

## Stage 1 — Fetch

**Input**: `SourceMetadata` (source_id, source_type, url)
**Output**: `FetchResult`

```python
@dataclass
class FetchResult:
    source_id: str
    documents: list[CanonicalDocument]  # already normalized by the adapter
    fetched_at: datetime
    success: bool
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

**Contract rules**:
- Adapter is responsible for producing valid `CanonicalDocument` instances
- `success=False` + populated `error` on any adapter-level failure
- `documents` is empty on failure — never `None`
- Adapter must set: `url`, `title`, `source_id`, `source_name`, `source_type`, `document_type`
- `content_hash` is auto-computed by `CanonicalDocument.__init__` — never set manually

**Owner**: `app/ingestion/base/interfaces.py` + adapter implementations

---

## Stage 2 — Canonical Document

**Model**: `CanonicalDocument` (`app/core/domain/document.py`)

The central data unit. All pipeline stages read from and write to this model.

### Fields by lifecycle phase

| Field | Set by | Notes |
|---|---|---|
| `id` | auto (uuid4) | Primary key |
| `url` | adapter | Dedup key |
| `content_hash` | auto (model_validator) | SHA-256 of url\|title\|raw_text |
| `source_id`, `source_name`, `source_type` | adapter | Source registry |
| `document_type` | adapter | article / youtube_video / podcast_episode / ... |
| `title`, `raw_text`, `author`, `published_at` | adapter | Core content |
| `language`, `country`, `region` | adapter or enrichment | Optional |
| `is_duplicate` | Deduplicator | Set before persist |
| `is_analyzed` | `update_analysis()` | Set after analysis |
| `entity_mentions` | `apply_to_document()` | Structured entities |
| `tickers`, `crypto_assets`, `tags`, etc. | `apply_to_document()` | Flat lists |
| `sentiment_label`, `sentiment_score` | `apply_to_document()` | From LLM |
| `relevance_score` | `apply_to_document()` | Blended: LLM + keyword |
| `impact_score`, `novelty_score` | `apply_to_document()` | From LLM |
| `spam_probability` | `apply_to_document()` | From LLM |
| `credibility_score` | `apply_to_document()` | `1.0 - spam_probability` |
| `market_scope` | `apply_to_document()` | From LLM |
| `priority_score` | `apply_to_document()` | `compute_priority(analysis_result)` |

**RULE**: `CanonicalDocument` is Pydantic, mutable (no `frozen`).
Fields may only be mutated within the defined pipeline stages listed above.

---

## Stage 3 — Deduplication

**Input**: `list[CanonicalDocument]` from `FetchResult.documents`
**Output**: `list[tuple[CanonicalDocument, DuplicateScore]]`

```python
deduplicator = Deduplicator()
scored = deduplicator.filter_scored(documents)
new_docs = [doc for doc, score in scored if not score.is_duplicate]
```

- Batch-level dedup only (hash comparison within the fetch batch)
- DB-level dedup happens in `DocumentRepository.save()` (url + hash check)
- Sets `doc.is_duplicate = True` for intra-batch duplicates

**Owner**: `app/enrichment/deduplication/deduplicator.py`

---

## Stage 4 — Persist

**Input**: `CanonicalDocument` (new, not duplicate)
**Output**: stored row in `canonical_documents`

```python
async with session_factory.begin() as session:
    repo = DocumentRepository(session)
    saved = await repo.save(doc)  # no-op if url/hash already exists
```

`DocumentRepository.save()` contract:
- Returns existing document if `content_hash` already in DB (idempotent)
- Calls `session.flush()` — does NOT commit (caller controls transaction)
- Raises `StorageError` on DB failures

**Owner**: `app/storage/repositories/document_repo.py`

---

## Stage 5 — Analyze

**Input**: `CanonicalDocument` (stored, `is_analyzed=False`)
**Output**: `PipelineResult`

```python
pipeline = AnalysisPipeline(keyword_engine, provider, run_llm=True)
result = await pipeline.run(doc)
# or for batches:
results = await pipeline.run_batch(docs)  # max 5 concurrent LLM calls
```

`PipelineResult` fields:

```python
@dataclass
class PipelineResult:
    document: CanonicalDocument        # original document (not yet mutated)
    keyword_hits: list[KeywordHit]     # stage 1 output
    entity_mentions: list[EntityMention]  # stage 2 output
    llm_output: LLMAnalysisOutput | None  # stage 3 output (None if no provider)
    analysis_result: AnalysisResult | None  # assembled from llm_output
    error: str | None                  # non-None on LLM failure
    success: bool                      # property: error is None
```

`LLMAnalysisOutput` is a Pydantic model validated directly from the OpenAI structured output.
`AnalysisResult` wraps `LLMAnalysisOutput` with `document_id`, `provider`, `model`, `analyzed_at`.

**Owner**: `app/analysis/pipeline.py`

---

## Stage 6 — Apply

**Input**: `PipelineResult`
**Output**: `PipelineResult.document` mutated with analysis scores

```python
result.apply_to_document()
doc = result.document  # doc now has all scores + priority_score set
```

`apply_to_document()` does in order:
1. Sets `doc.entity_mentions` from `result.entity_mentions`
2. Syncs topic entities → `doc.topics`
3. If `analysis_result` exists: copies all scores from `AnalysisResult` to `doc`
4. Computes `relevance_score` = `calculate_final_relevance(llm_relevance, keyword_hits)`
5. Computes `doc.priority_score` = `compute_priority(analysis_result).priority`

**RULE**: This is the ONLY place that mutates analysis scores on `CanonicalDocument`.
The CLI and API must not manually copy fields from `llm_output` to `doc`.

**Owner**: `PipelineResult.apply_to_document()` in `app/analysis/pipeline.py`

---

## Stage 7 — Update Storage

**Input**: mutated `CanonicalDocument` (after `apply_to_document()`)
**Output**: updated row in `canonical_documents`

```python
await repo.update_analysis(doc)
```

Fields written by `update_analysis()`:

| Field | DB Column |
|---|---|
| `sentiment_label` | `sentiment_label` |
| `sentiment_score` | `sentiment_score` |
| `relevance_score` | `relevance_score` |
| `impact_score` | `impact_score` |
| `novelty_score` | `novelty_score` |
| `credibility_score` | `credibility_score` |
| `spam_probability` | `spam_probability` |
| `priority_score` | `priority_score` (indexed) |
| `market_scope` | `market_scope` |
| `entity_mentions` | `entity_mentions` (JSON) |
| `tickers`, `tags`, `categories`, etc. | JSON columns |
| `is_analyzed` | `true` (always) |

**Owner**: `app/storage/repositories/document_repo.py`

---

## Stage 8 — Alert / Query

```python
# Priority threshold check (Phase 4 Alerting)
from app.analysis.scoring import is_alert_worthy
if is_alert_worthy(result.analysis_result, min_priority=7):
    # → send alert

# In-memory query filter (QuerySpec DSL)
from app.analysis.query.executor import QueryExecutor
matches = QueryExecutor().execute(spec, documents)
```

Priority scale:
- **8–10**: High urgency — actionable, high impact
- **6–7**: Notable — relevant, alert-worthy
- **4–5**: Background — low urgency
- **1–3**: Spam / noise (spam cap applies)

---

## Key Invariants

| Rule | Where enforced |
|---|---|
| `content_hash` auto-computed, never set manually | `CanonicalDocument.model_validator` |
| `word_count` is never stored in DB | `@computed_field` property |
| `save()` is idempotent on hash collision | `DocumentRepository.save()` |
| `apply_to_document()` is the single mutation point for analysis scores | `PipelineResult` |
| `update_analysis()` always sets `is_analyzed=True` | `DocumentRepository` |
| LLM calls use structured outputs (`beta.chat.completions.parse`) | `OpenAIAnalysisProvider` |
| All LLM failures wrapped in `ProviderError` | `BaseAnalysisProvider` contract |
| No credentials in code | `.env` + `AppSettings` |

---

## Module Ownership

| Module | Owner Agent | Responsibility |
|---|---|---|
| `app/core/domain/` | **Claude Code** | Canonical models, enums, errors — no business logic |
| `app/ingestion/` | **Codex** | Adapters, classifiers, resolvers, schedulers |
| `app/enrichment/` | **Codex** | Deduplication, entity matching |
| `app/analysis/` | **Claude Code** | Pipeline, keyword engine, scoring, historical |
| `app/integrations/` | **Claude Code** | LLM providers (OpenAI, Anthropic) |
| `app/storage/` | **Claude Code** | ORM, repositories, migrations |
| `app/api/` | **Codex** | FastAPI routers, dependencies |
| `app/cli/` | **Antigravity** | CLI commands orchestration |
| `app/alerts/` | **Codex** | Telegram, email, alert rules (Phase 4) |
| `monitor/` | **User** | Watchlists, sources, keywords (runtime config) |

---

## AnalysisResult Schema

```python
class AnalysisResult(BaseModel):
    id: UUID
    document_id: UUID          # links to CanonicalDocument
    provider: str              # "openai" | "anthropic" | "rule"
    model: str | None          # e.g. "gpt-4o"
    analyzed_at: datetime

    # Scores (all validated ranges)
    sentiment_label: SentimentLabel
    sentiment_score: float     # -1.0 to 1.0
    relevance_score: float     # 0.0 to 1.0 — blended after apply_to_document()
    impact_score: float        # 0.0 to 1.0
    confidence_score: float    # 0.0 to 1.0
    novelty_score: float       # 0.0 to 1.0
    spam_probability: float    # 0.0 to 1.0

    market_scope: MarketScope
    affected_assets: list[str]
    affected_sectors: list[str]
    event_type: str | None

    # Reasoning
    short_reasoning: str | None
    bull_case: str | None
    bear_case: str | None
    historical_analogs: list[str]

    recommended_priority: int  # 1–10 (LLM suggestion)
    actionable: bool
    tags: list[str]

    raw_output: dict[str, Any]  # preserved for debugging
```

`AnalysisResult` is **in-memory only** — it is not persisted to its own table.
Denormalized scores are written back to `canonical_documents` via `update_analysis()`.

---

## FetchResult Schema

```python
@dataclass
class FetchResult:
    source_id: str
    documents: list[CanonicalDocument]  # pre-normalized by adapter
    fetched_at: datetime
    success: bool
    error: str | None = None           # always set if success=False
    metadata: dict[str, Any] = field(default_factory=dict)
```

Adapters that implement `BaseSourceAdapter` must:
1. Return `success=False, documents=[], error=<message>` on failure
2. Never raise — wrap exceptions internally
3. Each document must have `url` and `title` at minimum
4. `source_id` on each document must match `FetchResult.source_id`
