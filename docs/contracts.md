# Contracts and Core Data Models

## Current State (2026-03-25)

| Field | Value |
|---|---|
| current_phase | `PHASE 5 (active) -- strategic hold on companion-ML infrastructure` |
| current_sprint | `PH5C_FILTER_BEFORE_LLM_BASELINE (closed D-97)` |
| next_required_step | `STRATEGIC_HOLD -- continue daily pipeline runs and resolve >=50 directional alerts (hit/miss)` |
| baseline | `See CI/local checks; do not treat static pass-count as a contract` |
| archive | `docs/archive/contracts_archive.md` (closed sections 38-82) |

## Navigation

| Section | Content |
|---|---|
| [Core Contracts](#core-contracts) | Sections 0-15: Domain models, invariants, intelligence stack |
| [Immutable Invariants](#immutable-invariants) | Non-negotiable runtime rules |
| [Strategic Hold](#strategic-hold-d-97) | Companion-ML freeze policy and gate conditions |
| [Archive](archive/contracts_archive.md) | Closed sections 38-82 (Phase 1-4) |

## Strategic Hold (D-97)

- No new companion-ML infrastructure sprint, decision, or invariant is opened while the strategic hold is active.
- Hold release gate is operator-driven and requires both:
  1. clearly positive alert-precision evidence
  2. clearly positive paper-trading metric evidence
- Documentation policy (D-99): no new standalone sprint-contract documents.
  Decisions are recorded only as short code comments or compact 3-line entries in `DECISION_LOG.md`.
- Until that gate is met, companion model infrastructure remains frozen and only governance/reporting updates are allowed.
- Operational unblock condition: at least 50 directional alerts must be resolved as `hit` or `miss`.

## Living Architecture Scope

- Active architecture sources are `CLAUDE.md` and this file (`docs/contracts.md`).
- All other architecture documents under `docs/` are historical artifacts in `docs/archive/`.

## Core-Path Target Architecture (D-109, 2026-03-25)

Target structure for active product development:

```text
app/
├── core/           # settings, domain, enums, errors, logging, schema runtime
├── ingestion/      # RSS adapters, scheduler, classifier, resolver
├── normalization/  # cleaners, deduplication, entity normalization helpers
├── analysis/       # pipeline, keywords, scoring, providers, narratives, briefs, watchlists
├── signals/        # signal generator, candidates, signal models
├── alerts/         # alert service, thresholding, channels
├── pipeline/       # orchestration entrypoint (fetch -> persist -> analyze -> alert)
├── storage/        # DB models, repositories, migrations
├── api/            # FastAPI routers
├── cli/            # canonical operator CLI surface
├── orchestrator/   # trading loop and orchestration-level journals
├── execution/      # paper/backtest engines and portfolio read
├── risk/           # risk engine
├── market_data/    # market-data adapters
└── security/       # SSRF and validation utilities
```

Convergence actions accepted in D-109:
- `enrichment` logic is consolidated into `normalization` (compatibility shims remain).
- runtime schema validation is consolidated into `core` (compatibility shims remain).
- decision journal implementation is consolidated into `orchestrator` (compatibility shims remain).

Boundary rules:
- `pipeline/` is the only module that directly calls `collect_rss_feed()`.
- `signals/` must not depend on `alerts/` or `storage/`.
- `alerts/` must not depend on ingestion adapters or provider integrations.
- `analysis/` must not depend on `alerts/` or `execution/`.

Measurability requirements per pipeline run:
- fetched documents
- persisted documents
- analyzed documents
- priority distribution
- alerts fired


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

Implementation note:
- Runtime code and tests are the source of truth for behavior.
- Historical sprint wording in this file is informational only.

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

### 11. Research and Signal Utility Contracts (archived)

Detailed legacy utility-contract text was moved to [contracts_archive.md](archive/contracts_archive.md) (D-109, 2026-03-25).

Active runtime scope for this layer:
- Watchlist source remains `monitor/watchlists.yml`.
- Signal extraction remains in `app/core/signals.py` and `app/signals/*`.
- Read-only API endpoints remain `GET /research/brief` and `GET /research/signals`.
- No canonical `research` CLI subgroup exists in the active surface.

---

### §12–§15: Companion-ML Contracts (archived)

> **Moved to** [contracts_archive.md](archive/contracts_archive.md) **(D-108, 2026-03-25)**
>
> Sections §12 (Provider-Independent Intelligence), §13 (Intelligence Layer),
> §14 (Dataset Export), §15 (Winner-Traceability) describe frozen companion-ML
> infrastructure under strategic hold D-97. Not part of the active production path.

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
| | **I-14..I-93: Companion-ML invariants — archived (D-108).** See [contracts_archive.md](archive/contracts_archive.md) |
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
