# Contracts and Core Data Models

## Current State (2026-03-24)

| Field | Value |
|---|---|
| current_phase | `PHASE 5 (active) — strategic hold on companion-ML infrastructure` |
| current_sprint | `PH5C_FILTER_BEFORE_LLM_BASELINE (closed D-97)` |
| next_required_step | `STRATEGIC_HOLD_GATE_REVIEW — wait for clearly positive alert-precision + paper-trading metrics` |
| baseline | `1449 passed, ruff clean, mypy 0 errors` |
| archive | `docs/contracts_archive.md` (closed §§38—§82) |

## Navigation

| Section | Content |
|---|---|
| [Core Contracts](#core-contracts) | §0-§15: Domain models, invariants, intelligence stack |
| [Immutable Invariants](#immutable-invariants) | Non-negotiable runtime rules |
| [Strategic Hold](#strategic-hold-d-97) | Companion-ML freeze policy and gate conditions |
| [Archive](contracts_archive.md) | Closed §§38—§82 (Phase 1—4) |

## Strategic Hold (D-97)

- No new companion-ML infrastructure sprint, decision, or invariant is opened while the strategic hold is active.
- Hold release gate is operator-driven and requires both:
  1. clearly positive alert-precision evidence
  2. clearly positive paper-trading metric evidence
- Documentation policy (D-99): no new standalone sprint-contract documents.
  Decisions are recorded only as short code comments or compact 3-line entries in `DECISION_LOG.md`.
- Until that gate is met, companion model infrastructure remains frozen and only governance/reporting updates are allowed.


---

## Purpose

This document defines the core shared contracts of the system.

These contracts are the foundation for:
- ingestion
- storage
- analysis
- scoring
- agent collaboration

No agent may modify these lightly.

Sprint 44 implementation note (2026-03-21):
- Operator API transport hardening is implemented in `app/api/routers/operator.py`.
- Canonical verification is covered by `tests/unit/test_api_operator.py` (20 tests).
- If any Sprint 44 section below still says "pending", treat code/tests above as source of truth.

---

## Core Contracts

### 0. FetchItem

The canonical raw-source type. Produced by adapters **before** normalization.

```python
@dataclass
class FetchItem:
    url: str                        # required Ã¢â‚¬” canonical item URL
    external_id: str | None = None  # source-assigned ID (RSS guid, API id, Ã¢â‚¬Â¦)
    title: str | None = None        # raw title from source
    content: str | None = None      # raw body text or excerpt
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)  # source extras
```

**Conversion**: `normalize_fetch_item(item, *, source_id, source_name, source_type) -> CanonicalDocument`

Rules:
- **No analysis** Ã¢â‚¬” no scores, no sentiment, no priority, no tickers, no entity mentions
- **No persistence state** Ã¢â‚¬” no `status`, `is_analyzed`, `is_duplicate`, `content_hash`, `id`
- **No source metadata** Ã¢â‚¬” `source_id`, `source_name`, `source_type` are injected by `normalize_fetch_item()`, never by the adapter
- As close to the source as possible Ã¢â‚¬” minimal transformation before `normalize_fetch_item()`
- `metadata` is a free-form bag for source-specific extras (image URL, author, feed tags, Ã¢â‚¬Â¦)

Implementation: Adapters create `FetchItem` internally, then call `normalize_fetch_item()` to
convert to `CanonicalDocument`. `FetchResult.documents` carries `list[CanonicalDocument]` by design.
Normalization is adapter-owned Ã¢â‚¬” it must NOT move into `persist_fetch_result()`, which is a
storage helper and must not contain source-type-specific transformation logic.

---

### 1. FetchResult

Represents raw ingestion output.

```python
@dataclass
class FetchResult:
    source_id: str
    documents: list[CanonicalDocument]  # never None Ã¢â‚¬” empty list on failure
    fetched_at: datetime
    success: bool
    error: str | None = None            # set when success=False
    metadata: dict[str, Any] = field(default_factory=dict)
```

Rules:
- adapter must never raise Ã¢â‚¬” catch all exceptions internally
- `success=False` + `error=<message>` on any failure
- `documents=[]` on failure (never None)
- every document must have: `url`, `title`, `source_id`, `source_name`, `source_type`
- `content_hash` must not be set by adapter Ã¢â‚¬” auto-computed by `CanonicalDocument`
- SSRF check (`validate_url()`) must run before any HTTP request

---

### 2. CanonicalDocument

The central data unit. Every document in the system is represented as a `CanonicalDocument`.

```python
class CanonicalDocument(BaseModel):
    id: UUID                            # primary key Ã¢â‚¬” never change after persist
    url: str                            # required Ã¢â‚¬” dedup key
    title: str                          # required
    raw_text: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime                # auto-set
    source_id: str | None = None
    source_name: str | None = None
    source_type: SourceType | None = None
    document_type: DocumentType         # ARTICLE / PODCAST_EPISODE / ...
    content_hash: str | None = None     # auto-computed Ã¢â‚¬” never set manually
    status: DocumentStatus              # lifecycle state Ã¢â‚¬” see below
    is_duplicate: bool                  # sync with status=DUPLICATE
    is_analyzed: bool                   # sync with status=ANALYZED
    # ... analysis scores, entity lists, metadata
```

Rules:
- `content_hash` is auto-computed from `url|title|raw_text` Ã¢â‚¬” never set manually
- `word_count` is a `@computed_field` Ã¢â‚¬” never stored in DB
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
    relevance_score: float              # [0.0, 1.0] Ã¢â‚¬” blended with keyword hits by apply_to_document()
    impact_score: float                 # [0.0, 1.0]
    novelty_score: float                # [0.0, 1.0]
    confidence_score: float             # [0.0, 1.0] Ã¢â‚¬” in-memory only, NOT persisted to DB
                                        # DB stores credibility_score = 1.0 - spam_probability

    market_scope: MarketScope | None
    affected_assets: list[str]
    affected_sectors: list[str]
    event_type: str | None

    explanation_short: str              # required Ã¢â‚¬” concise reasoning
    explanation_long: str               # required Ã¢â‚¬” full reasoning

    actionable: bool
    tags: list[str]
    spam_probability: float = 0.0       # stored for audit; ALWAYS pass separately to compute_priority()
    recommended_priority: int | None    # set by apply_to_document() after scoring
```

Rules:
- Must be fully populated Ã¢â‚¬” all score fields are required (no optional scores)
- Must be schema-validated Ã¢â‚¬” all ranges enforced by Pydantic
- Must not contain provider-specific fields (`provider`, `model`, `raw_output` removed)
- `AnalysisResult` is the provider-agnostic analysis contract for deterministic fallback,
  internal companion analysis, and external provider analysis
- `spam_probability` IS stored on `AnalysisResult` for audit Ã¢â‚¬” but scoring functions
  (`compute_priority`, `is_alert_worthy`) receive it as an **explicit separate parameter**
- `recommended_priority` is set by `apply_to_document()` after `compute_priority()` runs Ã¢â‚¬” not by the LLM
- `AnalysisResult` is in-memory only Ã¢â‚¬” no separate DB table
- scores are written back to `canonical_documents` via `repo.update_analysis(document_id, result)`

---

### 4. Document Lifecycle

```
pending Ã¢â€ ’ persisted Ã¢â€ ’ analyzed
         Ã¢â€ Ëœ failed
         Ã¢â€ Ëœ duplicate
```

| Status | Meaning | Owner |
|---|---|---|
| `pending` | in-memory only Ã¢â‚¬” not yet saved to DB | `prepare_ingested_document()` in `document_ingest.py` |
| `persisted` | saved to DB, awaiting analysis | `DocumentRepository.save_document()` |
| `analyzed` | scores written, pipeline complete | `DocumentRepository.update_analysis()` |
| `failed` | non-recoverable error Ã¢â‚¬” kept for audit | `repo.update_status(FAILED)` Ã¢â‚¬” ingest, `run_rss_pipeline()`, and `analyze_pending` CLI error handlers |
| `duplicate` | blocked at dedup gate Ã¢â‚¬” NOT saved | detected in-memory; `repo.mark_duplicate()` for retroactive marking |

Important: `DUPLICATE` and `FAILED` at the ingest stage are **in-memory states**.
Documents detected as duplicates by `persist_fetch_result()` are silently dropped (never saved to DB).
`status=DUPLICATE` is only written to DB when `repo.mark_duplicate()` is called explicitly
on an already-persisted document.

Rules:
- transitions are one-way Ã¢â‚¬” no rollback, no recycling
- `is_analyzed=True` must always be set together with `status=analyzed`
- `is_duplicate=True` must always be set together with `status=duplicate` (only when persisted)
- a document's status is always `pending` before any DB operation

---

### 5. Layer Boundaries

Every layer has a defined input and output. No layer may bypass another.

| Boundary | Rule |
|---|---|
| Ingestion Ã¢â€ ’ Storage | adapter returns `FetchResult`; only `persist_fetch_result()` persists |
| Storage Ã¢â€ ’ Analysis | `repo.get_pending_documents()` feeds the analysis queue Ã¢â‚¬” filters `status=PERSISTED` (not just flags) |
| Analysis Ã¢â€ ’ Storage | `apply_to_document()` then `repo.update_analysis()` Ã¢â‚¬” no other path |
| Analysis Ã¢â€ ’ Alerting | `is_alert_worthy()` is the only gate Ã¢â‚¬” no direct score access |
| LLM calls | always via `BaseAnalysisProvider.analyze()` Ã¢â‚¬” never direct SDK calls |
| Config | always via `AppSettings` Ã¢â‚¬” never `os.environ` directly |

---

### 6. Priority Score

```
raw = (relevance Ãƒâ€” 0.30) + (impact Ãƒâ€” 0.30) + (novelty Ãƒâ€” 0.20)
    + (actionable Ãƒâ€” 0.15) + ((1 - spam) Ãƒâ€” 0.05)

priority = round(raw Ãƒâ€” 9) + 1          # maps [0.0, 1.0] Ã¢â€ ’ [1, 10]

# Actionability bonus: +1 if result.actionable is True (and priority < 10)
if actionable:
    priority = min(10, priority + 1)
```

Cap: if `spam_probability > 0.7` Ã¢â€ ’ `priority = min(priority, 3)` (applied after bonus)

Scale:
- 8Ã¢â‚¬“10: high urgency, actionable
- 6Ã¢â‚¬“7: notable, alert-worthy
- 4Ã¢â‚¬“5: background, low urgency
- 1Ã¢â‚¬“3: noise or spam

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
- `run()` input is always `CanonicalDocument` Ã¢â‚¬” never a raw dict or ORM model
- `run()` output is always `PipelineResult` Ã¢â‚¬” never raises (errors surfaced in `result.error`)
- No direct DB writes inside `AnalysisPipeline` or `PipelineResult`
- `apply_to_document()` is the only point where scores and entities are written back to the document
- `llm_output` is optional; `analysis_result` is the required downstream contract for a successful run
- absence or failure of an external provider must degrade to a valid fallback-compatible analysis result,
  not an empty pipeline outcome
- `run_batch()` is concurrency-bounded by `_MAX_CONCURRENT`

---

### 8. Scoring Contract

Scoring is part of the pipeline result Ã¢â‚¬” not a separate side-effect.

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
3. title hash match (score 0.85 Ã¢â‚¬” catches same headline across sources)

Rules:
- conservative by default Ã¢â‚¬” prefer false negatives over false positives
- `is_duplicate()` never writes to DB Ã¢â‚¬” read-only
- dedup is enforced exclusively by `document_ingest.py` before `repo.save_document()`
- `filter_scored()` is used by `persist_fetch_result()` Ã¢â‚¬” returns all docs with scores for auditing
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
- every provider must return a fully validated `LLMAnalysisOutput` Ã¢â‚¬” never a raw dict (I-7)
- `analyze()` never receives a `CanonicalDocument` directly Ã¢â‚¬” caller extracts `title` + `text`
- providers are replaceable without touching pipeline logic
- structured output enforcement is provider-specific (OpenAI: `response_format`, Anthropic: tool-use,
  Gemini: `response_schema`) Ã¢â‚¬” but the output contract is identical
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
- never trust provider output blindly Ã¢â‚¬” schema validation is mandatory

---

### 11. Sprint 4 Ã¢â‚¬” Research & Signal Contracts

These contracts define the Sprint 4 output layer. All three types are **in-memory only** Ã¢â‚¬”
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
- Source: `monitor/watchlists.yml` Ã¢â‚¬” loaded via `WatchlistEntry` + `load_watchlist()`
- Sections: `crypto`, `equities`, `etfs`, `macro`, `persons`, `topics`, `domains`
- Tag lookup is case-insensitive
- `filter_documents()` is the primary document-to-watchlist matching path
- `WatchlistRegistry` is read-only after construction Ã¢â‚¬” no mutations during runtime
- `load_watchlist()` returns `[]` (not an error) if the file does not exist
- `find_by_text()` Ã¢â‚¬” Sprint 4B planned, not yet implemented; use `filter_documents()` instead

---

#### 11b. ResearchBrief

```python
class BriefFacet(BaseModel):
    name: str
    count: int

class BriefDocument(BaseModel):
    document_id: str          # str(CanonicalDocument.id) Ã¢â‚¬” traceability
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
- Input: `list[CanonicalDocument]` Ã¢â‚¬” only `is_analyzed=True` docs are used
- `ResearchBriefBuilder.build()` never raises Ã¢â‚¬” returns empty brief on empty/unanalyzed input
- `_ACTIONABLE_PRIORITY_THRESHOLD = 8` Ã¢â‚¬” must stay in sync with `ThresholdEngine.min_priority`
- Sorted by (priority_score, impact_score, published_at) descending
- `to_markdown()` and `to_json_dict()` are the only output serialization paths
- `ResearchBrief` is in-memory only Ã¢â‚¬” no DB table, no persistence

---

#### 11c. SignalCandidate

```python
class SignalCandidate(BaseModel):
    model_config = ConfigDict(strict=True, validate_assignment=True)

    signal_id: str              # f"sig_{document_id}" Ã¢â‚¬” deterministic
    document_id: str            # str(CanonicalDocument.id) Ã¢â‚¬” traceability

    target_asset: str           # primary asset ("BTC", "ETH", "General Market")
    direction_hint: str         # "bullish" | "bearish" | "neutral"
                                # NEVER "buy" / "sell" / "hold" Ã¢â‚¬” not an execution instruction
    confidence: float           # proxy: doc.relevance_score Ã¢â‚¬” [0.0, 1.0]
    supporting_evidence: str    # doc.summary or doc.title
    contradicting_evidence: str # static note Ã¢â‚¬” not extracted in primary scan
    risk_notes: str             # spam_prob + market_scope metadata
    source_quality: float       # doc.credibility_score Ã¢â‚¬” [0.0, 1.0]
    recommended_next_step: str  # always ends with "Ã¢â‚¬” human decision required."

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
- `priority >= 8` is a hard constraint Ã¢â‚¬” Pydantic `Field(ge=8)` enforced at construction
- `direction_hint` is research language, NOT trading instruction Ã¢â‚¬” "bullish"/"bearish"/"neutral"
- `signal_id` is deterministic: `f"sig_{document_id}"` Ã¢â‚¬” idempotent for same document
- `watchlist_boosts`: `{"BTC": 1}` raises effective priority by 1 for watchlist assets;
  capped at 10; never raises above 10
- `confidence_score` from `AnalysisResult` is NOT persisted to DB Ã¢â‚¬” `relevance_score` is
  used as the confidence proxy (available in DB)
- `SignalCandidate` is in-memory only Ã¢â‚¬” no DB table, no persistence
- `extract_signal_candidates()` never raises Ã¢â‚¬” returns `[]` if no candidates qualify

---

#### 11d. Research Layer Boundaries

| Boundary | Rule |
|---|---|
| Input gate | Only `CanonicalDocument` with `is_analyzed=True` enters research layer |
| No DB writes | `ResearchBrief` and `SignalCandidate` are always in-memory Ã¢â‚¬” never persisted |
| No LLM calls | Research layer is pure computation Ã¢â‚¬” no provider calls, no external I/O |
| Watchlist source | Always from `monitor/watchlists.yml` via `WatchlistRegistry.from_monitor_dir()` |
| CLI entry point | `research` Typer subgroup Ã¢â‚¬” `watchlists`, `brief`, `signals` commands |
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

**Status: Ã¢Å“â€¦ Implemented** (`app/core/settings.py`)

```python
companion_model_endpoint: str | None = None      # e.g. "http://localhost:11434"
companion_model_name: str = "kai-analyst-v1"
companion_model_timeout: int = 10                # seconds
```

Security constraint: `companion_model_endpoint` MUST be `localhost` or an explicitly allowlisted
internal address. Field validator rejects external URLs at settings load time.

---

#### 13b. Factory Routing

**Status: Ã¢Å“â€¦ Implemented** (`app/analysis/factory.py`)

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

`EnsembleProvider` (`app/analysis/ensemble/provider.py`) is not a factory target Ã¢â‚¬” it wraps
multiple providers directly. Its `provider_name` is a compound string like
`"ensemble(openai,internal)"` (see Ã‚§13e on EnsembleProvider and analysis_source).

---

#### 13c. AnalysisSource Enum

**Status: Ã¢Å“â€¦ Implemented** (`app/core/enums.py`, `app/core/domain/document.py`)

```python
# app/core/enums.py
class AnalysisSource(StrEnum):
    RULE = "rule"                  # Tier 1 Ã¢â‚¬” fallback / rule-based heuristics
    INTERNAL = "internal"          # Tier 2 Ã¢â‚¬” InternalModelProvider or InternalCompanionProvider
    EXTERNAL_LLM = "external_llm"  # Tier 3 Ã¢â‚¬” OpenAI / Anthropic / Gemini
```

**Current implementation**:
- `CanonicalDocument.analysis_source: AnalysisSource | None` exists as an explicit field
- `AnalysisResult.analysis_source: AnalysisSource | None` exists as an explicit field
- `canonical_documents.analysis_source` is a persisted DB column (migration `0006`)
- `CanonicalDocument.effective_analysis_source` remains the backward-compatible accessor for legacy rows

```python
# app/core/domain/document.py Ã¢â‚¬” compatibility accessor
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
- Distillation corpus selects ONLY `analysis_source=EXTERNAL_LLM` documents (Ã‚§14e)

---

#### 13d. Companion Model Output Scope

**Status: Ã¢Å“â€¦ Implemented** (`app/analysis/providers/companion.py`)

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
field (Ã‚§14c). The internal reasoning trace is not part of the training corpus output format.

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
   starts with `"ensemble("` to `INTERNAL`. This is the primary guard Ã¢â‚¬” it sets
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
| openai won | `"ensemble(openai,internal)"` | `"internal"` Ã¢Å¡Â Ã¯Â¸Â (conservative) | `"external_llm"` Ã¢Å“â€¦ |
| internal won | `"ensemble(openai,internal)"` | `"internal"` Ã¢Å“â€¦ (correct) | `"internal"` Ã¢Å“â€¦ |

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
    {"role": "assistant", "content": "<JSON target scores Ã¢â‚¬” sorted keys>"}
  ],
  "metadata": {
    "document_id":     "<uuid Ã¢â‚¬” str>",
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
| `affected_assets` | list[str] | Ã¢Å“â€¦ | deduplicated from `doc.tickers + doc.crypto_assets` |
| `impact_score` | float | Ã¢Å“â€¦ | 0.0 .. 1.0 |
| `market_scope` | str | Ã¢Å“â€¦ | e.g. `"crypto"` / `"etf"` / `"unknown"` |
| `novelty_score` | float | Ã¢Å“â€¦ | 0.0 .. 1.0 |
| `priority_score` | int | Ã¢Å“â€¦ | 1 .. 10 |
| `relevance_score` | float | Ã¢Å“â€¦ | 0.0 .. 1.0 |
| `sentiment_label` | str | Ã¢Å“â€¦ | `"bullish"` / `"bearish"` / `"neutral"` |
| `sentiment_score` | float | Ã¢Å“â€¦ | -1.0 .. 1.0 |
| `spam_probability` | float | Ã¢Å“â€¦ | 0.0 .. 1.0 |
| `summary` | str | Ã¢Å“â€¦ | `doc.summary` or `""` |
| `tags` | list[str] | Ã¢Å“â€¦ | `doc.ai_tags` |

All fields are always present (no optional fields in the assistant target).

---

#### 14c. `co_thought` Ã¢â‚¬” Final Decision: REMOVED

**`co_thought` is NOT part of the export format.**

This field was considered during Sprint 5A design. Final rationale for removal:

1. **Contamination risk**: Rule-based analysis sets `explanation_short = "Rule-based fallback
   analysis. ..."` Ã¢â‚¬” a heuristic label, not reasoning. Including it as chain-of-thought
   training signal would teach the companion model a placeholder, not financial reasoning.

2. **Inconsistent quality**: Even LLM-sourced `explanation_short` values vary in quality and
   depth. The field is a brief annotation, not a structured reasoning trace.

3. **Schema coupling**: `co_thought` would couple the export to `doc.metadata["explanation_short"]`
   Ã¢â‚¬” an implementation detail, not a stable contract field.
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
| `"external_llm"` | External LLM (OpenAI, Anthropic, Gemini) | `"openai"`, `"anthropic"`, `"gemini"`, etc. | Ã¢Å“â€¦ yes |
| `"internal"` | Tier 2 analysis (heuristic or companion HTTP) | `"internal"`, `"companion"` | Ã¢Å¡Â Ã¯Â¸Â evaluation only |
| `"rule"` | Rule-based / fallback analysis | `None`, `"fallback"`, `"rule"` | Ã¢ÂÅ’ no (I-19) |

**Source of truth**: exported `metadata["analysis_source"]`, produced from
`doc.effective_analysis_source` (`CanonicalDocument`, `app/core/domain/document.py`).

```python
# Backward-compatible accessor (current implementation):
doc.effective_analysis_source
# Ã¢â€ ’ returns doc.analysis_source if explicitly set (Sprint 5B field)
# Ã¢â€ ’ falls back to derivation from doc.provider (legacy path for pre-Sprint-5B rows)
```

The export reads `doc.effective_analysis_source.value` Ã¢â‚¬” never derives analysis_source inline.

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
- `doc.provider` Ã¢â‚¬” may be `"openai"` (teacher), `"internal"` (not teacher), or a legacy
  composite `"ensemble(openai,internal)"` Ã¢â‚¬” ambiguous without `analysis_source`
- `doc.metadata["ensemble_chain"]` Ã¢â‚¬” audit trail only, not a classification signal
- Any other metadata field

The **only valid teacher filter** is `metadata["analysis_source"] == "external_llm"`.
This ensures no ensemble composition detail can bypass I-16 or I-19.

---

#### 14f. provider vs analysis_source Ã¢â‚¬” Contract Separation

These are two distinct concepts that must never be conflated:

| Concept | Field | Type | Persistence | Purpose |
|---------|-------|------|-------------|---------|
| `provider` | `doc.provider` | `str \| None` | DB column | Technical engine name. Pre-5C: `"openai"`, `"internal"`, `"ensemble(openai,internal)"`, `"fallback"`. Post-5C: always the **winner name** Ã¢â‚¬” never a composite string. |
| `analysis_source` | `doc.analysis_source` | `AnalysisSource` enum | DB column (migration 0006) | Semantic tier: `RULE` / `INTERNAL` / `EXTERNAL_LLM` Ã¢â‚¬” stable, use this for filtering. |
| `ensemble_chain` | `doc.metadata["ensemble_chain"]` | `list[str]` | JSON metadata | Full ordered provider list when `EnsembleProvider` was used. Set by Sprint-5C. Legacy rows: absent. |

**Rules:**
- `provider` is a technical string Ã¢â‚¬” never use it directly for corpus filtering or tier decisions
- `analysis_source` is the stable semantic value Ã¢â‚¬” always use this for filtering and guardrails
- `provider` semantics changed in Sprint-5C: composite `"ensemble(...)"` strings are legacy only
- `analysis_source` must NEVER be set manually Ã¢â‚¬” always set by pipeline at result creation time
- Downstream code (ResearchBrief, SignalCandidate, alerts) consumes analysis results via the same
  `CanonicalDocument` contract regardless of which tier produced them Ã¢â‚¬” no branching on `provider`

**Companion model in research outputs:**
Companion-analyzed documents (`analysis_source=INTERNAL`) flow through the same research pipeline:
- `ResearchBrief.key_documents` Ã¢â‚¬” Ã¢Å“â€¦ included
- `ResearchBrief.top_actionable_signals` Ã¢â‚¬” Ã¢Å“â€¦ if `priority >= 8`
- `SignalCandidate` Ã¢â‚¬” Ã¢Å“â€¦ if `priority >= 8` (companion can reach 8 with strong output)
- Alert gating Ã¢â‚¬” Ã¢Å“â€¦ same `ThresholdEngine.is_alert_worthy()` path

No parallel models, no second result format. Provenance is tracked via `analysis_source` only.

---

## Final Rule

These contracts define the system.

If they become inconsistent with the code, the system becomes unstable.

**Protect them. Update them. Never bypass them.**

---

### 15. Sprint-5C Ã¢â‚¬” Winner-Traceability Contract

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
`doc.analysis_source = INTERNAL` and `doc.provider = "ensemble(openai,internal)"` Ã¢â‚¬”
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
    """Return the actual winner name after analyze() Ã¢â‚¬” for EnsembleProvider via duck typing."""
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
pipelines Ã¢â‚¬” `_resolve_runtime_provider_name` returns the winner name instead. The composite
guard remains in `CanonicalDocument.effective_analysis_source` for legacy DB rows only (Ã‚§15e).

**Pipeline call site** (success path):

```python
# trace_metadata resolved before analyze() Ã¢â‚¬” provider_chain doesn't change
trace_metadata = _resolve_trace_metadata(self._provider)   # {"ensemble_chain": [...]} or {}

llm_output = await self._provider.analyze(title=..., text=..., context=...)

# winner name resolved AFTER analyze() Ã¢â‚¬” active_provider_name updated by EnsembleProvider
provider_name = _resolve_runtime_provider_name(self._provider) or self._provider.provider_name
analysis_source = _resolve_analysis_source(provider_name)   # I-24
```

**Error path** (except branch):

```python
except Exception as exc:
    # analysis_source = RULE (set by _build_fallback_analysis, always Ã¢â‚¬” I-13)
    # provider_name stays "fallback" (initialized at top of run())
    # _resolve_runtime_provider_name() is not called in the error path
    analysis_result = self._build_fallback_analysis(...)
```

The error path never performs winner resolution. `analysis_source=RULE` always when analysis
failed Ã¢â‚¬” regardless of which provider was configured.

---

#### 15c. doc.provider Ã¢â‚¬” winner name, not composite

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
| No provider configured | `"fallback"` | `RULE` | Ã¢ÂÅ’ never |
| Provider call failed Ã¢â€ ’ fallback | `"fallback"` | `RULE` | Ã¢ÂÅ’ never |
| InternalModelProvider ran | `"internal"` | `INTERNAL` | Ã¢Å¡Â Ã¯Â¸Â eval only |
| InternalCompanionProvider ran | `"companion"` | `INTERNAL` | Ã¢Å¡Â Ã¯Â¸Â eval only |
| OpenAI ran (direct) | `"openai"` | `EXTERNAL_LLM` | Ã¢Å“â€¦ teacher |
| Ensemble: openai won | `"openai"` (from `ensemble.model`) | `EXTERNAL_LLM` | Ã¢Å“â€¦ teacher |
| Ensemble: internal fallback | `"internal"` (from `ensemble.model`) | `INTERNAL` | Ã¢Å¡Â Ã¯Â¸Â eval only |
| Ensemble: companion fallback | `"companion"` (from `ensemble.model`) | `INTERNAL` | Ã¢Å¡Â Ã¯Â¸Â eval only |

---

#### 15e. Backward compatibility

- Pre-Sprint-5C rows: `doc.provider` may be `"ensemble(openai,internal)"`.
  `effective_analysis_source` maps `startswith("ensemble(")` Ã¢â€ ’ `INTERNAL` (conservative).
  These rows are NOT upgraded automatically. The conservative mapping is intentional.
- New rows (Sprint-5C+): `doc.provider` is always the winner name. The `ensemble_chain`
  metadata key is present if an `EnsembleProvider` was used.

---

#### 15f. Non-ensemble providers: no change

For `OpenAIAnalysisProvider`, `AnthropicAnalysisProvider`, `GeminiAnalysisProvider`,
`InternalModelProvider`, `InternalCompanionProvider` used directly (not via ensemble):

- `provider.model` is the **model identifier** (e.g. `"gpt-4o"`, `"rule-heuristic-v1"`),
  **not** the provider name.
- The pipeline uses `provider.provider_name` for `doc.provider` Ã¢â‚¬” unchanged.
- `_resolve_analysis_source()` logic (provider-object-based, pre-analyze) Ã¢â‚¬” unchanged.

Only `EnsembleProvider` triggers post-analyze winner resolution (I-24).

---

#### 15g. End-to-End Provenance Flow (post Sprint-5C)

This trace documents the full lifecycle of provenance from ingestion to research outputs.
Every downstream consumer relies on `doc.analysis_source` Ã¢â‚¬” never on `doc.provider`.

```
1. Ingestion
   doc.provider       = None
   doc.analysis_source = None
   doc.status          = PERSISTED

2. analyze_pending Ã¢â€ ’ AnalysisPipeline.run(doc)
   Pre-analyze:
     trace_metadata = _resolve_trace_metadata(ensemble)
       Ã¢â€ ’ {"ensemble_chain": ["openai", "internal"]}  (provider_chain property)

3. await ensemble.analyze(title, text, context)
   EnsembleProvider iterates providers in order:
     Ã¢â€ ’ tries openai.analyze()   Ã¢â€ Â succeeds
     Ã¢â€ ’ (internal.analyze() never called)
   ensemble._active_provider_name = "openai"   Ã¢â€ Â winner recorded (I-23)

   Fallback scenario (openai fails):
     Ã¢â€ ’ tries openai.analyze()   Ã¢â€ Â raises RuntimeError
     Ã¢â€ ’ tries internal.analyze() Ã¢â€ Â succeeds
     ensemble._active_provider_name = "internal"
     analysis_source (post-5C) Ã¢â€ ’ INTERNAL Ã¢Å“â€¦ (correct, internal ran)

4. Post-analyze (Sprint-5C, success path)
   provider_name   = _resolve_runtime_provider_name(ensemble)
                   = ensemble.active_provider_name Ã¢â€ ’ "openai"
   analysis_source = _resolve_analysis_source("openai")  Ã¢â€ ’ EXTERNAL_LLM (I-24)

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
   doc.effective_analysis_source Ã¢â€ ’ returns doc.analysis_source  Ã¢â€ ’ EXTERNAL_LLM

9. export_training_data(doc)
   metadata["analysis_source"] = effective_analysis_source.value  Ã¢â€ ’ "external_llm"
   Ã¢â€ ’ teacher-eligible Ã¢Å“â€¦ (I-16, I-19 satisfied)

10. extract_signal_candidates(doc)
    signal.analysis_source = effective_analysis_source.value  Ã¢â€ ’ "external_llm"

11. ResearchBriefBuilder._to_brief_document(doc)
    brief_doc.analysis_source = effective_analysis_source.value  Ã¢â€ ’ "external_llm"
```

**Consistency invariant**: All consumers in steps 9Ã¢â‚¬“11 read `doc.effective_analysis_source`.
If `doc.analysis_source` is set (post-pipeline), that value is returned directly.
If not set (legacy pre-5B row), the property derives from `doc.provider` conservatively.
This guarantees no consumer ever branches on `provider` for tier decisions.

**Error-path scenario** (all ensemble providers fail Ã¢â€ ’ RuntimeError re-raised):
```
3'. All providers fail Ã¢â€ ’ pipeline except branch Ã¢â€ ’ _build_fallback_analysis()
    analysis_source  = RULE   (set by fallback builder, always)
    provider_name    = "ensemble(openai,internal)"  (unchanged, composite Ã¢â‚¬” pre-5C legacy)
    doc.analysis_source = RULE after apply_to_document()
    Ã¢â€ ’ teacher-ineligible Ã¢Å“â€¦ (RULE never teacher Ã¢â‚¬” I-19)
    Ã¢â€ ’ effective_analysis_source returns RULE (analysis_source is set)
```
`_resolve_analysis_source_from_winner()` is never called in the error path (I-24).

**Legacy rows** (pre-Sprint-5C, where `doc.provider = "ensemble(openai,internal)"`):
- `doc.analysis_source` may be `None` or `INTERNAL`
- `effective_analysis_source` returns `INTERNAL` (conservative)
- These rows are NOT corpus-eligible even if `openai` had won Ã¢â‚¬” intentional tradeoff (I-26)

---

---

## Immutable Invariants

These may never be broken without a new spec:

| # | Rule |
|---|---|
| I-1 | `content_hash` is auto-computed Ã¢â‚¬” never set manually |
| I-2 | `word_count` is never stored in DB |
| I-3 | `repo.save()` is idempotent on hash collision |
| I-4 | `apply_to_document()` is the only score mutation point |
| I-5 | `update_analysis()` always sets `is_analyzed=True` and `status=analyzed` |
| I-6 | `AnalysisResult` has no DB table Ã¢â‚¬” scores are denormalized |
| I-7 | LLM output always arrives as validated `LLMAnalysisOutput` Ã¢â‚¬” never raw dict |
| I-8 | `spam_probability > 0.7` Ã¢â€ ’ `priority_score Ã¢â€°Â¤ 3` |
| I-9 | status transitions are one-way |
| I-10 | `is_analyzed` and `status=analyzed` are set together, atomically |
| I-11 | `AnalysisResult.confidence_score` is in-memory only Ã¢â‚¬” NOT written to DB. The DB column `credibility_score` is computed as `1.0 - spam_probability` inside `update_analysis()` |
| I-12 | A document with `analysis_result=None` MUST NOT have `status=ANALYZED` set. `update_analysis(doc_id, None)` is a contract violation Ã¢â‚¬” caller must check for None and mark FAILED |
| I-13 | Deterministic fallback analysis must remain conservative and must not bypass the shared signal thresholding path |
| I-14 | `InternalCompanionProvider` implements `BaseAnalysisProvider` exactly Ã¢â‚¬” zero pipeline changes required for companion introduction |
| I-15 | Companion model endpoint MUST be localhost or allowlisted internal address Ã¢â‚¬” no external inference calls |
| I-16 | Distillation corpus uses only `analysis_source=EXTERNAL_LLM` documents as teacher signal |
| I-17 | Companion model `impact_score` cap: Ã¢â€°Â¤ 0.8 (conservative, not overconfident) |
| I-18 | `AnalysisSource` is set at result creation time Ã¢â‚¬” immutable after `apply_to_document()` |
| I-19 | Rule-only documents (`analysis_source=RULE`) NEVER serve as distillation teacher signal |
| I-20 | `InternalModelProvider.provider_name` is always `"internal"`, `recommended_priority` Ã¢â€°Â¤ 5, `actionable=False`, `sentiment_label=NEUTRAL` Ã¢â‚¬” these are hard invariants, not configurable |
| I-21 | `InternalCompanionProvider.provider_name` is always `"companion"` Ã¢â‚¬” distinct from `"internal"` (heuristic). factory.py routes `"internal"` Ã¢â€ ’ `InternalModelProvider`, `"companion"` Ã¢â€ ’ `InternalCompanionProvider` |
| I-22 | `EnsembleProvider` requires at least one provider. InternalModelProvider MUST be the last entry to guarantee a fallback result. If all providers fail, raises `RuntimeError` |
| I-23 | `EnsembleProvider.model` MUST return the winning provider's `provider_name` (not the composite string) immediately after `analyze()` completes. This is the canonical winner signal for pipeline source resolution. |
| I-24 | `_resolve_runtime_provider_name(provider)` resolves the winner name AFTER `analyze()` succeeds using duck-typed `active_provider_name`. `_resolve_analysis_source(winner_name)` then derives the tier. Neither is called in the error/fallback path Ã¢â‚¬” only `RULE` is valid when analysis failed. |
| I-25 | `doc.provider` stores the **winning** provider name (e.g. `"openai"`, `"internal"`) Ã¢â‚¬” never the composite ensemble string. `doc.metadata["ensemble_chain"]` records the full ordered list for traceability. |
| I-26 | Teacher eligibility is determined exclusively by `analysis_source=EXTERNAL_LLM`. `doc.provider`, `doc.metadata["ensemble_chain"]`, and all other metadata fields MUST NOT be used as teacher-eligibility criteria. No ensemble composition detail may bypass I-16 or I-19. |
| I-27 | `export_training_data()` MUST enforce teacher-eligibility at the function level when `teacher_only=True`. Uses `doc.analysis_source` directly (not `effective_analysis_source`) Ã¢â‚¬” legacy rows without an explicit field are excluded. Ã¢Å“â€¦ Implemented. |
| I-28 | The `evaluate` CLI command compares teacher-labeled scores against rule-baseline scores (no LLM calls). This is the Sprint-6 baseline only Ã¢â‚¬” it does NOT represent companion-model accuracy until a real companion inference endpoint is configured. |
| I-29 | Sprint-6 dataset roles are determined exclusively by `analysis_source`: `EXTERNAL_LLM` = teacher-only, `INTERNAL` = benchmark-only, `RULE` = baseline-only. |
| I-30 | `INTERNAL` and `RULE` rows MUST NEVER be used as teacher labels for distillation, even when other metadata appears favorable. |
| I-31 | Teacher-only filtering MUST use `doc.analysis_source` directly (strict mode, not `effective_analysis_source`) Ã¢â‚¬” never `provider`, `ensemble_chain`, source name, title, or URL. |
| I-32 | `compare_datasets()` joins datasets by `metadata["document_id"]` only. No fuzzy matching by URL, title, or publish time is allowed. |
| I-33 | The evaluation metric set is mandatory: `sentiment_agreement`, `priority_mae`, `relevance_mae`, `impact_mae`, `tag_overlap_mean`, `actionable_accuracy`, and `false_actionable_rate`. All are implemented in `EvaluationMetrics`. |
| I-34 | Before companion promotion, `false_actionable_rate` MUST be evaluated on paired teacher/candidate rows only and remain `<= 0.05`. `actionable_accuracy` is reported for audit but is not a gate by itself. |
| I-35 | `research check-promotion` reads a saved `evaluation_report.json` only. It MUST NOT trigger analysis, DB reads, or model inference. |
| I-36 | Promotion is never automatic. `check-promotion` exiting 0 does NOT change any system state. A human operator must act on the result explicitly. |
| I-37 | `--save-report` / `--save-artifact` flags are audit-trail only. They do NOT change evaluation semantics or metric values. |
| I-38 | Benchmark artifacts are read-only once written. A re-run MUST produce a new file, never overwrite in-place. |
| I-39 | Companion remains `analysis_source=INTERNAL` until an operator explicitly reconfigures the provider. Passing promotion gates does NOT change provider routing. |
| I-40 | No Sprint-8 code path trains a model, modifies weights, or calls an external training API. Training is exclusively an external operator process. |
| I-41 | `promotion_record.json` is an audit artifact only Ã¢â‚¬” it does NOT change provider routing. Routing is controlled exclusively by env vars. |
| I-42 | Provider routing is controlled exclusively by `APP_LLM_PROVIDER` and `companion_model_endpoint` env vars. No platform code writes to these. |
| I-43 | `save_promotion_record()` requires a non-empty `operator_note`. Blank notes raise `ValueError`. Operators must acknowledge the promotion decision explicitly. |
| I-44 | Promotion is reversible by setting `APP_LLM_PROVIDER` to the previous value. No migration or code change required. |
| I-45 | `record-promotion` and `save_promotion_record()` require the evaluation report to exist and pass all 6 quantitative gates (G1Ã¢â‚¬“G6). Non-passing reports block record creation. |
| I-46 | `false_actionable_rate` is the 6th automated promotion gate (G6, threshold <= 0.05). Computed by `compare_datasets()`, enforced by `validate_promotion()` as `false_actionable_pass`. Supersedes the original I-34 "manual, deferred" note. |
| I-47 | `PromotionRecord` MUST embed `gates_summary: dict[str, bool]` Ã¢â‚¬” a snapshot of all 6 gate pass/fail results at record creation time. A promotion record without gate evidence is incomplete. |
| I-48 | `record-promotion` MUST call `validate_promotion()` and pass the result as `gates_summary` to `save_promotion_record()`. This makes the record self-documenting. |
| I-49 | When `--tuning-artifact` is provided to `record-promotion`, the artifact's `evaluation_report` field MUST resolve to the same path as the provided `report_file`. Mismatch blocks record creation (Exit 1). |
| I-50 | Sprint 9 changes no routing. No new provider, no analysis tier change. All routing remains operator-controlled via env vars (I-42). |
| I-51 | Shadow run MUST NEVER call `apply_to_document()` or `repo.update_analysis()`. Zero DB writes to `canonical_documents`. Shadow result is JSONL-only. |
| I-52 | Shadow run calls `InternalCompanionProvider.analyze()` directly and explicitly Ã¢â‚¬” independent of `APP_LLM_PROVIDER`. Shadow run is a separate, explicit audit call, never a routing override. |
| I-53 | Shadow JSONL is a standalone audit artifact. It MUST NOT be used as evaluation report input, training teacher data, or promotion gate input. |
| I-54 | Shadow run requires `companion_model_endpoint` to be configured. If absent, the command exits 0 with an informational message Ã¢â‚¬” not an error. |
| I-55 | Divergence summary is informational only. It MUST NOT be used for routing decisions, promotion gating, alert filtering, or research output modification. |
| I-56 | Live shadow (inline `--shadow` flag in `analyze-pending`/`pipeline run`): Shadow provider runs concurrent to Primary inside `AnalysisPipeline.run()`. Both launched as `asyncio.create_task()`; Primary is awaited first. Shadow exception is caught non-blocking Ã¢â‚¬” `shadow_error` set, primary unaffected. |
| I-57 | Live shadow persistence: `update_analysis()` receives `metadata_updates=res.document.metadata` (after `apply_to_document()`) Ã¢â‚¬” NOT `res.trace_metadata`. This ensures `shadow_analysis` and `shadow_provider` written by `apply_to_document()` reach the DB `document_metadata` column. Enforced in both `run_rss_pipeline()` and `analyze-pending`. |
| I-58 | `DistillationReadinessReport` is a readiness assessment only. It MUST NOT trigger training, weight updates, or provider routing changes. `promotion_validation.is_promotable=True` is informational Ã¢â‚¬” the operator must still use `record-promotion` explicitly (I-36, I-39). |
| I-59 | Shadow JSONL MUST NEVER be passed as `DistillationInputs.teacher_path` or `candidate_path`. Shadow records are audit artifacts only (I-16, I-53). |
| I-60 | `compute_shadow_coverage()` reads shadow records for aggregate divergence stats only. It MUST NOT call `compare_datasets()` or treat shadow data as candidate baseline input. |
| I-61 | `DistillationReadinessReport.shadow_coverage` is optional. Absent shadow data does not invalidate or block a distillation readiness assessment. |
| I-62 | `build_distillation_report()` is pure computation Ã¢â‚¬” no DB reads, no LLM calls, no network. All I/O is JSONL/JSON file reads via `load_jsonl()` and `json.loads()`. |
| I-63 | `TrainingJobRecord` is a platform-side pre-training manifest only. No platform code runs training jobs, calls fine-tuning APIs, or modifies model weights. Training is exclusively an external operator process. |
| I-64 | A `TrainingJobRecord` with `status="pending"` does not represent a trained model. The operator must run training externally before post-training evaluation can begin. |
| I-65 | Post-training evaluation MUST use the same promotion gates G1Ã¢â‚¬“G6 as pre-promotion evaluation. `validate_promotion()` is the canonical gate Ã¢â‚¬” no Sprint-12 bypass is permitted. |
| I-66 | A trained model is not active until the operator reconfigures `APP_LLM_PROVIDER` and `companion_model_endpoint`. No Sprint-12 code changes routing (I-42 extends here). |
| I-67 | The teacher dataset used in `TrainingJobRecord` MUST contain only `analysis_source=EXTERNAL_LLM` rows. `INTERNAL`, `RULE`, and Shadow records MUST NOT be used as training input (I-16, I-19, I-53 extend here). |
| I-68 | `record-promotion` remains the sole promotion gate. `TrainingJobRecord` and `PostTrainingEvaluationSpec` are audit artifacts only Ã¢â‚¬” they do not trigger or substitute promotion. |
| I-69 | Sprint-12 canonicalizes shadow JSONL schema: `shadow.py` MUST write `"deviations"` field (with `priority_delta`, `relevance_delta`, `impact_delta`) as canonical Ã¢â‚¬” matching `evaluation.py`. `"divergence"` remains as deprecated backward-compat alias. `compute_shadow_coverage()` continues to normalize both formats until old shadow files are migrated. |
| I-70 | `EvaluationComparisonReport` is a comparison artifact only Ã¢â‚¬” no routing change, no promotion trigger, no G1Ã¢â‚¬“G6 gate bypass. |
| I-71 | `compare_evaluation_reports(baseline_report, candidate_report)` takes `EvaluationReport` objects Ã¢â‚¬” it is pure computation. No DB reads, no LLM calls, no network. The CLI `compare-evaluations` handles file loading via `load_saved_evaluation_report()` before calling this function. (I-62 extends here.) |
| I-72 | When `regression_summary.has_regression=True` in the comparison report and `--comparison` is provided to `record-promotion`, a prominent WARNING is printed. Promotion is NOT automatically blocked Ã¢â‚¬” the operator must explicitly decide to proceed. `PromotionRecord.comparison_report_path` is set for the audit trail. Hard regression per-metric thresholds (R1Ã¢â‚¬“R6) are deferred; `has_regression` (any worsening) is the current operative flag. |
| I-73 | `compare-evaluations` exit code 0 does NOT imply the candidate is promotable. `check-promotion` on the candidate report remains required (I-36, I-65). The comparison is additional audit context only. |
| I-74 | Baseline and candidate evaluation reports MUST share the same `dataset_type`. Different `dataset_type` values raise `ValueError` in `compare_evaluation_reports()`. |
| I-75 | `UpgradeCycleReport` is a pure read/summarize artifact. `build_upgrade_cycle_report()` MUST NOT trigger training, evaluation reruns, promotions, or routing changes. The only I/O is JSON file reads via `json.loads()`. (I-62, I-70 extend here.) |
| I-76 | `UpgradeCycleReport.status` is derived exclusively from artifact presence (`Path.exists()`) Ã¢â‚¬” never auto-advanced by the platform. No platform code advances `status` without the operator supplying a new artifact path. |
| I-77 | `UpgradeCycleReport.promotion_readiness=True` is informational only. No platform code calls `record-promotion` or changes `APP_LLM_PROVIDER` based on this field. The operator must run `record-promotion` explicitly (I-36, I-68 extend here). |
| I-78 | `UpgradeCycleReport.promotion_record_path` is set ONLY when the operator explicitly supplies this path to `build_upgrade_cycle_report()` or the CLI. It MUST NOT be auto-populated from env vars or settings. |
| I-79 | Each `UpgradeCycleReport` represents one upgrade cycle attempt. Parallel or sequential cycles (e.g. v1Ã¢â€ ’v2, v2Ã¢â€ ’v3) produce separate files. A cycle report MUST NOT be overwritten in-place Ã¢â‚¬” re-runs produce new files (I-38 extends here). |
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
| I-93 | `ABCInferenceEnvelope` produced during a shadow-enabled analyze-pending run is written per-document to audit JSONL only Ã¢â‚¬” no DB writes, no routing changes. |
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

---


---

## §85 — Sprint: PH5C_FILTER_BEFORE_LLM_BASELINE (D-95/D-97, 2026-03-24)

**Sprint:** Pre-LLM stub document filter baseline — classify placeholder documents, validate threshold, produce filter recommendation.

**Results:**
- `conservative_placeholder_skip`: flagged=11, recall=58%, FP=0 — **recommended**
- `aggressive_placeholder_skip`: flagged=30, recall=100%, FP=11

§85 status: **closed (D-97, 2026-03-24)**

---

## STRATEGIC HOLD (D-97, 2026-03-24)

No new sprint contracts (§86+) will be opened until the operator confirms:

1. Alert precision shows a clearly positive finding.
2. Paper-trading metrics show a clearly positive finding.

**Phase-5 sprints blocked:** PH5D and all subsequent sprints.

Hold status: **active** — lifted only by operator decision.

Documentation policy (D-99): no new standalone sprint-contract documents.
Decisions are documented only as short code comments or compact 3-line
entries in `DECISION_LOG.md`.
