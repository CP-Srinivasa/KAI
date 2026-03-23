# Contracts and Core Data Models

## Current State (2026-03-23)

| Field | Value |
|---|---|
| current_phase | `PHASE 4 (active)` |
| current_sprint | `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)` |
| next_required_step | `PH4G_CLOSE_AND_PH4H_POLICY_REVIEW` |
| baseline | `1538 passed, ruff clean` |
| active_contracts | §75 (PH4G, execution complete; ready to close) · §74–§67 (closed) |
| cli_canonical_count | 53 (frozen §65) |

## Navigation

| Section | Content | Status |
|---|---|---|
| [§76 PH4H Rule-Only Ceiling & Actionability Policy Review](#s76-ph4h-rule-only-ceiling-and-actionability-policy-review) | Review-only policy sprint: I-13 ceiling vs actionability in fallback path | candidate (not active) |
| [§75 PH4G Fallback Input Enrichment Baseline](#s75-ph4g-fallback-input-enrichment-baseline) | Narrow fallback-path enrichment for PH4F top-3 field gaps | active (ready to close) |
| [§74 PH4F Rule Input Completeness Audit](#s74-ph4f-rule-input-completeness-audit) | Diagnostic audit of missing rule-input fields on paired documents | closed (D-68) |
| [§73 PH4E Scoring Calibration Audit](#s73-ph4e-scoring-calibration-audit) | Diagnostic per-field scoring audit; divergence cluster analysis | closed (D-67) |
| [§72 Phase 4 Interim Review](#s72-phase-4-interim-review) | Review PH4A–PH4D arc; select next Phase-4 sprint | closed (D-65/D-66) |
| [§71 PH4D Targeted Keyword Expansion Baseline](#s71-ph4d-targeted-keyword-expansion-baseline) | Targeted keyword expansion for 3 confirmed gap categories | closed (D-68) |
| [§70 PH4C Rule Keyword Coverage Audit](#s70-ph4c-rule-keyword-coverage-audit) | Diagnostic keyword coverage audit contract | closed |
| [§69 PH4B Results Review](#s69-ph4b-results-review) | PH4B review gate contract | closed |
| [§68 PH4B Tier3 Coverage Expansion](#s68-ph4b-tier3-coverage-expansion) | Overlap-first Tier-3 coverage expansion contract | closed |
| [§67 PH4A Signal Quality Audit Baseline](#s67-ph4a-signal-quality-audit-baseline) | Phase-4 first sprint baseline contract | closed |
| [§66 S50D Doc Hygiene](#s50d-doc-hygiene) | Structure rules, split/trim plan | closed/frozen |
| [§65 S50C CLI Contract Freeze](#s50c-cli-contract-freeze) | 53-command canonical list | frozen |
| [§64 S50B CLI Governance](#s50b-cli-governance) | Classification decisions, D-29 | closed |
| [§63 S50A Canonical Inventory](#s50a-canonical-inventory) | Inventory contract, I-384–388 | closed |
| [§§38–62 Historical Archive](#historical-archive) | Phase 1–2 sprint contracts | closed |
| [Core Contracts](#core-contracts) | Invariants, security, domain models | permanent |

## Split/Trim Plan (S50D.3 — frozen 2026-03-22)

**Approach: additive-only restructuring. No § renumbering. No file splits. No deletions.**

### Reference preservation rules
- All existing § numbers (§38–§66) remain unchanged permanently.
- Cross-references in AGENTS.md, ASSUMPTIONS.md, RUNBOOK.md, ONBOARDING.md remain valid.
- Historical sprint sections (§38–§62) retain full content; no collapse or deletion.

### Structural additions (applied in this session)
1. Current-state table at top (done above).
2. Navigation table at top linking to active and historical sections (done above).
3. Section divider `## Phase-3 Active Contracts (§§63–66)` inserted before §63.
4. Section divider `## Phase 1–2 Historical Contract Archive (§§38–62)` inserted before §38.
5. Core invariants section label `## Core Contracts and Invariants (permanent)` confirmed at line ~23.

### No-touch zones
- All § content bodies — no edits to any contract text, invariant, or decision.
- §§63–66 active section bodies — currently authoritative, must not be structurally disrupted.
- Any line containing `I-NNN` invariant references — structural-only context preserved.

### Out of scope for S50D
- File splits (contracts_archive.md etc.) — deferred to future sprint if needed.
- § renumbering — never; would break all cross-references.
- Semantic contract content changes — I-395 strictly enforced.

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
    url: str                        # required â€” canonical item URL
    external_id: str | None = None  # source-assigned ID (RSS guid, API id, â€¦)
    title: str | None = None        # raw title from source
    content: str | None = None      # raw body text or excerpt
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)  # source extras
```

**Conversion**: `normalize_fetch_item(item, *, source_id, source_name, source_type) -> CanonicalDocument`

Rules:
- **No analysis** â€” no scores, no sentiment, no priority, no tickers, no entity mentions
- **No persistence state** â€” no `status`, `is_analyzed`, `is_duplicate`, `content_hash`, `id`
- **No source metadata** â€” `source_id`, `source_name`, `source_type` are injected by `normalize_fetch_item()`, never by the adapter
- As close to the source as possible â€” minimal transformation before `normalize_fetch_item()`
- `metadata` is a free-form bag for source-specific extras (image URL, author, feed tags, â€¦)

Implementation: Adapters create `FetchItem` internally, then call `normalize_fetch_item()` to
convert to `CanonicalDocument`. `FetchResult.documents` carries `list[CanonicalDocument]` by design.
Normalization is adapter-owned â€” it must NOT move into `persist_fetch_result()`, which is a
storage helper and must not contain source-type-specific transformation logic.

---

### 1. FetchResult

Represents raw ingestion output.

```python
@dataclass
class FetchResult:
    source_id: str
    documents: list[CanonicalDocument]  # never None â€” empty list on failure
    fetched_at: datetime
    success: bool
    error: str | None = None            # set when success=False
    metadata: dict[str, Any] = field(default_factory=dict)
```

Rules:
- adapter must never raise â€” catch all exceptions internally
- `success=False` + `error=<message>` on any failure
- `documents=[]` on failure (never None)
- every document must have: `url`, `title`, `source_id`, `source_name`, `source_type`
- `content_hash` must not be set by adapter â€” auto-computed by `CanonicalDocument`
- SSRF check (`validate_url()`) must run before any HTTP request

---

### 2. CanonicalDocument

The central data unit. Every document in the system is represented as a `CanonicalDocument`.

```python
class CanonicalDocument(BaseModel):
    id: UUID                            # primary key â€” never change after persist
    url: str                            # required â€” dedup key
    title: str                          # required
    raw_text: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime                # auto-set
    source_id: str | None = None
    source_name: str | None = None
    source_type: SourceType | None = None
    document_type: DocumentType         # ARTICLE / PODCAST_EPISODE / ...
    content_hash: str | None = None     # auto-computed â€” never set manually
    status: DocumentStatus              # lifecycle state â€” see below
    is_duplicate: bool                  # sync with status=DUPLICATE
    is_analyzed: bool                   # sync with status=ANALYZED
    # ... analysis scores, entity lists, metadata
```

Rules:
- `content_hash` is auto-computed from `url|title|raw_text` â€” never set manually
- `word_count` is a `@computed_field` â€” never stored in DB
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
    relevance_score: float              # [0.0, 1.0] â€” blended with keyword hits by apply_to_document()
    impact_score: float                 # [0.0, 1.0]
    novelty_score: float                # [0.0, 1.0]
    confidence_score: float             # [0.0, 1.0] â€” in-memory only, NOT persisted to DB
                                        # DB stores credibility_score = 1.0 - spam_probability

    market_scope: MarketScope | None
    affected_assets: list[str]
    affected_sectors: list[str]
    event_type: str | None

    explanation_short: str              # required â€” concise reasoning
    explanation_long: str               # required â€” full reasoning

    actionable: bool
    tags: list[str]
    spam_probability: float = 0.0       # stored for audit; ALWAYS pass separately to compute_priority()
    recommended_priority: int | None    # set by apply_to_document() after scoring
```

Rules:
- Must be fully populated â€” all score fields are required (no optional scores)
- Must be schema-validated â€” all ranges enforced by Pydantic
- Must not contain provider-specific fields (`provider`, `model`, `raw_output` removed)
- `AnalysisResult` is the provider-agnostic analysis contract for deterministic fallback,
  internal companion analysis, and external provider analysis
- `spam_probability` IS stored on `AnalysisResult` for audit â€” but scoring functions
  (`compute_priority`, `is_alert_worthy`) receive it as an **explicit separate parameter**
- `recommended_priority` is set by `apply_to_document()` after `compute_priority()` runs â€” not by the LLM
- `AnalysisResult` is in-memory only â€” no separate DB table
- scores are written back to `canonical_documents` via `repo.update_analysis(document_id, result)`

---

### 4. Document Lifecycle

```
pending â†’ persisted â†’ analyzed
         â†˜ failed
         â†˜ duplicate
```

| Status | Meaning | Owner |
|---|---|---|
| `pending` | in-memory only â€” not yet saved to DB | `prepare_ingested_document()` in `document_ingest.py` |
| `persisted` | saved to DB, awaiting analysis | `DocumentRepository.save_document()` |
| `analyzed` | scores written, pipeline complete | `DocumentRepository.update_analysis()` |
| `failed` | non-recoverable error â€” kept for audit | `repo.update_status(FAILED)` â€” ingest, `run_rss_pipeline()`, and `analyze_pending` CLI error handlers |
| `duplicate` | blocked at dedup gate â€” NOT saved | detected in-memory; `repo.mark_duplicate()` for retroactive marking |

Important: `DUPLICATE` and `FAILED` at the ingest stage are **in-memory states**.
Documents detected as duplicates by `persist_fetch_result()` are silently dropped (never saved to DB).
`status=DUPLICATE` is only written to DB when `repo.mark_duplicate()` is called explicitly
on an already-persisted document.

Rules:
- transitions are one-way â€” no rollback, no recycling
- `is_analyzed=True` must always be set together with `status=analyzed`
- `is_duplicate=True` must always be set together with `status=duplicate` (only when persisted)
- a document's status is always `pending` before any DB operation

---

### 5. Layer Boundaries

Every layer has a defined input and output. No layer may bypass another.

| Boundary | Rule |
|---|---|
| Ingestion â†’ Storage | adapter returns `FetchResult`; only `persist_fetch_result()` persists |
| Storage â†’ Analysis | `repo.get_pending_documents()` feeds the analysis queue â€” filters `status=PERSISTED` (not just flags) |
| Analysis â†’ Storage | `apply_to_document()` then `repo.update_analysis()` â€” no other path |
| Analysis â†’ Alerting | `is_alert_worthy()` is the only gate â€” no direct score access |
| LLM calls | always via `BaseAnalysisProvider.analyze()` â€” never direct SDK calls |
| Config | always via `AppSettings` â€” never `os.environ` directly |

---

### 6. Priority Score

```
raw = (relevance Ã— 0.30) + (impact Ã— 0.30) + (novelty Ã— 0.20)
    + (actionable Ã— 0.15) + ((1 - spam) Ã— 0.05)

priority = round(raw Ã— 9) + 1          # maps [0.0, 1.0] â†’ [1, 10]

# Actionability bonus: +1 if result.actionable is True (and priority < 10)
if actionable:
    priority = min(10, priority + 1)
```

Cap: if `spam_probability > 0.7` â†’ `priority = min(priority, 3)` (applied after bonus)

Scale:
- 8â€“10: high urgency, actionable
- 6â€“7: notable, alert-worthy
- 4â€“5: background, low urgency
- 1â€“3: noise or spam

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
- `run()` input is always `CanonicalDocument` â€” never a raw dict or ORM model
- `run()` output is always `PipelineResult` â€” never raises (errors surfaced in `result.error`)
- No direct DB writes inside `AnalysisPipeline` or `PipelineResult`
- `apply_to_document()` is the only point where scores and entities are written back to the document
- `llm_output` is optional; `analysis_result` is the required downstream contract for a successful run
- absence or failure of an external provider must degrade to a valid fallback-compatible analysis result,
  not an empty pipeline outcome
- `run_batch()` is concurrency-bounded by `_MAX_CONCURRENT`

---

### 8. Scoring Contract

Scoring is part of the pipeline result â€” not a separate side-effect.

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
3. title hash match (score 0.85 â€” catches same headline across sources)

Rules:
- conservative by default â€” prefer false negatives over false positives
- `is_duplicate()` never writes to DB â€” read-only
- dedup is enforced exclusively by `document_ingest.py` before `repo.save_document()`
- `filter_scored()` is used by `persist_fetch_result()` â€” returns all docs with scores for auditing
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
- every provider must return a fully validated `LLMAnalysisOutput` â€” never a raw dict (I-7)
- `analyze()` never receives a `CanonicalDocument` directly â€” caller extracts `title` + `text`
- providers are replaceable without touching pipeline logic
- structured output enforcement is provider-specific (OpenAI: `response_format`, Anthropic: tool-use,
  Gemini: `response_schema`) â€” but the output contract is identical
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
- never trust provider output blindly â€” schema validation is mandatory

---

### 11. Sprint 4 â€” Research & Signal Contracts

These contracts define the Sprint 4 output layer. All three types are **in-memory only** â€”
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
- Source: `monitor/watchlists.yml` â€” loaded via `WatchlistEntry` + `load_watchlist()`
- Sections: `crypto`, `equities`, `etfs`, `macro`, `persons`, `topics`, `domains`
- Tag lookup is case-insensitive
- `filter_documents()` is the primary document-to-watchlist matching path
- `WatchlistRegistry` is read-only after construction â€” no mutations during runtime
- `load_watchlist()` returns `[]` (not an error) if the file does not exist
- `find_by_text()` â€” Sprint 4B planned, not yet implemented; use `filter_documents()` instead

---

#### 11b. ResearchBrief

```python
class BriefFacet(BaseModel):
    name: str
    count: int

class BriefDocument(BaseModel):
    document_id: str          # str(CanonicalDocument.id) â€” traceability
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
- Input: `list[CanonicalDocument]` â€” only `is_analyzed=True` docs are used
- `ResearchBriefBuilder.build()` never raises â€” returns empty brief on empty/unanalyzed input
- `_ACTIONABLE_PRIORITY_THRESHOLD = 8` â€” must stay in sync with `ThresholdEngine.min_priority`
- Sorted by (priority_score, impact_score, published_at) descending
- `to_markdown()` and `to_json_dict()` are the only output serialization paths
- `ResearchBrief` is in-memory only â€” no DB table, no persistence

---

#### 11c. SignalCandidate

```python
class SignalCandidate(BaseModel):
    model_config = ConfigDict(strict=True, validate_assignment=True)

    signal_id: str              # f"sig_{document_id}" â€” deterministic
    document_id: str            # str(CanonicalDocument.id) â€” traceability

    target_asset: str           # primary asset ("BTC", "ETH", "General Market")
    direction_hint: str         # "bullish" | "bearish" | "neutral"
                                # NEVER "buy" / "sell" / "hold" â€” not an execution instruction
    confidence: float           # proxy: doc.relevance_score â€” [0.0, 1.0]
    supporting_evidence: str    # doc.summary or doc.title
    contradicting_evidence: str # static note â€” not extracted in primary scan
    risk_notes: str             # spam_prob + market_scope metadata
    source_quality: float       # doc.credibility_score â€” [0.0, 1.0]
    recommended_next_step: str  # always ends with "â€” human decision required."

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
- `priority >= 8` is a hard constraint â€” Pydantic `Field(ge=8)` enforced at construction
- `direction_hint` is research language, NOT trading instruction â€” "bullish"/"bearish"/"neutral"
- `signal_id` is deterministic: `f"sig_{document_id}"` â€” idempotent for same document
- `watchlist_boosts`: `{"BTC": 1}` raises effective priority by 1 for watchlist assets;
  capped at 10; never raises above 10
- `confidence_score` from `AnalysisResult` is NOT persisted to DB â€” `relevance_score` is
  used as the confidence proxy (available in DB)
- `SignalCandidate` is in-memory only â€” no DB table, no persistence
- `extract_signal_candidates()` never raises â€” returns `[]` if no candidates qualify

---

#### 11d. Research Layer Boundaries

| Boundary | Rule |
|---|---|
| Input gate | Only `CanonicalDocument` with `is_analyzed=True` enters research layer |
| No DB writes | `ResearchBrief` and `SignalCandidate` are always in-memory â€” never persisted |
| No LLM calls | Research layer is pure computation â€” no provider calls, no external I/O |
| Watchlist source | Always from `monitor/watchlists.yml` via `WatchlistRegistry.from_monitor_dir()` |
| CLI entry point | `research` Typer subgroup â€” `watchlists`, `brief`, `signals` commands |
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

**Status: âœ… Implemented** (`app/core/settings.py`)

```python
companion_model_endpoint: str | None = None      # e.g. "http://localhost:11434"
companion_model_name: str = "kai-analyst-v1"
companion_model_timeout: int = 10                # seconds
```

Security constraint: `companion_model_endpoint` MUST be `localhost` or an explicitly allowlisted
internal address. Field validator rejects external URLs at settings load time.

---

#### 13b. Factory Routing

**Status: âœ… Implemented** (`app/analysis/factory.py`)

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

`EnsembleProvider` (`app/analysis/ensemble/provider.py`) is not a factory target â€” it wraps
multiple providers directly. Its `provider_name` is a compound string like
`"ensemble(openai,internal)"` (see Â§13e on EnsembleProvider and analysis_source).

---

#### 13c. AnalysisSource Enum

**Status: âœ… Implemented** (`app/core/enums.py`, `app/core/domain/document.py`)

```python
# app/core/enums.py
class AnalysisSource(StrEnum):
    RULE = "rule"                  # Tier 1 â€” fallback / rule-based heuristics
    INTERNAL = "internal"          # Tier 2 â€” InternalModelProvider or InternalCompanionProvider
    EXTERNAL_LLM = "external_llm"  # Tier 3 â€” OpenAI / Anthropic / Gemini
```

**Current implementation**:
- `CanonicalDocument.analysis_source: AnalysisSource | None` exists as an explicit field
- `AnalysisResult.analysis_source: AnalysisSource | None` exists as an explicit field
- `canonical_documents.analysis_source` is a persisted DB column (migration `0006`)
- `CanonicalDocument.effective_analysis_source` remains the backward-compatible accessor for legacy rows

```python
# app/core/domain/document.py â€” compatibility accessor
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
- Distillation corpus selects ONLY `analysis_source=EXTERNAL_LLM` documents (Â§14e)

---

#### 13d. Companion Model Output Scope

**Status: âœ… Implemented** (`app/analysis/providers/companion.py`)

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
field (Â§14c). The internal reasoning trace is not part of the training corpus output format.

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
   starts with `"ensemble("` to `INTERNAL`. This is the primary guard â€” it sets
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
| openai won | `"ensemble(openai,internal)"` | `"internal"` âš ï¸ (conservative) | `"external_llm"` âœ… |
| internal won | `"ensemble(openai,internal)"` | `"internal"` âœ… (correct) | `"internal"` âœ… |

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
    {"role": "assistant", "content": "<JSON target scores â€” sorted keys>"}
  ],
  "metadata": {
    "document_id":     "<uuid â€” str>",
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
| `affected_assets` | list[str] | âœ… | deduplicated from `doc.tickers + doc.crypto_assets` |
| `impact_score` | float | âœ… | 0.0 .. 1.0 |
| `market_scope` | str | âœ… | e.g. `"crypto"` / `"etf"` / `"unknown"` |
| `novelty_score` | float | âœ… | 0.0 .. 1.0 |
| `priority_score` | int | âœ… | 1 .. 10 |
| `relevance_score` | float | âœ… | 0.0 .. 1.0 |
| `sentiment_label` | str | âœ… | `"bullish"` / `"bearish"` / `"neutral"` |
| `sentiment_score` | float | âœ… | -1.0 .. 1.0 |
| `spam_probability` | float | âœ… | 0.0 .. 1.0 |
| `summary` | str | âœ… | `doc.summary` or `""` |
| `tags` | list[str] | âœ… | `doc.ai_tags` |

All fields are always present (no optional fields in the assistant target).

---

#### 14c. `co_thought` â€” Final Decision: REMOVED

**`co_thought` is NOT part of the export format.**

This field was considered during Sprint 5A design. Final rationale for removal:

1. **Contamination risk**: Rule-based analysis sets `explanation_short = "Rule-based fallback
   analysis. ..."` â€” a heuristic label, not reasoning. Including it as chain-of-thought
   training signal would teach the companion model a placeholder, not financial reasoning.

2. **Inconsistent quality**: Even LLM-sourced `explanation_short` values vary in quality and
   depth. The field is a brief annotation, not a structured reasoning trace.

3. **Schema coupling**: `co_thought` would couple the export to `doc.metadata["explanation_short"]`
   â€” an implementation detail, not a stable contract field.
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
| `"external_llm"` | External LLM (OpenAI, Anthropic, Gemini) | `"openai"`, `"anthropic"`, `"gemini"`, etc. | âœ… yes |
| `"internal"` | Tier 2 analysis (heuristic or companion HTTP) | `"internal"`, `"companion"` | âš ï¸ evaluation only |
| `"rule"` | Rule-based / fallback analysis | `None`, `"fallback"`, `"rule"` | âŒ no (I-19) |

**Source of truth**: exported `metadata["analysis_source"]`, produced from
`doc.effective_analysis_source` (`CanonicalDocument`, `app/core/domain/document.py`).

```python
# Backward-compatible accessor (current implementation):
doc.effective_analysis_source
# â†’ returns doc.analysis_source if explicitly set (Sprint 5B field)
# â†’ falls back to derivation from doc.provider (legacy path for pre-Sprint-5B rows)
```

The export reads `doc.effective_analysis_source.value` â€” never derives analysis_source inline.

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
- `doc.provider` â€” may be `"openai"` (teacher), `"internal"` (not teacher), or a legacy
  composite `"ensemble(openai,internal)"` â€” ambiguous without `analysis_source`
- `doc.metadata["ensemble_chain"]` â€” audit trail only, not a classification signal
- Any other metadata field

The **only valid teacher filter** is `metadata["analysis_source"] == "external_llm"`.
This ensures no ensemble composition detail can bypass I-16 or I-19.

---

#### 14f. provider vs analysis_source â€” Contract Separation

These are two distinct concepts that must never be conflated:

| Concept | Field | Type | Persistence | Purpose |
|---------|-------|------|-------------|---------|
| `provider` | `doc.provider` | `str \| None` | DB column | Technical engine name. Pre-5C: `"openai"`, `"internal"`, `"ensemble(openai,internal)"`, `"fallback"`. Post-5C: always the **winner name** â€” never a composite string. |
| `analysis_source` | `doc.analysis_source` | `AnalysisSource` enum | DB column (migration 0006) | Semantic tier: `RULE` / `INTERNAL` / `EXTERNAL_LLM` â€” stable, use this for filtering. |
| `ensemble_chain` | `doc.metadata["ensemble_chain"]` | `list[str]` | JSON metadata | Full ordered provider list when `EnsembleProvider` was used. Set by Sprint-5C. Legacy rows: absent. |

**Rules:**
- `provider` is a technical string â€” never use it directly for corpus filtering or tier decisions
- `analysis_source` is the stable semantic value â€” always use this for filtering and guardrails
- `provider` semantics changed in Sprint-5C: composite `"ensemble(...)"` strings are legacy only
- `analysis_source` must NEVER be set manually â€” always set by pipeline at result creation time
- Downstream code (ResearchBrief, SignalCandidate, alerts) consumes analysis results via the same
  `CanonicalDocument` contract regardless of which tier produced them â€” no branching on `provider`

**Companion model in research outputs:**
Companion-analyzed documents (`analysis_source=INTERNAL`) flow through the same research pipeline:
- `ResearchBrief.key_documents` â€” âœ… included
- `ResearchBrief.top_actionable_signals` â€” âœ… if `priority >= 8`
- `SignalCandidate` â€” âœ… if `priority >= 8` (companion can reach 8 with strong output)
- Alert gating â€” âœ… same `ThresholdEngine.is_alert_worthy()` path

No parallel models, no second result format. Provenance is tracked via `analysis_source` only.

---

## Final Rule

These contracts define the system.

If they become inconsistent with the code, the system becomes unstable.

**Protect them. Update them. Never bypass them.**

---

### 15. Sprint-5C â€” Winner-Traceability Contract

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
`doc.analysis_source = INTERNAL` and `doc.provider = "ensemble(openai,internal)"` â€”
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
    """Return the actual winner name after analyze() â€” for EnsembleProvider via duck typing."""
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
pipelines â€” `_resolve_runtime_provider_name` returns the winner name instead. The composite
guard remains in `CanonicalDocument.effective_analysis_source` for legacy DB rows only (Â§15e).

**Pipeline call site** (success path):

```python
# trace_metadata resolved before analyze() â€” provider_chain doesn't change
trace_metadata = _resolve_trace_metadata(self._provider)   # {"ensemble_chain": [...]} or {}

llm_output = await self._provider.analyze(title=..., text=..., context=...)

# winner name resolved AFTER analyze() â€” active_provider_name updated by EnsembleProvider
provider_name = _resolve_runtime_provider_name(self._provider) or self._provider.provider_name
analysis_source = _resolve_analysis_source(provider_name)   # I-24
```

**Error path** (except branch):

```python
except Exception as exc:
    # analysis_source = RULE (set by _build_fallback_analysis, always â€” I-13)
    # provider_name stays "fallback" (initialized at top of run())
    # _resolve_runtime_provider_name() is not called in the error path
    analysis_result = self._build_fallback_analysis(...)
```

The error path never performs winner resolution. `analysis_source=RULE` always when analysis
failed â€” regardless of which provider was configured.

---

#### 15c. doc.provider â€” winner name, not composite

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
| No provider configured | `"fallback"` | `RULE` | âŒ never |
| Provider call failed â†’ fallback | `"fallback"` | `RULE` | âŒ never |
| InternalModelProvider ran | `"internal"` | `INTERNAL` | âš ï¸ eval only |
| InternalCompanionProvider ran | `"companion"` | `INTERNAL` | âš ï¸ eval only |
| OpenAI ran (direct) | `"openai"` | `EXTERNAL_LLM` | âœ… teacher |
| Ensemble: openai won | `"openai"` (from `ensemble.model`) | `EXTERNAL_LLM` | âœ… teacher |
| Ensemble: internal fallback | `"internal"` (from `ensemble.model`) | `INTERNAL` | âš ï¸ eval only |
| Ensemble: companion fallback | `"companion"` (from `ensemble.model`) | `INTERNAL` | âš ï¸ eval only |

---

#### 15e. Backward compatibility

- Pre-Sprint-5C rows: `doc.provider` may be `"ensemble(openai,internal)"`.
  `effective_analysis_source` maps `startswith("ensemble(")` â†’ `INTERNAL` (conservative).
  These rows are NOT upgraded automatically. The conservative mapping is intentional.
- New rows (Sprint-5C+): `doc.provider` is always the winner name. The `ensemble_chain`
  metadata key is present if an `EnsembleProvider` was used.

---

#### 15f. Non-ensemble providers: no change

For `OpenAIAnalysisProvider`, `AnthropicAnalysisProvider`, `GeminiAnalysisProvider`,
`InternalModelProvider`, `InternalCompanionProvider` used directly (not via ensemble):

- `provider.model` is the **model identifier** (e.g. `"gpt-4o"`, `"rule-heuristic-v1"`),
  **not** the provider name.
- The pipeline uses `provider.provider_name` for `doc.provider` â€” unchanged.
- `_resolve_analysis_source()` logic (provider-object-based, pre-analyze) â€” unchanged.

Only `EnsembleProvider` triggers post-analyze winner resolution (I-24).

---

#### 15g. End-to-End Provenance Flow (post Sprint-5C)

This trace documents the full lifecycle of provenance from ingestion to research outputs.
Every downstream consumer relies on `doc.analysis_source` â€” never on `doc.provider`.

```
1. Ingestion
   doc.provider       = None
   doc.analysis_source = None
   doc.status          = PERSISTED

2. analyze_pending â†’ AnalysisPipeline.run(doc)
   Pre-analyze:
     trace_metadata = _resolve_trace_metadata(ensemble)
       â†’ {"ensemble_chain": ["openai", "internal"]}  (provider_chain property)

3. await ensemble.analyze(title, text, context)
   EnsembleProvider iterates providers in order:
     â†’ tries openai.analyze()   â† succeeds
     â†’ (internal.analyze() never called)
   ensemble._active_provider_name = "openai"   â† winner recorded (I-23)

   Fallback scenario (openai fails):
     â†’ tries openai.analyze()   â† raises RuntimeError
     â†’ tries internal.analyze() â† succeeds
     ensemble._active_provider_name = "internal"
     analysis_source (post-5C) â†’ INTERNAL âœ… (correct, internal ran)

4. Post-analyze (Sprint-5C, success path)
   provider_name   = _resolve_runtime_provider_name(ensemble)
                   = ensemble.active_provider_name â†’ "openai"
   analysis_source = _resolve_analysis_source("openai")  â†’ EXTERNAL_LLM (I-24)

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
   doc.effective_analysis_source â†’ returns doc.analysis_source  â†’ EXTERNAL_LLM

9. export_training_data(doc)
   metadata["analysis_source"] = effective_analysis_source.value  â†’ "external_llm"
   â†’ teacher-eligible âœ… (I-16, I-19 satisfied)

10. extract_signal_candidates(doc)
    signal.analysis_source = effective_analysis_source.value  â†’ "external_llm"

11. ResearchBriefBuilder._to_brief_document(doc)
    brief_doc.analysis_source = effective_analysis_source.value  â†’ "external_llm"
```

**Consistency invariant**: All consumers in steps 9â€“11 read `doc.effective_analysis_source`.
If `doc.analysis_source` is set (post-pipeline), that value is returned directly.
If not set (legacy pre-5B row), the property derives from `doc.provider` conservatively.
This guarantees no consumer ever branches on `provider` for tier decisions.

**Error-path scenario** (all ensemble providers fail â†’ RuntimeError re-raised):
```
3'. All providers fail â†’ pipeline except branch â†’ _build_fallback_analysis()
    analysis_source  = RULE   (set by fallback builder, always)
    provider_name    = "ensemble(openai,internal)"  (unchanged, composite â€” pre-5C legacy)
    doc.analysis_source = RULE after apply_to_document()
    â†’ teacher-ineligible âœ… (RULE never teacher â€” I-19)
    â†’ effective_analysis_source returns RULE (analysis_source is set)
```
`_resolve_analysis_source_from_winner()` is never called in the error path (I-24).

**Legacy rows** (pre-Sprint-5C, where `doc.provider = "ensemble(openai,internal)"`):
- `doc.analysis_source` may be `None` or `INTERNAL`
- `effective_analysis_source` returns `INTERNAL` (conservative)
- These rows are NOT corpus-eligible even if `openai` had won â€” intentional tradeoff (I-26)

---

---

### 16. Sprint-6 â€” Distillation Corpus Safety + Evaluation Baseline

**Status: âœ… Implemented (Sprint 6)**

Implemented in this sprint:
- `export_training_data(teacher_only=True)` â€” function-level teacher guard (I-27) âœ…
- `compare_datasets()` - JSONL-based offline evaluation harness with actionable metrics and promotion gate support
- `EvaluationMetrics` / `EvaluationReport` dataclasses âœ…
- `load_jsonl()` helper âœ…
- 19 new tests covering all modes and edge cases âœ…

---

#### 16a. Teacher-Eligibility at Function Level âœ…

After Sprint-5C, `analysis_source=EXTERNAL_LLM` is written correctly for all new analyzed documents.
Sprint-6 closes the direct-API-caller gap by adding `teacher_only=True` at function level.

**Current safety coverage:**

| Call path | Teacher filter applied? |
|---|---|
| `research dataset-export --source-type external_llm` (CLI) | âœ… before calling `export_training_data()` |
| `export_training_data(docs, path)` (direct API call, default) | âœ… no filter â€” caller responsible (unchanged) |
| `export_training_data(docs, path, teacher_only=True)` | âœ… function-level strict guard (I-27) |

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

**Default `teacher_only=False`** â€” backward-compatible. Existing callers unaffected.

**Current repo status**:
- `export_training_data(..., teacher_only=True)` is implemented and is the canonical strict guard
- CLI hook-up via `dataset-export --teacher-only` is implemented in
  [app/cli/main.py](C:/Users/sasch/.local/bin/ai_analyst_trading_bot/app/cli/main.py)

---

#### 16c. Corpus Integrity Guarantee (post Sprint-5D)

With `teacher_only=True`, the following must hold for any input:

| Input document | Exported? |
|---|---|
| `analysis_source=EXTERNAL_LLM`, `is_analyzed=True`, has text | âœ… yes |
| `analysis_source=INTERNAL`, `is_analyzed=True` | âŒ no |
| `analysis_source=RULE`, `is_analyzed=True` | âŒ no |
| `analysis_source=None` (legacy), `doc.provider="openai"` | âŒ no (conservative â€” explicit field required) |
| `is_analyzed=False` | âŒ no (existing guard) |
| no text | âŒ no (existing guard) |

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

This establishes the **floor gap** â€” how far the rule-based fallback diverges from external LLM output.
Sprint-6 will add a second comparison: rule-baseline vs companion model (real Ollama inference).

`EvaluationResult` fields defined and stable â€” no changes needed for Sprint-5D.
`compare_outputs()` signature is stable â€” no changes needed for Sprint-5D.

The evaluation command MUST NOT call any external API. All inference in Sprint-5D is offline.

---

#### 16f. Sprint-6 Acceptance Criteria âœ…

All conditions satisfied in Sprint 6:

1. âœ… `export_training_data(docs, path, teacher_only=True)` skips RULE and INTERNAL docs
2. âœ… `export_training_data(docs, path)` (default, no flag) â€” unchanged behavior
3. âœ… `export_training_data(docs, path, teacher_only=True)` with legacy row (`analysis_source=None`) â†’ skipped (strict mode)
4. âœ… CLI `dataset-export --teacher-only` flag passes `teacher_only=True`
5. âœ… `pytest` passes (22 tests in test_datasets.py + test_evaluation.py, 547 total)
6. âœ… `ruff check .` clean
7. âœ… `compare_outputs()` and `research evaluate` CLI unchanged and passing
8. âœ… `research evaluate-datasets` wraps `load_jsonl()` + `compare_datasets()` and handles missing or empty files defensively

---

### 17. Sprint-6 â€” Dataset Construction, Evaluation Harness, Distillation Readiness

**Status: âœ… Architecture, core harness, and CLI hooks complete.**

Core implementation in `app/research/datasets.py` and `app/research/evaluation.py`.
Full CLI spec: [docs/dataset_evaluation_contract.md](./dataset_evaluation_contract.md).

Sprint 6 defines three dataset roles and one offline evaluation harness:
- teacher-only dataset export (`teacher_only=True`) âœ…
- internal benchmark export (CLI `--source-type internal`) âœ…
- rule baseline export (CLI `--source-type rule`) âœ…
- dataset-to-dataset evaluation by `document_id` (`compare_datasets()` + `research evaluate-datasets`) âœ…
- companion benchmark CLI wrapper (`research benchmark-companion`) âœ…
- structured benchmark persistence hooks (`save_evaluation_report()` + `save_benchmark_artifact()`) âœ…

Mandatory role mapping:
- `analysis_source=external_llm` â†’ teacher-only dataset (fine-tuning eligible)
- `analysis_source=internal` â†’ internal benchmark (evaluation only, never teacher)
- `analysis_source=rule` â†’ rule baseline (floor metrics only, never teacher)

Mandatory metric set (all implemented in `EvaluationMetrics`):
- `sentiment_agreement` â€” fraction of rows with matching sentiment_label
- `priority_mae` â€” mean absolute error on priority_score (1â€“10 scale)
- `relevance_mae` â€” mean absolute error on relevance_score (0.0â€“1.0)
- `impact_mae` â€” mean absolute error on impact_score (0.0â€“1.0)
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
- teacher-only filtering uses `doc.analysis_source` directly (strict, not `effective_analysis_source`) (Â§16b, I-31)
- evaluation matches rows by `metadata["document_id"]` only (I-32)

Remaining non-runtime task: contract acceptance / commit flow.

---

### 18. Sprint-7 â€” Companion Benchmark Harness, Promotion Gate, Artifact Contract

**Status: âœ… Sprint 7 â€” Implemented (benchmark, report, and promotion-gate path)**

Full spec: [docs/benchmark_promotion_contract.md](./benchmark_promotion_contract.md)

Runtime stubs already in `app/research/evaluation.py` (unverified, untested):
- `PromotionValidation` â€” per-gate pass/fail dataclass
- `validate_promotion(metrics)` - checks 6 quantitative promotion thresholds
- `save_evaluation_report()` â€” persists `EvaluationReport` as structured JSON
- `save_benchmark_artifact()` â€” writes companion benchmark manifest

**Three explicit separations (non-negotiable):**

| Concept | What it is | What it is NOT |
|---------|-----------|----------------|
| Benchmark | Run harness, produce report + artifact | Not training, not inference tuning |
| Evaluation | Measure metric gap (MAE / agreement) | Not a promotion decision |
| Promotion | Manual Gated validation (G1-G6) | Not an automated switch/deployment |

---

### 21. Sprint-10 â€” Companion Shadow Run

**Status: âœ… Sprint 10 â€” Implemented**

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
| G1 | `sentiment_agreement` | â‰¥ 0.85 | âœ… Sprint 7 |
| G2 | `priority_mae` | â‰¤ 1.5 | âœ… Sprint 7 |
| G3 | `relevance_mae` | â‰¤ 0.15 | âœ… Sprint 7 |
| G4 | `impact_mae` | â‰¤ 0.20 | âœ… Sprint 7 |
| G5 | `tag_overlap_mean` | â‰¥ 0.30 | âœ… Sprint 7 |
| G6 | `false_actionable_rate` | <= 0.05 | implemented |

**I-34 (automated gate)**: `false_actionable_rate` is computed by `compare_datasets()` on paired teacher/candidate rows and enforced by `validate_promotion()` as gate G6. `actionable_accuracy` remains an audit metric for operator review, but it is not itself a promotion gate.

**Sprint-7 deliverables:**
1. Tests for `validate_promotion()`, `save_evaluation_report()`, `save_benchmark_artifact()` (task 7.1)
2. CLI: `evaluate-datasets --save-report <path> [--save-artifact <path>]` (task 7.2)
3. CLI: `research check-promotion <report.json>` â€” per-gate table, exit 0/1 (task 7.3)

**Constraints (all sprint-7):**
- No training pipeline, no fine-tuning, no weight updates
- No new provider or analysis tier
- No automatic promotion
- Companion remains `analysis_source=INTERNAL` until operator promotion (I-39)

---

### 19. Sprint-8 â€” Controlled Companion Inference, Tuning Artifact Flow, Manual Promotion

**Status: âœ… Sprint 8 â€” controlled companion inference and artifact path implemented**

Full spec: [docs/tuning_promotion_contract.md](./tuning_promotion_contract.md)

New module: `app/research/tuning.py` â€” `TuningArtifact`, `PromotionRecord`,
`save_tuning_artifact()`, `save_promotion_record()`.

**Four explicit separations (non-negotiable):**

| Concept | What it is | What it is NOT |
|---------|-----------|----------------|
| Benchmark | Run harness, produce EvaluationReport | Not tuning, not training |
| Tuning | Record dataset + model base manifest | Not training, not weights |
| Training | External gradient descent (operator runs) | Not in this platform |
| Promotion | Immutable audit record of operator decision | Not automatic, not routing change |

**Sprint-8 deliverables:**
1. `app/research/tuning.py` â€” `TuningArtifact`, `PromotionRecord`, `save_tuning_artifact()`,
   `save_promotion_record()` (task 8.1 + tests)
2. CLI: `research prepare-tuning-artifact <teacher_file> <model_base>` (task 8.2)
3. CLI: `research record-promotion <report_file> <model_id> --endpoint <url> --operator-note <text>` (task 8.3)
4. CLI: `research benchmark-companion-run <teacher.jsonl> <candidate.jsonl>` - local companion inference plus benchmark/report/artifact flow

**Constraints (all sprint-8):**
- No training engine, no external training API calls
- No new provider, no analysis tier change
- No automatic promotion or routing change
- Promotion is reversible by env var only (I-44)
- `operator_note` required â€” operators must explicitly acknowledge (I-43)

---

### 20. Sprint-9 â€” Promotion Audit Hardening

**Status: âœ… Implemented â€” G6, `gates_summary`, and artifact-linkage validation are live**

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

### 21. Sprint-10 â€” Companion Shadow Run

**Status: âœ… Implemented â€” offline shadow, live shadow, and audit persistence are live**

Full spec: [docs/sprint10_shadow_run_contract.md](./sprint10_shadow_run_contract.md)

New module: `app/research/shadow.py` â€” `ShadowRunRecord`, `DivergenceSummary`,
`compute_divergence()`, `write_shadow_record()`, `run_shadow_batch()`.

**Core principle:** Shadow remains purely auditing. The primary result stays authoritative,
and shadow never owns production persistence or routing decisions.

**Five explicit separations (non-negotiable):**

| Concept | What it is | What it is NOT |
|---------|-----------|----------------|
| Primary analysis | `AnalysisPipeline` â†’ `apply_to_document()` â†’ DB | Not shadow |
| Shadow companion result | `InternalCompanionProvider.analyze()` â†’ JSONL only | Not pipeline result |
| Divergence summary | Computed diff, informational | Not a gate, not a signal |
| Shadow JSONL | Standalone audit file | Not EvaluationReport, not training corpus |
| Shadow report CLI | Offline reader for operator review | Not a promotion gate |

**Sprint-10 deliverables:**
1. `app/research/shadow.py` â€” `ShadowRunRecord`, `DivergenceSummary`, `compute_divergence()`,
   `write_shadow_record()`, `run_shadow_batch()` + unit tests
2. `DocumentRepository.get_recent_analyzed(limit)` â€” new query method, no schema change
3. CLI: `research shadow-run [--count N] [--output PATH]` â€” audit run on recent analyzed docs
4. CLI: `research shadow-report <path>` â€” divergence table + aggregate summary

**Constraints (all sprint-10):**
- No second production pipeline and no shadow-owned mutation path
- No new analysis tier, no factory change, no DB migration
- No routing change â€” `APP_LLM_PROVIDER` is never modified by shadow paths
- Shadow JSONL is not an evaluation report, not a training corpus (I-53)
- Shadow run exits 0 on companion errors â€” non-fatal by design (I-54)

---

### 22. Sprint-11 â€” Distillation Harness und Evaluation Engine

**Status: âœ… Implemented â€” distillation harness and readiness manifest are live**

Full spec: [docs/sprint11_distillation_contract.md](./sprint11_distillation_contract.md)

New module: `app/research/distillation.py` â€” `DistillationInputs`, `ShadowCoverageReport`,
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
| Shadow | `record_type=companion_shadow_run` | `inputs.shadow_path` â€” context only |

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
- No routing change â€” `APP_LLM_PROVIDER` never modified
- `build_distillation_report()` is pure computation (I-62)
- Shadow data NEVER used as teacher or candidate input (I-59)

---

### 23. Sprint-12 â€” Training Job Record und Post-Training Evaluation

**Status: âœ… Implemented â€” training.py, prepare-training-job, link-training-evaluation, record-promotion --training-job, shadow schema canonicalization (I-69), 667+ Tests**

Full spec: [docs/sprint12_training_job_contract.md](./sprint12_training_job_contract.md)

New module: `app/research/training.py` â€” `TrainingJobRecord`, `PostTrainingEvaluationSpec`,
`save_training_job_record()`, `save_post_training_eval_spec()`.

Extension: `app/research/tuning.py` â€” `PromotionRecord.training_job_record` (optional field, additive).

Extension: `app/research/shadow.py` â€” canonical `deviations.*_delta` output (I-69).

**Core principle:** Training is exclusively an external operator process. The platform records
intent (TrainingJobRecord) and links job to evaluation (PostTrainingEvaluationSpec) â€” nothing more.
`record-promotion` remains the sole promotion gate.

**Minimal artifact chain:**
`teacher.jsonl` â†’ `prepare-training-job` â†’ `training_job_record.json`
â†’ [operator trains externally] â†’ `evaluate-datasets` â†’ `evaluation_report.json`
â†’ `link-training-evaluation` â†’ `post_training_eval_spec.json`
â†’ `check-promotion` â†’ `record-promotion` â†’ `promotion_record.json`
â†’ [operator sets APP_LLM_PROVIDER]

**Constraints (Sprint 12):**
- No training engine, no fine-tuning API calls, no weight updates (I-63)
- No auto-routing, no auto-deploy (I-66, I-42)
- No INTERNAL/RULE/Shadow records as training input (I-67)
- record-promotion remains sole gate â€” TrainingJobRecord does not bypass it (I-68)
- Shadow schema canonicalization: `deviations.*_delta` as canonical (I-69)

---

### 24. Sprint-13 â€” Evaluation Comparison und Regression Guard

**Status: âœ… Implemented (Sprint 13C) â€” `compare-evaluations --out`, `EvaluationComparisonReport`,
`save_evaluation_comparison_report()` in `evaluation.py`, `PromotionRecord.comparison_report_path`,
and `record-promotion --comparison` are live.**

Full spec: [docs/sprint13_comparison_contract.md](./sprint13_comparison_contract.md)

**Canonical location**: `app/research/evaluation.py` â€” all comparison types and functions live here.
No separate `comparison.py` module. (See Sprint 13C architecture note in sprint13_comparison_contract.md.)

Implemented in `app/research/evaluation.py`:
- `EvaluationComparisonReport` â€” comparison between two evaluation reports
- `compare_evaluation_reports(baseline_report, candidate_report)` â€” takes `EvaluationReport` objects
- `save_evaluation_comparison_report(report, path, *, baseline_report, candidate_report)` â€” writes JSON
- `RegressionSummary` â€” `has_regression`, `regressed_metrics`, `improved_metrics`, `regressed_gates`, `improved_gates`
- `CountComparison`, `EvaluationMetricDeltas`, `PromotionGateChanges` â€” supporting types
- `report_type: "evaluation_report_comparison"` in persisted JSON (added by `save_evaluation_comparison_report()`)

Implemented extension: `app/research/tuning.py` â€” `PromotionRecord.comparison_report_path`
(optional, additive audit link).

**Core principle:** Comparison report is audit context only â€” not a promotion trigger. Regression
visibility is mandatory; promotion remains exclusively a manual operator decision. `check-promotion`
(G1â€“G6 via `validate_promotion()`) remains the sole quantitative promotion gate.

**Hard regression thresholds (R1â€“R6) â€” deferred to post-Sprint-13C:**
These are not yet implemented. The existing `regression_summary.has_regression` (any metric worsening)
and `regression_summary.regressed_metrics` (which metrics regressed) provide sufficient regression
visibility for Sprint 13C. Explicit per-metric thresholds (R1â€“R6) may be added to `evaluation.py` in
a future sprint without breaking existing contracts.

**Constraints (Sprint 13):**
- No auto-block on any regression â€” operator decides (I-72)
- G1â€“G6 gates unchanged â€” regression visibility is additive audit context (I-73)
- No training, no routing, no auto-deploy (I-70)
- `compare_evaluation_reports()` is pure computation â€” no DB, no LLM, no network (I-71)
- Baseline and candidate must share same `dataset_type` (I-74)

---

## Immutable Invariants

These may never be broken without a new spec:

| # | Rule |
|---|---|
| I-1 | `content_hash` is auto-computed â€” never set manually |
| I-2 | `word_count` is never stored in DB |
| I-3 | `repo.save()` is idempotent on hash collision |
| I-4 | `apply_to_document()` is the only score mutation point |
| I-5 | `update_analysis()` always sets `is_analyzed=True` and `status=analyzed` |
| I-6 | `AnalysisResult` has no DB table â€” scores are denormalized |
| I-7 | LLM output always arrives as validated `LLMAnalysisOutput` â€” never raw dict |
| I-8 | `spam_probability > 0.7` â†’ `priority_score â‰¤ 3` |
| I-9 | status transitions are one-way |
| I-10 | `is_analyzed` and `status=analyzed` are set together, atomically |
| I-11 | `AnalysisResult.confidence_score` is in-memory only â€” NOT written to DB. The DB column `credibility_score` is computed as `1.0 - spam_probability` inside `update_analysis()` |
| I-12 | A document with `analysis_result=None` MUST NOT have `status=ANALYZED` set. `update_analysis(doc_id, None)` is a contract violation â€” caller must check for None and mark FAILED |
| I-13 | Deterministic fallback analysis must remain conservative and must not bypass the shared signal thresholding path |
| I-14 | `InternalCompanionProvider` implements `BaseAnalysisProvider` exactly â€” zero pipeline changes required for companion introduction |
| I-15 | Companion model endpoint MUST be localhost or allowlisted internal address â€” no external inference calls |
| I-16 | Distillation corpus uses only `analysis_source=EXTERNAL_LLM` documents as teacher signal |
| I-17 | Companion model `impact_score` cap: â‰¤ 0.8 (conservative, not overconfident) |
| I-18 | `AnalysisSource` is set at result creation time â€” immutable after `apply_to_document()` |
| I-19 | Rule-only documents (`analysis_source=RULE`) NEVER serve as distillation teacher signal |
| I-20 | `InternalModelProvider.provider_name` is always `"internal"`, `recommended_priority` â‰¤ 5, `actionable=False`, `sentiment_label=NEUTRAL` â€” these are hard invariants, not configurable |
| I-21 | `InternalCompanionProvider.provider_name` is always `"companion"` â€” distinct from `"internal"` (heuristic). factory.py routes `"internal"` â†’ `InternalModelProvider`, `"companion"` â†’ `InternalCompanionProvider` |
| I-22 | `EnsembleProvider` requires at least one provider. InternalModelProvider MUST be the last entry to guarantee a fallback result. If all providers fail, raises `RuntimeError` |
| I-23 | `EnsembleProvider.model` MUST return the winning provider's `provider_name` (not the composite string) immediately after `analyze()` completes. This is the canonical winner signal for pipeline source resolution. |
| I-24 | `_resolve_runtime_provider_name(provider)` resolves the winner name AFTER `analyze()` succeeds using duck-typed `active_provider_name`. `_resolve_analysis_source(winner_name)` then derives the tier. Neither is called in the error/fallback path â€” only `RULE` is valid when analysis failed. |
| I-25 | `doc.provider` stores the **winning** provider name (e.g. `"openai"`, `"internal"`) â€” never the composite ensemble string. `doc.metadata["ensemble_chain"]` records the full ordered list for traceability. |
| I-26 | Teacher eligibility is determined exclusively by `analysis_source=EXTERNAL_LLM`. `doc.provider`, `doc.metadata["ensemble_chain"]`, and all other metadata fields MUST NOT be used as teacher-eligibility criteria. No ensemble composition detail may bypass I-16 or I-19. |
| I-27 | `export_training_data()` MUST enforce teacher-eligibility at the function level when `teacher_only=True`. Uses `doc.analysis_source` directly (not `effective_analysis_source`) â€” legacy rows without an explicit field are excluded. âœ… Implemented. |
| I-28 | The `evaluate` CLI command compares teacher-labeled scores against rule-baseline scores (no LLM calls). This is the Sprint-6 baseline only â€” it does NOT represent companion-model accuracy until a real companion inference endpoint is configured. |
| I-29 | Sprint-6 dataset roles are determined exclusively by `analysis_source`: `EXTERNAL_LLM` = teacher-only, `INTERNAL` = benchmark-only, `RULE` = baseline-only. |
| I-30 | `INTERNAL` and `RULE` rows MUST NEVER be used as teacher labels for distillation, even when other metadata appears favorable. |
| I-31 | Teacher-only filtering MUST use `doc.analysis_source` directly (strict mode, not `effective_analysis_source`) â€” never `provider`, `ensemble_chain`, source name, title, or URL. |
| I-32 | `compare_datasets()` joins datasets by `metadata["document_id"]` only. No fuzzy matching by URL, title, or publish time is allowed. |
| I-33 | The evaluation metric set is mandatory: `sentiment_agreement`, `priority_mae`, `relevance_mae`, `impact_mae`, `tag_overlap_mean`, `actionable_accuracy`, and `false_actionable_rate`. All are implemented in `EvaluationMetrics`. |
| I-34 | Before companion promotion, `false_actionable_rate` MUST be evaluated on paired teacher/candidate rows only and remain `<= 0.05`. `actionable_accuracy` is reported for audit but is not a gate by itself. |
| I-35 | `research check-promotion` reads a saved `evaluation_report.json` only. It MUST NOT trigger analysis, DB reads, or model inference. |
| I-36 | Promotion is never automatic. `check-promotion` exiting 0 does NOT change any system state. A human operator must act on the result explicitly. |
| I-37 | `--save-report` / `--save-artifact` flags are audit-trail only. They do NOT change evaluation semantics or metric values. |
| I-38 | Benchmark artifacts are read-only once written. A re-run MUST produce a new file, never overwrite in-place. |
| I-39 | Companion remains `analysis_source=INTERNAL` until an operator explicitly reconfigures the provider. Passing promotion gates does NOT change provider routing. |
| I-40 | No Sprint-8 code path trains a model, modifies weights, or calls an external training API. Training is exclusively an external operator process. |
| I-41 | `promotion_record.json` is an audit artifact only â€” it does NOT change provider routing. Routing is controlled exclusively by env vars. |
| I-42 | Provider routing is controlled exclusively by `APP_LLM_PROVIDER` and `companion_model_endpoint` env vars. No platform code writes to these. |
| I-43 | `save_promotion_record()` requires a non-empty `operator_note`. Blank notes raise `ValueError`. Operators must acknowledge the promotion decision explicitly. |
| I-44 | Promotion is reversible by setting `APP_LLM_PROVIDER` to the previous value. No migration or code change required. |
| I-45 | `record-promotion` and `save_promotion_record()` require the evaluation report to exist and pass all 6 quantitative gates (G1â€“G6). Non-passing reports block record creation. |
| I-46 | `false_actionable_rate` is the 6th automated promotion gate (G6, threshold <= 0.05). Computed by `compare_datasets()`, enforced by `validate_promotion()` as `false_actionable_pass`. Supersedes the original I-34 "manual, deferred" note. |
| I-47 | `PromotionRecord` MUST embed `gates_summary: dict[str, bool]` â€” a snapshot of all 6 gate pass/fail results at record creation time. A promotion record without gate evidence is incomplete. |
| I-48 | `record-promotion` MUST call `validate_promotion()` and pass the result as `gates_summary` to `save_promotion_record()`. This makes the record self-documenting. |
| I-49 | When `--tuning-artifact` is provided to `record-promotion`, the artifact's `evaluation_report` field MUST resolve to the same path as the provided `report_file`. Mismatch blocks record creation (Exit 1). |
| I-50 | Sprint 9 changes no routing. No new provider, no analysis tier change. All routing remains operator-controlled via env vars (I-42). |
| I-51 | Shadow run MUST NEVER call `apply_to_document()` or `repo.update_analysis()`. Zero DB writes to `canonical_documents`. Shadow result is JSONL-only. |
| I-52 | Shadow run calls `InternalCompanionProvider.analyze()` directly and explicitly â€” independent of `APP_LLM_PROVIDER`. Shadow run is a separate, explicit audit call, never a routing override. |
| I-53 | Shadow JSONL is a standalone audit artifact. It MUST NOT be used as evaluation report input, training teacher data, or promotion gate input. |
| I-54 | Shadow run requires `companion_model_endpoint` to be configured. If absent, the command exits 0 with an informational message â€” not an error. |
| I-55 | Divergence summary is informational only. It MUST NOT be used for routing decisions, promotion gating, alert filtering, or research output modification. |
| I-56 | Live shadow (inline `--shadow` flag in `analyze-pending`/`pipeline run`): Shadow provider runs concurrent to Primary inside `AnalysisPipeline.run()`. Both launched as `asyncio.create_task()`; Primary is awaited first. Shadow exception is caught non-blocking â€” `shadow_error` set, primary unaffected. |
| I-57 | Live shadow persistence: `update_analysis()` receives `metadata_updates=res.document.metadata` (after `apply_to_document()`) â€” NOT `res.trace_metadata`. This ensures `shadow_analysis` and `shadow_provider` written by `apply_to_document()` reach the DB `document_metadata` column. Enforced in both `run_rss_pipeline()` and `analyze-pending`. |
| I-58 | `DistillationReadinessReport` is a readiness assessment only. It MUST NOT trigger training, weight updates, or provider routing changes. `promotion_validation.is_promotable=True` is informational â€” the operator must still use `record-promotion` explicitly (I-36, I-39). |
| I-59 | Shadow JSONL MUST NEVER be passed as `DistillationInputs.teacher_path` or `candidate_path`. Shadow records are audit artifacts only (I-16, I-53). |
| I-60 | `compute_shadow_coverage()` reads shadow records for aggregate divergence stats only. It MUST NOT call `compare_datasets()` or treat shadow data as candidate baseline input. |
| I-61 | `DistillationReadinessReport.shadow_coverage` is optional. Absent shadow data does not invalidate or block a distillation readiness assessment. |
| I-62 | `build_distillation_report()` is pure computation â€” no DB reads, no LLM calls, no network. All I/O is JSONL/JSON file reads via `load_jsonl()` and `json.loads()`. |
| I-63 | `TrainingJobRecord` is a platform-side pre-training manifest only. No platform code runs training jobs, calls fine-tuning APIs, or modifies model weights. Training is exclusively an external operator process. |
| I-64 | A `TrainingJobRecord` with `status="pending"` does not represent a trained model. The operator must run training externally before post-training evaluation can begin. |
| I-65 | Post-training evaluation MUST use the same promotion gates G1â€“G6 as pre-promotion evaluation. `validate_promotion()` is the canonical gate â€” no Sprint-12 bypass is permitted. |
| I-66 | A trained model is not active until the operator reconfigures `APP_LLM_PROVIDER` and `companion_model_endpoint`. No Sprint-12 code changes routing (I-42 extends here). |
| I-67 | The teacher dataset used in `TrainingJobRecord` MUST contain only `analysis_source=EXTERNAL_LLM` rows. `INTERNAL`, `RULE`, and Shadow records MUST NOT be used as training input (I-16, I-19, I-53 extend here). |
| I-68 | `record-promotion` remains the sole promotion gate. `TrainingJobRecord` and `PostTrainingEvaluationSpec` are audit artifacts only â€” they do not trigger or substitute promotion. |
| I-69 | Sprint-12 canonicalizes shadow JSONL schema: `shadow.py` MUST write `"deviations"` field (with `priority_delta`, `relevance_delta`, `impact_delta`) as canonical â€” matching `evaluation.py`. `"divergence"` remains as deprecated backward-compat alias. `compute_shadow_coverage()` continues to normalize both formats until old shadow files are migrated. |
| I-70 | `EvaluationComparisonReport` is a comparison artifact only â€” no routing change, no promotion trigger, no G1â€“G6 gate bypass. |
| I-71 | `compare_evaluation_reports(baseline_report, candidate_report)` takes `EvaluationReport` objects â€” it is pure computation. No DB reads, no LLM calls, no network. The CLI `compare-evaluations` handles file loading via `load_saved_evaluation_report()` before calling this function. (I-62 extends here.) |
| I-72 | When `regression_summary.has_regression=True` in the comparison report and `--comparison` is provided to `record-promotion`, a prominent WARNING is printed. Promotion is NOT automatically blocked â€” the operator must explicitly decide to proceed. `PromotionRecord.comparison_report_path` is set for the audit trail. Hard regression per-metric thresholds (R1â€“R6) are deferred; `has_regression` (any worsening) is the current operative flag. |
| I-73 | `compare-evaluations` exit code 0 does NOT imply the candidate is promotable. `check-promotion` on the candidate report remains required (I-36, I-65). The comparison is additional audit context only. |
| I-74 | Baseline and candidate evaluation reports MUST share the same `dataset_type`. Different `dataset_type` values raise `ValueError` in `compare_evaluation_reports()`. |
| I-75 | `UpgradeCycleReport` is a pure read/summarize artifact. `build_upgrade_cycle_report()` MUST NOT trigger training, evaluation reruns, promotions, or routing changes. The only I/O is JSON file reads via `json.loads()`. (I-62, I-70 extend here.) |
| I-76 | `UpgradeCycleReport.status` is derived exclusively from artifact presence (`Path.exists()`) â€” never auto-advanced by the platform. No platform code advances `status` without the operator supplying a new artifact path. |
| I-77 | `UpgradeCycleReport.promotion_readiness=True` is informational only. No platform code calls `record-promotion` or changes `APP_LLM_PROVIDER` based on this field. The operator must run `record-promotion` explicitly (I-36, I-68 extend here). |
| I-78 | `UpgradeCycleReport.promotion_record_path` is set ONLY when the operator explicitly supplies this path to `build_upgrade_cycle_report()` or the CLI. It MUST NOT be auto-populated from env vars or settings. |
| I-79 | Each `UpgradeCycleReport` represents one upgrade cycle attempt. Parallel or sequential cycles (e.g. v1â†’v2, v2â†’v3) produce separate files. A cycle report MUST NOT be overwritten in-place â€” re-runs produce new files (I-38 extends here). |
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
| I-93 | `ABCInferenceEnvelope` produced during a shadow-enabled analyze-pending run is written per-document to audit JSONL only â€” no DB writes, no routing changes. |
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

### 25. Sprint-13 Extension â€” Companion Upgrade Cycle Report

**Status: âœ… Implemented â€” `upgrade-cycle-status` and `UpgradeCycleReport` are live**

Full spec: [docs/sprint13_comparison_contract.md Part 2](./sprint13_comparison_contract.md)

New module: `app/research/upgrade_cycle.py` â€” `UpgradeCycleReport`,
`build_upgrade_cycle_report()`, `derive_cycle_status()`, `save_upgrade_cycle_report()`.

New CLI command: `research upgrade-cycle-status` â€” displays current cycle state and
next-step guidance. Does NOT replace individual step commands.

**Status phases** (hierarchical, derived from artifact presence only):

| Phase | Condition |
|-------|-----------|
| `prepared` | teacher_dataset_path exists |
| `training_recorded` | + training_job_record.json exists |
| `evaluated` | + evaluation_report.json exists |
| `compared` | + comparison_report.json exists (optional step) |
| `promotable` | evaluated + candidate passes G1â€“G6 via `validate_promotion()` |
| `promoted_manual` | + promotion_record.json exists |

**Core principle:** The orchestrator reads, chains, and summarizes â€” never auto-advances,
never auto-promotes, never changes routing. Simple but powerful audit surface.

**Constraints (Sprint 13, Task 13.5):**
- No auto-routing, no auto-deploy, no auto-promotion (I-76, I-77)
- `build_upgrade_cycle_report()` is pure computation â€” JSON reads only (I-75)
- `promotion_readiness=True` is informational only (I-77)
- `promotion_record_path` is operator-supplied only, never auto-populated (I-78)
- Each cycle = one file; no in-place overwrite (I-79)

---

### 26. Sprint-14 â€” Controlled A/B/C Inference Profiles and Signal Distribution

**Status: âœ… Contract-defined â€” runtime implementation intentionally deferred**

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

### 27. Sprint-17 â€” Route Integration in analyze-pending

**Status: âœ… Implemented â€” primary/shadow/control route runner live**

Full spec: [docs/sprint17_route_integration_contract.md](./sprint17_route_integration_contract.md)

Sprint 17 wires `ActiveRouteState` (Sprint 14C) into `analyze-pending`:
- Phase 2.5: shadow/control inference via `run_route_provider()` â€” no DB writes
- Phase 3: primary results â†’ DB only (I-92)
- Phase ABC: `ABCInferenceEnvelope` per document â†’ audit JSONL only (I-93)

New module: `app/research/route_runner.py` â€” `map_path_to_provider_name()`,
`build_path_result_from_llm_output()`, `build_path_result_from_analysis_result()`,
`build_comparison_summaries()`, `build_abc_envelope()`, `run_route_provider()`.

**Core constraints (I-90â€“I-93):**
- Primary result is the sole DB write â€” shadow/control never touch DB (I-92)
- `ABCInferenceEnvelope` is audit JSONL only â€” no DB, no routing change (I-93)
- `APP_LLM_PROVIDER` and route state unchanged by analyze-pending (I-90, I-91)
- Active route profile suppresses `--shadow-companion` (I-84)
- `run_route_provider()` never raises â€” failure is isolated to shadow/control path

**`DistributionMetadata.activation_state`:**
- `"active"` â€” set by `route_runner.build_abc_envelope()` (live route run)
- `"audit_only"` â€” set by Sprint 14 `abc-run` CLI (post-hoc artifact construction)

---

### 29. Sprint-18 â€” Controlled MCP Server Integration

**Status: âœ… Implemented â€” read surface + guarded write surface**

Full spec: [docs/sprint18_mcp_contract.md](./sprint18_mcp_contract.md)

Sprint 18 defines and implements a controlled MCP server (`app/agents/mcp_server.py`) that
exposes KAI's research surface to AI-capable tools (e.g. Claude Desktop) with strict guardrails.

**Surface layers:**

| Layer | Tools | DB? | File writes? |
|-------|-------|-----|--------------|
| Read | `get_watchlists`, `get_research_brief`, `get_signal_candidates`, `get_route_profile_report`, `get_inference_route_profile`, `get_active_route_status`, `get_upgrade_cycle_status`, `get_mcp_capabilities` | read-only | none |
| Guarded write | `create_inference_profile`, `activate_route_profile`, `deactivate_route_profile` | none | one artifact JSON per call |

**Core constraints (I-94â€“I-100):**
- MCP is a controlled ingress point â€” not an admin panel, not a trading interface
- Read tools are side-effect free (I-95)
- Write tools produce exactly one artifact file, return audit JSON with `app_llm_provider_unchanged: true`,
  and MUST NOT change `APP_LLM_PROVIDER` (I-96, I-97)
- All file paths validated via `_resolve_workspace_path()` to prevent path traversal (I-94)
- Trading execution, auto-promotion, auto-routing, dataset export, training submission,
  promotion recording remain permanently out of MCP scope (I-98, I-99, I-100)

---

### 30. Sprint-16 â€” Controlled External Signal Consumption Layer

**Status: âœ… Implemented â€” read-only execution handoff surface**

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

**Core constraints (I-101â€“I-104):**
- The handoff is advisory only and read-first â€” never an execution hook
- The batch report in `distribution.py` wraps canonical immutable `SignalHandoff` rows from `execution_handoff.py`
- Provenance must remain explicit per row: provider, `analysis_source`, route, source, timestamps
- No reverse control channel exists: no fills, no execution callback, no strategy feedback, no trading writes. Audit-only receipt logging is allowed only as append-only acknowledgement artifacts.

**Sprint 16 â€” Immutable `SignalHandoff` artifact (I-105â€“I-108):**

Sprint 16 adds `app/research/execution_handoff.py` with a frozen `SignalHandoff` dataclass
as the canonical external delivery artifact:

- `frozen=True` â€” immutable after construction, new UUID `handoff_id` per call (I-105)
- `evidence_summary` â€” truncated to 500 chars; no full document text forwarded (I-106)
- `recommended_next_step` excluded â€” internal KAI field, never serialized (I-107)
- `consumer_note` always present; `provenance_complete` based on `signal_id`/`document_id`/`analysis_source` (I-108)

CLI: `research signal-handoff [--out batch.jsonl] [--out-json single.json]`

---

### 31. Sprint-19 â€” Route-Aware Signal Distribution Classification

**Status: âœ… Implemented â€” primary handoff stays productive, shadow/control stay audit-only**

Sprint 20 extends the existing external-consumption surface without introducing a second
signal or handoff stack. Route-aware delivery is derived from canonical route metadata only.

**Classification rules (I-109â€“I-112):**
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

**Core constraints (I-109â€“I-112):**
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

### 32. Sprint-20 â€” External Consumer Collector & Acknowledgement Orchestration

**Status: âœ… Implemented â€” audit-only consumer acknowledgement surface**

Sprint 20 defines the controlled consumer acknowledgement layer on top of the existing
SignalHandoff-based handoff surface. Consumers may read and acknowledge signal handoffs.
Acknowledgement is audit-only and has no operational effect.

**Core principle (I-116): Acknowledgement is AUDIT ONLY.**
- Acknowledgement â‰  execution
- Acknowledgement â‰  approval
- Consumer state â‰  routing decision (I-117, I-121, I-122)
- No reverse channel into KAI core analysis (I-118, I-120)

**Invariants (I-116â€“I-122):**
- I-116: Consumer acknowledgement is AUDIT ONLY. The record is a receipt, not an approval.
- I-117: Acknowledgement does not confirm trade intent, execution, or routing eligibility.
- I-118: Acknowledgement MUST NOT write back to KAI core DB or modify any SignalHandoff.
- I-119: Acknowledgement exists only when receipt occurred. There is no pending write-back state in core models.
- I-120: Consumer acknowledgements are stored append-only in `consumer_acknowledgements.jsonl`.
  Existing records are never overwritten or deleted.
- I-121: Consumer state (who acknowledged what) is NEVER a routing decision input.
- I-122: Aggregate collector surfaces are read-only count summaries. They contain no execution
  state, no routing mutation, and no write-back capability.

**Surface (Sprint 20C â€” kanonisch):**
- Canonical runtime path: `app/research/execution_handoff.py` + `app/research/distribution.py`
- Audit artifact type: `HandoffAcknowledgement` (execution_handoff.py, frozen=True)
- Rehydration helper: `handoff_acknowledgement_from_dict(payload)` â€” fail-closed parser for persisted JSONL rows
- Collector report: `HandoffCollectorSummaryReport` (distribution.py)
- Append-only audit file: `HANDOFF_ACK_JSONL_FILENAME = "consumer_acknowledgements.jsonl"`
- `create_handoff_acknowledgement(handoff, *, consumer_agent_id, notes="")` â€” validates visibility, creates immutable audit record; raises PermissionError for non-visible handoffs
- `append_handoff_acknowledgement_jsonl(ack, path)` â€” append-only JSONL write
- `load_handoff_acknowledgements(path)` â€” read-only load, skips malformed lines
- `build_handoff_collector_summary(handoffs, acks)` â€” combined handoff + ack counts â†’ HandoffCollectorSummaryReport
- MCP write: `acknowledge_signal_handoff(handoff_path, handoff_id, consumer_agent_id, notes="")` â€” audit-only, PermissionError on hidden handoffs
- MCP read: `get_handoff_collector_summary(handoff_path, acknowledgement_path)` â€” read-only collector
- MCP compatibility alias only: `get_handoff_summary(handoff_path, acknowledgement_path)` â€” not the canonical name
- CLI write: `research handoff-acknowledge <handoff_file> --handoff-id ... --consumer-agent-id ...`
- CLI read: `research handoff-collector-summary <handoff_file> [--ack-file ...]`
- CLI compatibility aliases only: `research handoff-summary <handoff_file> [--ack-file ...]`, `research consumer-ack <handoff_file> <handoff_id> --consumer-agent-id ...`
- Superseded/removed runtime module only: `app/research/consumer_collection.py`

**What is explicitly excluded:**
- No DB mutation in the acknowledgement path
- No auto-escalation of acknowledged signals to trading
- No order semantics in any acknowledgement artifact
- No broker access, no execution engine interface
- Collector surface â‰  execution engine


---

### 33. Sprint-21 â€” Operational Readiness Surface

**Status: âœ… Implemented â€” observational-only readiness surface**

Sprint 21/22 defines a small operational readiness layer for route health, provider health,
distribution drift, collector backlog, artifact freshness, and shadow/control visibility.
The report is derived from existing handoff, acknowledgement, route-state, ABC-envelope,
and alert-audit artifacts only.

**Core principle (I-123): Readiness is OBSERVATIONAL ONLY.**
- Readiness â‰  execution trigger
- Readiness â‰  auto-remediation (I-124)
- Readiness â‰  routing decision (I-128)
- Readiness â‰  auto-promotion (I-129)
- No readiness report modifies any signal, handoff, route profile, or KAI state (I-126)

**Invariants (I-123â€“I-130):**
- I-123: Readiness reports are OBSERVATIONAL ONLY. No report triggers execution, routing, or state change.
- I-124: Readiness generation â‰  auto-remediation. Operator must act manually.
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
- MCP read: `get_operational_readiness_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours)` â€” read-only
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

### 34. Sprint-22 â€” Provider Health & Distribution Drift Monitoring Surface

**Status: âœ… Implemented â€” observational-only provider and drift monitoring**

Sprint 22 consolidates the Monitoring/Readiness stack and provides a dedicated operator-facing surface for provider health and distribution drift. No new parallel architecture is introduced. The canonical backend is `operational_readiness.py`; provider health and drift are derived read views only.

**Core principle (I-131): Provider health and distribution drift monitoring is OBSERVATIONAL ONLY.**

**Invariants:**

- I-131: Provider health and drift observations NEVER trigger execution, order placement, or broker access.
- I-132: No auto-routing based on provider health or drift status. Operator intervention is always required.
- I-133: No auto-promotion of signals, models, or routes based on health status.
- I-134: `get_provider_health` and `get_distribution_drift` are read-only MCP tools. No state is mutated.
- I-135: All health and drift artifacts derive from existing runtime artifacts only. No second monitoring stack is introduced.
- I-136: Provider-health and drift outputs expose issue context only. Guidance is advisory only â€” it never implies or enables execution.
- I-137: Monitoring outputs remain read-only at all times. No remediation, routing, or promotion flags are introduced.
- I-138: CLI commands `research provider-health` and `research drift-summary` produce human-readable operator output only. No write-back or DB mutation.

**Canonical modules:**
- `app/research/operational_readiness.py` â€” canonical readiness report: `OperationalReadinessReport`, `ProviderHealthSummary` (per-path health rows), `DistributionDriftSummary` (aggregate drift indicators), `build_operational_readiness_report`, `save_operational_readiness_report`
- `app/research/operational_alerts.py` â€” standalone check library (exists, not in MCP/CLI path); superseded as production surface by Sprint 22

**MCP surface (read-only):**
- `get_operational_readiness_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours)` â€” canonical full readiness report
- `get_provider_health(handoff_path, state_path, abc_output_path)` â€” returns the readiness-derived `provider_health_summary` slice
- `get_distribution_drift(handoff_path, state_path, abc_output_path)` â€” returns the readiness-derived `distribution_drift_summary` slice
- All tools validate workspace path confinement (I-95); provider/drift views are bounded subsets of the canonical readiness report

**CLI surface:**
- `research readiness-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--out ...]` â€” canonical operator-facing monitoring command
- `research provider-health [--handoff-file ...] [--state ...] [--abc-output ...] [--out ...]` â€” human-readable provider health view derived from readiness
- `research drift-summary [--handoff-file ...] [--state ...] [--abc-output ...] [--out ...]` â€” human-readable distribution drift view derived from readiness

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

### 35. Sprint-23 â€” Protective Gates & Remediation Recommendations Surface

**Status: âœ… Implemented â€” readiness-derived protective gates and advisory-only remediation**

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

- `app/research/operational_readiness.py` â€” internal gate model: `ProtectiveGateSummary` (gate_status, blocking_count, warning_count, advisory_count, items), `ProtectiveGateItem` (gate_status, severity, category, summary, subsystem, blocking_reason, recommended_actions, evidence_refs), embedded in `OperationalReadinessReport`

**Gate contract fields:**

- `gate_status` â€” `clear`, `blocking`, `warning`, or `advisory`
- `severity` â€” inherited readiness severity (`info`, `warning`, `critical`)
- `blocking_reason` â€” explicit blocking explanation for blocking items only
- `subsystem` â€” `handoff`, `artifacts`, `providers`, `distribution`, `routing`, or `monitoring`
- `recommended_actions` â€” ordered operator-only hints
- `evidence_refs` â€” source/category/path/provider references tied to existing artifacts only

**MCP surface (read-only):**

- `get_protective_gate_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours)` â€” returns readiness-derived gate counts and items
- `get_remediation_recommendations(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours)` â€” returns read-only recommendation rows derived from gate items

**CLI surface:**

- `research gate-summary [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--stale-after-hours N] [--out ...]`
- `research remediation-recommendations [--handoff-file ...] [--ack-file ...] [--state ...] [--abc-output ...] [--alert-audit-dir ...] [--stale-after-hours N] [--out ...]`

**What is explicitly excluded:**

- No write-back to `CanonicalDocument`, signal handoffs, route profiles, or collector state
- No acknowledgement side effects beyond existing append-only audit flows
- No trading semantics or execution enablement
- No open remote-superuser or remediation control plane

---

### 36. Sprint-24 â€” Artifact Lifecycle Management Surface

**Status: âœ… Implemented â€” operator-triggered inventory and safe archival of stale artifacts**

Sprint 24 closes the operational loop established in Sprints 21â€“23.
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
- I-151: Stale detection uses file `mtime` only â€” no content inspection of artifact files.
- I-152: CLI `artifact-rotate` defaults to `--dry-run`. Operator must pass `--no-dry-run` for actual archival.

**Canonical module:**

- `app/research/artifact_lifecycle.py` â€” `ArtifactEntry` (frozen), `ArtifactInventoryReport` (frozen, execution_enabled=False), `ArtifactRotationSummary` (frozen), `build_artifact_inventory(artifacts_dir, stale_after_days=30.0)`, `rotate_stale_artifacts(artifacts_dir, stale_after_days=30.0, *, dry_run=True)`, `save_artifact_inventory()`, `save_artifact_rotation_summary()`

**Managed file types:** `.json` and `.jsonl` only. Directories (including `archive/`) always skipped.

**MCP surface (read-only):**

- `get_artifact_inventory(artifacts_dir, stale_after_days)` â€” workspace-confined read-only inventory (I-149)

**CLI surface:**

- `research artifact-inventory [--artifacts-dir DIR] [--stale-after-days N] [--out FILE]` â€” read-only inventory report
- `research artifact-rotate [--artifacts-dir DIR] [--stale-after-days N] [--dry-run/--no-dry-run] [--out FILE]` â€” dry-run-safe rotation (default: dry-run, I-152)

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

### 37. Sprint-25 â€” Safe Artifact Retention & Cleanup Policy

**Status: âœ… Implemented â€” read-only retention classification surface**

Sprint 25 extends `artifact_lifecycle.py` with explicit retention policy classification.
Each artifact is assigned an `artifact_class` and `retention_class` to guide operator decisions
about what is safe to archive. Retention policy is advisory only â€” no auto-cleanup, no auto-deletion.

**Core principle (I-153): Retention policy is classification only. No cleanup is triggered automatically.**

**Invariants:**

- I-153: Retention policy is classification only. No cleanup triggered automatically.
- I-154: `ArtifactRetentionEntry.delete_eligible` MUST always be `False`. Deletion is never platform-initiated.
- I-155: `protected=True` artifacts MUST NOT appear as rotation candidates.
- I-156: AUDIT_TRAIL artifacts always protected: `mcp_write_audit.jsonl`, `consumer_acknowledgements.jsonl`, `alert_audit.jsonl`, and canonical signal handoff artifacts such as `handoffs.jsonl`, `handoff.json`, and `execution_signal_handoff*.json`.
- I-157: PROMOTION_RECORD artifacts always protected: `promotion_record.json`.
- I-158: TRAINING_DATA artifacts always protected: `teacher.jsonl`, `candidate.jsonl`, `tuning_manifest.json`.
- I-159: ACTIVE_STATE artifacts (`active_route_profile.json`) protected when route is active.
- I-160: `build_retention_report()` is pure computation â€” no DB reads, no LLM calls, no network, no filesystem writes.
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

- `protected` â€” operator MUST NOT archive; critical audit/training/state data
- `rotatable` â€” stale and safe to archive via `artifact-rotate`
- `review_required` â€” operator must confirm classification before any action

**Canonical module:**

- `app/research/artifact_lifecycle.py` (extended Sprint 25):
  - `ArtifactRetentionEntry` (frozen): name/path/size_bytes/modified_at/age_days/status/artifact_class/retention_class/protected/rotatable/delete_eligible=False/retention_rationale/operator_guidance
  - `ArtifactRetentionReport` (frozen): execution_enabled=False, write_back_allowed=False, delete_eligible_count=0
  - `ArtifactCleanupEligibilitySummary` (frozen): cleanup_eligible_count, dry_run_default=True, candidates, delete_eligible_count=0
  - `ProtectedArtifactSummary` (frozen): protected_count, entries, delete_eligible_count=0
  - `classify_artifact_retention(entry, *, active_route_active=False)` â€” pure classification
  - `build_retention_report(artifacts_dir, stale_after_days=30.0, *, active_route_active=False)`
  - `build_cleanup_eligibility_summary(report)` / `build_protected_artifact_summary(report)` â€” pure report projections only
  - `save_retention_report(report, path)`
  - `save_cleanup_eligibility_summary(summary, path)` / `save_protected_artifact_summary(summary, path)`

> **Sprint 26 extension (â†’ Â§38):** `ReviewRequiredArtifactSummary`, `build_review_required_summary()`, `save_review_required_summary()`, `get_review_required_summary`, `research review-required-summary` wurden in Sprint 26 ergÃ¤nzt. Kanonische Dokumentation: Â§38.

**MCP surface (read-only):**

- `get_artifact_retention_report(artifacts_dir, stale_after_days, state_path)` â€” workspace-confined, read-only (I-153/I-160)
- `get_cleanup_eligibility_summary(artifacts_dir, stale_after_days, state_path)` â€” advisory archive eligibility only
- `get_protected_artifact_summary(artifacts_dir, stale_after_days, state_path)` â€” protected entries only

> **Sprint 26 extension (â†’ Â§38):** `get_review_required_summary` in Â§38 dokumentiert.

**CLI surface:**

- `research artifact-retention [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` â€” read-only classification view
- `research cleanup-eligibility-summary [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` â€” archive-eligibility summary, dry-run-first
- `research protected-artifact-summary [--artifacts-dir DIR] [--stale-after-days N] [--state PATH] [--out FILE]` â€” protected artifact summary only

> **Sprint 26 extension (â†’ Â§38):** `research review-required-summary` in Â§38 dokumentiert.

**What is explicitly excluded:**

- No automatic cleanup or deletion (I-153/I-154)
- No write-back to routing, handoffs, or DB state
- No trading semantics or execution enablement
- No second classification stack alongside this one


---

<a name="historical-archive"></a>

## Phase 1–2 Historical Contract Archive (§§38–§62) — all closed

> All sprint contracts below are closed. Content is preserved for traceability. No modifications permitted under S50D (I-395).

---

## Â§38 Sprint 26/26C â€” Artifact Governance/Review Surface (Canonical)

**Module:** `app/research/artifact_lifecycle.py` (canonical, extended from Sprint 25)

**Canonical governance/review surface:**

| Class | Purpose |
|---|---|
| `ArtifactRetentionReport` | Single classification source for protected / rotatable / review_required |
| `ArtifactCleanupEligibilitySummary` | Advisory archive-eligibility view, dry-run-first |
| `ProtectedArtifactSummary` | Protected artifact visibility for operators |
| `ReviewRequiredArtifactSummary` | Operator review queue with `retention_rationale` and `operator_guidance` per entry |

**Canonical functions:**

- `build_retention_report(artifacts_dir, stale_after_days=30.0, *, active_route_active=False)` â€” single classification source
- `build_cleanup_eligibility_summary(report)` / `build_protected_artifact_summary(report)` / `build_review_required_summary(report)` â€” pure projections only
- `save_retention_report(report, path)` / `save_cleanup_eligibility_summary(summary, path)` / `save_protected_artifact_summary(summary, path)` / `save_review_required_summary(summary, path)` â€” JSON persistence

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

## Â§39 Sprint 27 - Safe Operational Escalation Surface (Canonical)

**Status: âœ… Implemented - read-only escalation surface on the canonical readiness and governance stacks**

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

CLI commands (`research escalation-summary`, `research blocking-summary`, `research operator-action-summary`) call MCP server tools via asyncio and pass `--artifacts-dir` as a workspace-relative path resolved through the MCP workspace guard (I-95). The artifact-lifecycle CLI commands (`research artifact-retention`, `research cleanup-eligibility-summary`, `research protected-artifact-summary`, `research review-required-summary`, `research artifact-rotate`) call `artifact_lifecycle` functions directly â€” the MCP workspace guard (I-95) applies to the MCP protocol context only, not to CLI invocations.

> **Note:** The `research escalation-summary` CLI command had a pre-existing `state` parameter bug (duplicate `out` replacing `state`) corrected in Sprint 27C. Canonical CLI parameter list: `--handoff-file`, `--ack-file`, `--state`, `--abc-output`, `--alert-audit-dir`, `--stale-after-hours`, `--artifacts-dir`, `--retention-stale-after-days`, `--out`.

---

## Â§40 Sprint 28 - Safe Operator Action Queue (Canonical)

**Status: âœ… Implemented - read-only operator action queue projected from the canonical escalation surface**

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

## Â§41 Sprint 29 - Read-Only Operator Decision Pack (Canonical)

**Status: âœ… Implemented - read-only operator decision pack bundling canonical summaries only**

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

## Â§42 Sprint 30/30C â€” Read-Only Operator Runbook (Kanonisch)

**Status: âœ… Sprint 30C final â€” read-only operator runbook with validated command refs**

Sprint 30 adds a small operator-facing runbook surface on top of the canonical
decision pack. The runbook does NOT recompute readiness, escalation, governance,
or queue state. It derives ordered next steps from the existing `OperatorDecisionPack`
and validates every referenced CLI command against the actually registered
`research` command set (fail-closed).

**Invariants (I-193â€“I-200):**

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

- `report_type` â€” always `"operator_runbook_summary"`
- `overall_status`, `blocking_count`, `review_required_count`, `action_queue_count`
- `affected_subsystems`, `operator_guidance`, `evidence_refs`, `command_refs`
- `steps` (all `RunbookStep` objects, ordered by priority then queue_status)
- `next_steps` (first â‰¤3 steps)
- `generated_at`, `interface_mode`, `execution_enabled`, `write_back_allowed`

**Canonical MCP surface (read-only, workspace-confined via I-95):**

| Tool | Returns |
|---|---|
| `get_operator_runbook(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours, artifacts_dir, retention_stale_after_days)` | `OperatorRunbookSummary.to_json_dict()` |

**Canonical CLI surface (drei eigenstÃ¤ndige Kommandos â€” kein Alias):**

| Command | Output |
|---|---|
| `research operator-runbook [--handoff-path ...] [--state-path ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--stale-after-days N] [--out FILE]` | VollstÃ¤ndiger Runbook mit allen Steps und Guidance |
| `research runbook-summary [--handoff-path ...] [--state-path ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--stale-after-days N]` | Kompakter Status-Ãœberblick (kein --out) |
| `research runbook-next-steps [--handoff-path ...] [--state-path ...] [--alert-audit-dir ...] [--artifacts-dir ...] [--stale-after-days N]` | Nur next_steps slice |

**Command Safety Guardrail:**

- `get_registered_research_command_names()` in `app/cli/main.py` liefert die Referenzmenge
- `get_invalid_research_command_refs()` validiert fail-closed beim MCP-Call
- Superseded Commands (`governance-summary`, `operator-decision-pack`) dÃ¼rfen NICHT in `command_refs` erscheinen

**What is explicitly excluded:**

- No auto-remediation
- No auto-routing or auto-promotion
- No trading execution
- No destructive cleanup or deletion
- No superseded command refs inside the runbook payload

---

## Â§43 Sprint 31 â€” CLI Contract Lock & MCP Surface Lock (Canonical)

**Ziel**: Den kanonischen CLI- und MCP-Surface nach Sprint 30/30C einzufrieren, Coverage-LÃ¼cken zu schlieÃŸen und Drift-PrÃ¤vention dauerhaft sicherzustellen. Keine neuen Business-Features â€” ausschlieÃŸlich Stabilisierung, Coverage und Contract-Klarheit.

**Invarianten: I-201â€“I-210**

### Kanonische CLI-OberflÃ¤che (44 Commands, eingefroren nach Sprint 31)

| App | Count |
|---|---|
| `query_app` | 4 |
| `research_app` | 40 |

**Autoritative Referenzmenge:** `get_registered_research_command_names()` in `app/cli/main.py`

**Coverage-Pflicht (I-203):** Jeder kanonische CLI-Command MUSS mindestens einen targeted Test haben. Nach Sprint 31: 0 ungetestete Commands.

**6 Coverage-LÃ¼cken geschlossen in Sprint 31:**

| Command | Neue Tests |
|---|---|
| `research signals` | in-help + no-candidates (DB mock) |
| `research benchmark-companion-run` | in-help + missing-teacher-file |
| `research check-promotion` | in-help + missing-file + all-pass + gate-fail |
| `research prepare-tuning-artifact` | in-help + missing-teacher-file |
| `research record-promotion` | in-help + missing-file + gates-blocked |
| `research evaluate` | in-help + no-teacher-docs (DB mock) |

### Kanonische MCP-OberflÃ¤che (38 registrierte Tools, konsolidiert nach Sprint 32)

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

**`get_narrative_clusters` (I-205):** Registriertes `@mcp.tool()` und kanonisches read-only Tool â€” MUSS in `read_tools` erscheinen.

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

## Â§44 Sprint 32 â€” MCP Contract Lock & Coverage Completion (Canonical)

**Ziel**: Den MCP-Surface vollstÃ¤ndig klassifizieren, Coverage auf 100% bringen und Drift-PrÃ¤vention durch maschinenlesbare Klassifikation dauerhaft absichern. Keine neuen Business-Features.

**Invarianten: I-211â€“I-220**

### MCP Tool Classification Schema

Jedes registrierte `@mcp.tool()` ist genau einer der folgenden Klassen zugeordnet:

| Klasse | Bedeutung |
|---|---|
| `canonical` | PrimÃ¤re, autorisierte Surface-Funktion |
| `active_alias` | Backward-kompatibler Alias mit stabilem Verhalten; erscheint in `read_tools` |
| `superseded` | Durch kanonische Funktion ersetzt; NICHT in `read_tools`; bleibt registriert fÃ¼r KompatibilitÃ¤t |
| `workflow_helper` | Meta-Funktion (get_mcp_capabilities); erscheint in `workflow_helpers`, nicht in `read_tools` |

Tool-Mode Klassen:

| Mode | Bedeutung |
|---|---|
| `read_only` | Kein Schreiben, keine Routing-Ã„nderung, kein Auto-anything |
| `guarded_write` | Workspace-confined, Write-Audit JSONL zwingend (I-94/I-95) |
| `workflow_helper` | Gibt nur Capabilities zurÃ¼ck |

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
| `get_handoff_summary` | active_alias â†’ `get_handoff_collector_summary` | read_only | handoff |
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
| `get_operational_escalation_summary` | superseded â†’ `get_escalation_summary` | read_only | escalation |
| `get_escalation_summary` | canonical | read_only | escalation |
| `get_blocking_summary` | canonical | read_only | escalation |
| `get_operator_action_summary` | canonical | read_only | escalation |
| `get_action_queue_summary` | canonical | read_only | action_queue |
| `get_blocking_actions` | canonical | read_only | action_queue |
| `get_prioritized_actions` | canonical | read_only | action_queue |
| `get_review_required_actions` | canonical | read_only | action_queue |
| `get_decision_pack_summary` | canonical | read_only | decision_pack |
| `get_operator_decision_pack` | active_alias â†’ `get_decision_pack_summary` | read_only | decision_pack |
| `get_operator_runbook` | canonical | read_only | runbook |

**Zusammenfassung:**
- Canonical: 34
- Active alias (in read_tools): 2 (`get_handoff_summary`, `get_operator_decision_pack`)
- Superseded (NOT in read_tools): 1 (`get_operational_escalation_summary`)
- Workflow helper: 1 (`get_mcp_capabilities`)
- **Total: 38 registered `@mcp.tool()`**

**read_tools ZÃ¤hlung:** 32 (34 canonical âˆ’ 4 guarded_write âˆ’ 1 workflow_helper + 2 active_alias + 1 superseded_not_in_read = 32 canonical_read + 2 alias = 32 total in list)

### Coverage Completion nach Sprint 32

| Tool | Status |
|---|---|
| `get_narrative_clusters` | âœ… targeted test (Sprint 32) |
| `get_operational_escalation_summary` | âœ… targeted test (Sprint 32) |
| Alle Ã¼brigen 36 Tools | âœ… bereits getestet (Sprint 1â€“31) |

### Safety Guardrails (unverÃ¤nderlich)

- Keine Auto-Routing, keine Auto-Promotion, keine Auto-Remediation
- Kein direkter Trading-Execution-Hook
- Guarded-write tools: write-confined zu `workspace/artifacts/`, Write-Audit JSONL
- Superseded tools bleiben registriert (KompatibilitÃ¤t), aber NICHT in `read_tools`
- `get_mcp_capabilities()` bleibt die autoritative, maschinenlesbare Surface-Beschreibung (I-217)

**What is explicitly excluded:**

- No new business logic, no new monitoring architecture
- No trading execution, no DB mutation from read-only tools
- No auto-deletion, no auto-remediation, no auto-routing

---

## Â§45 Sprint 33 â€” Append-Only Operator Review Journal & Resolution Tracking (Canonical)

**Status: âœ… canonical append-only operator review surface on top of the existing runbook / decision-pack / governance stack**

Sprint 33 adds a minimal operator review journal that documents human review and
resolution state without mutating any KAI core models. The journal is an audit
surface only. It does NOT introduce a second governance, action-queue, or
decision stack.

**Invarianten: I-221â€“I-230**

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

## Â§46 Sprint 35 â€” KAI Backtest Engine: Signalâ†’Riskâ†’Paper Loop (Canonical)

**Status: âœ… canonical paper-only backtest surface â€” Signalâ†’RiskEngineâ†’PaperExecution loop**

Sprint 35 closes the core KAI execution loop: SignalCandidates from the research
surface are routed through all RiskEngine gates and, if approved, executed in
PaperExecutionEngine. The backtest is simulation-only, audit-safe, and kill-switch-aware.

**Invarianten: I-231â€“I-240**

- I-231: BacktestEngine MUST use `PaperExecutionEngine(live_enabled=False)`. No live path.
- I-232: Every signal MUST pass through all RiskEngine gates. No gate bypass permitted.
- I-233: `BacktestResult` MUST be immutable (frozen dataclass).
- I-234: Market data MUST be provided via `dict[str, float]` â€” no hidden data fetches inside run().
- I-235: Signalâ†’Order mapping MUST be deterministic given identical inputs.
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
| `skipped_neutral` | direction_hint=="neutral" â€” always skipped (I-236) |
| `skipped_bearish` | direction_hint=="bearish" with long_only=True â€” skipped (I-236, A-012) |
| `no_price` | No price found for target_asset in prices dict |
| `no_quantity` | Position size calculated as zero or fill rejected by paper engine |
| `kill_switch_halted` | Kill switch was active before this signal was processed (I-237) |

### CLI Surface

| Command | Zweck |
|---|---|
| `research backtest-run [--signals-path ...] [--out ...] [--initial-equity ...] [--stop-loss-pct ...] [--min-confidence ...] [--audit-path ...]` | Paper backtest from signal JSONL |

### Assumptions

- A-012: long_only=True by default â€” bearish signals skipped
- A-013: max_leverage=1.0 always in BacktestEngine
- A-014: SL/TP derived mechanically from config (not from signal risk notes)
- A-015: signal_confluence_count=1 per signal in backtest

**What is explicitly excluded:**

- No live execution path
- No gate bypass under any condition
- No trading PnL guarantee or performance claim
- No short-selling without explicit long_only=False
- No external market data fetch inside BacktestEngine.run()


## Â§47 Sprint 36 â€” Decision Journal & TradingLoop CLI/MCP Surface (Canonical)

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
- `get_loop_cycle_summary` is strictly read-only â€” no state change.
- All new MCP tools appear in `get_mcp_tool_inventory()` with correct classification.
- `test_mcp_tool_inventory_matches_registered_tools` enforces registered == classified.

### Assumptions Referenced

- A-014: Evidence Before Action â€” decision records are advisory only.
- A-019: Decision Records Are Immutable, Append-Only, and Live-Incompatible by default.
- A-020: Next phase defaults to strictest runtime decision contract.

**What is explicitly excluded:**

- No live trading path
- No decision-to-order bridge
- No automatic approval state changes
- No loop cycle replay or re-execution

---

## Â§48 Sprint 37 â€” Runtime Schema Binding & Decision Backbone Convergence

**Sprint**: 37 | **Datum**: 2026-03-21 | **Status**: Kanonisch

### Konvergenz-Entscheidung

`DecisionInstance` ist jetzt ein `TypeAlias` fÃ¼r `DecisionRecord`.
`DecisionRecord` (in `app/execution/models.py`) ist das einzige kanonische Datenmodell.
Die `journal.py`-API bleibt fÃ¼r CLI/MCP-KompatibilitÃ¤t, delegiert aber vollstÃ¤ndig auf `DecisionRecord`.

### Zwei-Schichten-Architektur (kanonisch)

| Schicht | Modul | Zweck |
|---|---|---|
| **Schema-IntegritÃ¤t** | `app/core/schema_binding.py` | PrÃ¼ft, ob die Schema-DATEI selbst korrekt ist (Struktur, Safety-Consts, Feld-Alignment). Boot-time check. Raises nie â€” gibt `SchemaValidationResult` zurÃ¼ck. |
| **Payload-Validierung** | `app/schemas/runtime_validator.py` | PrÃ¼ft, ob ein DATA-Payload das Schema einhÃ¤lt. Runtime check. Raises `SchemaValidationError` (fail-closed). |

Diese zwei Schichten sind komplementÃ¤r, nicht konkurrierend.
`app/core/settings.py::validate_json_schema_payload()` ist eine KompatibilitÃ¤ts-Wrapper-Funktion, die an `runtime_validator.py` delegiert.

### Runtime Schema Binding

| Schema | Kanonischer Validator | Wann aufgerufen |
|---|---|---|
| `DECISION_SCHEMA.json` | `app/schemas/runtime_validator.py::validate_json_schema_payload()` | `DecisionRecord._validate_safe_state()` â€” bei jeder Instanziierung |
| `CONFIG_SCHEMA.json` | `app/schemas/runtime_validator.py::validate_runtime_config_payload()` | `AppSettings.validate_runtime_contract()` â€” beim Settings-Startup |

### Public API â€” Payload-Validierung (`app/schemas/runtime_validator.py`)

| Funktion / Typ | Zweck |
|---|---|
| `validate_json_schema_payload(payload, *, schema_filename, label)` | Generische Payload-Validierung gegen beliebige bundled JSON Schema â€” raises `SchemaValidationError` |
| `validate_runtime_config_payload(payload)` | Config payload gegen CONFIG_SCHEMA.json â€” raises `SchemaValidationError` |
| `validate_decision_schema_payload(payload)` | Decision payload gegen DECISION_SCHEMA.json â€” raises `SchemaValidationError` |
| `validate_config_payload(payload)` | Alias fÃ¼r `validate_runtime_config_payload()` |
| `validate_decision_payload(payload)` | Alias fÃ¼r `validate_decision_schema_payload()` |
| `load_schema_document(schema_filename)` | Schema-Datei laden (lru_cache) â€” raises `SchemaValidationError` bei Fehler |
| `SchemaValidationError` | Subclass von `ValueError` â€” fail-closed Fehlertyp |

### Public API â€” Schema-IntegritÃ¤t (`app/core/schema_binding.py`)

| Funktion / Typ | Zweck |
|---|---|
| `validate_config_schema(schema_path)` | PrÃ¼ft CONFIG_SCHEMA.json: Struktur + Safety-Consts |
| `validate_decision_schema(schema_path)` | PrÃ¼ft DECISION_SCHEMA.json: 26+ Pflichtfelder + Mode-Enum |
| `validate_decision_schema_alignment(schema_path)` | PrÃ¼ft Feld-Deckung: Schema-Required âŠ† DecisionRecord.model_fields |
| `run_all_schema_validations(...)` | FÃ¼hrt alle drei Checks aus â€” gibt Liste von `SchemaValidationResult` zurÃ¼ck |
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
| `auto_approved_paper` | `not_required` | `approval_state` â€” gelÃ¶scht aus VALID_APPROVAL_STATES |
| `submitted` | `queued` | `execution_state` â€” Legacy-Mapping beim Laden |
| `filled` | `executed` | `execution_state` â€” Legacy-Mapping beim Laden |
| `partial` | `blocked` | `execution_state` â€” Legacy-Mapping beim Laden |
| `cancelled` | `failed` | `execution_state` â€” Legacy-Mapping beim Laden |
| `error` | `failed` | `execution_state` â€” Legacy-Mapping beim Laden |

### DECISION_SCHEMA.json: report_type-Regel

`report_type` ist in `properties` als optionales String-Feld definiert (nicht in `required`).
Grund: Legacy-Journal-Rows kÃ¶nnen `report_type: "decision_instance"` enthalten.
`_normalize_legacy_decision_payload()` strippt `report_type` vor der Validierung.
`DecisionRecord.to_json_dict()` (`model_dump(mode="json")`) emittiert kein `report_type`.

### Security Invariants

- `app/schemas/runtime_validator.py` ist die einzige kanonische Implementierung des Validators.
- `app/core/settings.py::validate_json_schema_payload()` ist ein KompatibilitÃ¤ts-Wrapper â€” kein zweiter Validator.
- `DecisionRecord._validate_safe_state()` ruft den Validator Ã¼ber `settings.py` â†’ `runtime_validator.py` auf.
- `AppSettings.validate_runtime_contract()` ruft `validate_runtime_config_payload()` direkt aus `runtime_validator.py` auf.
- `SchemaValidationError` ist Subclass von `ValueError` â€” alle bestehenden `except ValueError`-Handler greifen.
- Legacy-Rows werden beim Laden normalisiert, nicht beim Schreiben.
- Neue Rows werden immer im kanonischen Format gespeichert.
- Safety-Consts in CONFIG_SCHEMA.json: 10 Felder mit `const`-Constraints; `validate_config_schema()` verifiziert alle.

### Tests

- `tests/unit/test_schema_binding.py` â€” 14 Tests (Schema-IntegritÃ¤t, Safety-Consts, Alignment, Immutability)
- `tests/unit/test_schema_runtime_binding.py` â€” 25 Tests (Payload-Validierung, invalid enums, missing fields)
- `tests/unit/test_decision_journal.py` â€” 20 Tests (Konvergenz, Legacy-Normalisierung, Summary)
- `tests/unit/test_decision_record.py` â€” 9 Tests (Runtime-Schema-Binding, Safe-State-Validator)

---

## Â§49 Sprint 38+38C â€” Telegram Command Hardening & Canonical Read Surfaces

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
| `/positions` | read_only | `get_paper_positions_summary()` (MCP) | `research paper-positions-summary` | none; kein Live-Positions-Pfad |
| `/exposure` | read_only | `get_paper_exposure_summary()` (MCP) | `research paper-exposure-summary` | none |
| `/risk` | read_only | `get_protective_gate_summary()` (MCP) | `research gate-summary` | none |
| `/signals` | read_only | `get_signals_for_execution(limit=5)` (MCP) | `research signal-handoff` | kein Routing, keine Execution, kein Promote |
| `/journal` | read_only | `get_review_journal_summary()` (MCP) | `research review-journal-summary` | none |
| `/daily_summary` | read_only | `get_daily_operator_summary()` (MCP) | `research daily-summary` | none |
| `/approve <dec_ref>` | guarded_audit | audit-only: `artifacts/operator_commands.jsonl` | `research review-journal-append` | kein Live-Execution, kein Routing, kein State-Change |
| `/reject <dec_ref>` | guarded_audit | audit-only: `artifacts/operator_commands.jsonl` | `research review-journal-append` | kein Live-Execution, kein Routing, kein State-Change |
| `/pause` | guarded_write | `RiskEngine.pause()` â€” dry_run gated | â€” | kein Trading-Trigger |
| `/resume` | guarded_write | `RiskEngine.resume()` â€” dry_run gated | â€” | kein Trading-Trigger |
| `/kill` | guarded_write | `RiskEngine.trigger_kill_switch()` â€” 2-Step + dry_run gated | â€” | Notfall-Only |
| `/incident <note>` | guarded_audit | `get_escalation_summary()` (MCP) + audit-append | `research escalation-summary` | keine State-Mutation, kein Auto-Remediation |
| `/help` | read_only | static | â€” | none |

### Surface-Klassen (kanonisch)

| Klasse | Bedeutung |
|---|---|
| `read_only` | Kein Schreiben, kein State-Wechsel; via MCP canonical read tools |
| `guarded_audit` | Schreibt nur append-only Audit-Log â€” kein Execution-Seiteneffekt |
| `guarded_write` | Mutiert Risk-Engine-State â€” explizit dry_run gated |

**Hinweis zu `/incident`**: `guarded_audit` â€” liest zusaetzlich `get_escalation_summary()` (MCP) fuer Kontext.
Audit-Eintrag wird **immer** per `_audit()` vor dem Handler geschrieben â€” MCP-Fehler wird fail-closed abgefangen.

### Kanonische Inventory-Funktion

`get_telegram_command_inventory()` in `app/messaging/telegram_bot.py` ist die maschinenlesbare Vertragsdefinition.
Sie liefert `read_only_commands`, `guarded_audit_commands`, `canonical_research_refs`.
`test_telegram_command_inventory_references_registered_cli_research_commands` MUSS gruen sein.

### Klassifikations-Invarianten (Sprint 38C)

- `_READ_ONLY_COMMANDS` = `{status, health, positions, risk, signals, journal, daily_summary}` â€” 7 Eintraege
- `_GUARDED_AUDIT_COMMANDS` = `{approve, reject, incident}` â€” 3 Eintraege
- `incident` ist NICHT in `_READ_ONLY_COMMANDS` â€” Klassifikationskonflikt Sprint 38 bereinigt (Sprint 38C)
- Disjunkte Sets: kein Command darf in beiden Sets erscheinen
- `exposure` und `help` sind static stubs â€” kein Canonical-Ref, kein Set-Eintrag notwendig

### decision_ref Format

`/approve` und `/reject` akzeptieren nur: `dec_` + 12 Hex-Zeichen (`^dec_[0-9a-f]{12}$`).
Ungueltige Refs: fail-closed Fehlermeldung. Implementierung: `_DECISION_REF_PATTERN` + `_validate_decision_ref()`.

### Telegram Safety Boundary (nicht verhandelbar)

- Telegram = Operator-Surface, NICHT Execution-Surface
- `/approve` und `/reject` = audit-only â€” kein Live-Execution-Pfad
- Kein Trading ueber Telegram
- Kein Auto-Routing ueber Telegram
- Kein Auto-Promote ueber Telegram
- Keine ungepruefte Telegram-Aktion mit Core-State-Wirkung
- Kein Auto-Remediation via `/incident`
- Alle read_only MCP-Antworten muessen `execution_enabled=False` und `write_back_allowed=False` enthalten

### Security Invariants

- I-266 bis I-277 in `docs/intelligence_architecture.md` (Sprint 38)
- Kanonische Command-Surface-Definition in `TELEGRAM_INTERFACE.md`
- Alle guarded_write Kommandos dry_run gated â€” default safe
- Alle Kommandos audit-geloggt vor Handler-Ausfuehrung
- Admin-Gating fail-closed â€” Unauthorized = logged + generic response

### Assumptions Referenced

- A-004: Telegram Bot Commands are Admin-Gated
- A-027 bis A-031 (Sprint 38) in `ASSUMPTIONS.md`

### Gelieferte Dateien (Sprint 38+38C)

- `app/messaging/telegram_bot.py` â€” `_READ_ONLY_COMMANDS`/`_GUARDED_AUDIT_COMMANDS`, alle MCP-Bindings, `_validate_decision_ref()`, `get_telegram_command_inventory()`
- `tests/unit/test_telegram_bot.py` â€” 28 Tests (admin gating, MCP surface bindings, fail-closed, guarded_write, approve/reject audit-only, inventory)
- `TELEGRAM_INTERFACE.md` â€” kanonischer Operator-Surface-Contract
- `docs/contracts.md Â§49` â€” final
- `docs/intelligence_architecture.md` I-266â€“I-277
- `ASSUMPTIONS.md` A-027â€“A-031

### Tests (Sprint 38+38C)

- `tests/unit/test_telegram_bot.py` â€” 28 Tests (alle gruen)
  - Admin gating (authorized vs. unauthorized)
  - Unknown command â†’ fail-closed
  - `/kill` Zwei-Schritt-Confirm
  - dry_run: `/pause` â†’ kein State-Wechsel
  - Audit-Log-Eintrag pro Command
  - Alle 8 read_only Commands â†’ korrekter MCP-Loader aufgerufen
  - `/incident` â†’ guarded_audit + MCP + Audit-Log
  - fail-closed bei MCP-Surface-Fehler
  - fail-closed bei ungueltigen CLI-Refs
  - `/approve` und `/reject` â†’ audit-only, kein Execution-Seiteneffekt
  - Read-only commands mutieren keinen Runtime-State
  - `/help` listet alle 14 gehÃ¤rteten Commands
  - `get_telegram_command_inventory()` â†’ alle CLI-refs valid

---

## Â§50 â€” Market Data Layer: Read-Only Adapter Contract (Sprint 39)

### Zweck

Definiert den einzigen kanonischen read-only Market-Data-Contract, auf dem Signale, Backtests und Operator-Surfaces sicher aufbauen kÃ¶nnen. Kein Execution-Pfad, keine Routing-Entscheidung, keine Order-Submission darf aus diesem Layer entstehen.

---

### Â§50.1 â€” Kanonisches Datenmodell: `MarketDataPoint`

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
- `frozen=True` â€” unveraenderlich nach Erstellung
- `timestamp_utc` MUSS UTC-aware sein â€” naive datetimes sind ungueltig
- `source` MUSS durch den Adapter gesetzt werden â€” niemals durch den Consumer inferiert
- `is_stale=True` signalisiert Degradation â€” Consumer MUSS fail-closed reagieren
- `freshness_seconds` ist informativ â€” Stale-Entscheidung liegt beim Adapter, nicht beim Consumer

---

### Â§50.2 â€” Unterstuetzende Datenmodelle

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

### Â§50.3 â€” Adapter-Interface: `BaseMarketDataAdapter`

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
- `health_check()` gibt `False` zurueck bei Fehler â€” wirft nie

**Read-Only-Invariante** (nicht verhandelbar):
- Keine Adapter-Methode darf Orders senden, Positionen oeffnen, oder Execution-State mutieren
- Adapter sind passive Daten-Quellen â€” sie haben keine Schreibrechte auf Broker-Systeme
- Kein Adapter-Konstruktor darf Broker-Credentials fuer Schreibzugriff initialisieren

---

### Â§50.4 â€” Default-Adapter: `MockMarketDataAdapter`

**Implementierung**: `app/market_data/mock_adapter.py`

- **Deterministische sinusoidale Preise**: Kein `random()`, kein Zufall, kein externes Netzwerk
- **Hash-basierte Phase**: Jedes Symbol hat eine stabile, reproduzierbare Preisbewegung
- **Basis-Preise**: BTC/USDT (43000), ETH/USDT (2600), BNB/USDT (380), SOL/USDT (105), AAPL (185), MSFT (415), SPY (510)
- **24h-Periode**: Sinusoid mit konfigurierbarer Amplitude (`amplitude_pct`)
- **`adapter_name`**: `"mock"`
- **Verwendung**: Pflicht-Default fuer Paper-Trading und alle Unit-Tests ohne externe Abhaengigkeiten

**Invariante**: Tests, die spezifische Preise erwarten, MUESSEN `MockMarketDataAdapter` verwenden. Der Mock darf nicht durch echte Marktdaten ersetzt werden, ohne Tests zu aktualisieren.

---

### Â§50.5 â€” Freshness-Semantik

| Feld | Bedeutung | Wer setzt es |
|---|---|---|
| `is_stale` | `True` = Datenpunkt ausserhalb der konfigurierten Freshness-Schwelle | Adapter |
| `freshness_seconds` | Alter in Sekunden seit API-Abruf | Adapter |
| `timestamp_utc` | UTC-Zeitstempel des Datenpunkts (nicht des Abrufs) | Adapter |

**Consumer-Regeln**:
- `is_stale=True` â†’ TradingLoop ueberspringt den Zyklus fuer dieses Symbol (`no_market_data:symbol`)
- `None`-Return â†’ TradingLoop ueberspringt den Zyklus (identische Behandlung wie stale)
- Consumer DARF `is_stale` nicht ueberschreiben oder ignorieren
- Consumer DARF NICHT automatisch auf einen anderen Provider umschalten (kein Auto-Routing)

---

### Â§50.6 â€” Provenance-Semantik

- `MarketDataPoint.source` ist ein Provider-Identifier (z.B. `"mock"`, `"binance"`, `"alpaca"`)
- Der Adapter setzt `source` â€” der Consumer liest nur
- Signale, die aus einem `MarketDataPoint` abgeleitet werden, SOLLEN `source` im Signal-Kontext propagieren (Traceability)
- `source` ist KEIN Routing-Signal und KEIN Permission-Check â€” es ist ein Provenance-Tag

---

### Â§50.7 â€” Failure- und Degradations-Semantik

| Szenario | Adapter-Verhalten | Consumer-Verhalten |
|---|---|---|
| Transient-Netzwerkfehler | `None` zurueckgeben, intern loggen | Zyklus ueberspringen |
| Symbol unbekannt | `None` zurueckgeben | Zyklus ueberspringen |
| Datenpunkt veraltet | `MarketDataPoint(is_stale=True)` | Zyklus ueberspringen |
| Provider down | `health_check()` â†’ `False` | Kein Auto-Routing |
| OHLCV leer | `[]` zurueckgeben | Keine Analyse, kein Signal |
| Exception intern | Fangen, loggen, `None`/`[]` | Zyklus ueberspringen |

**Fail-Closed-Invariante**: Fehlende oder veraltete Marktdaten fuehren NIEMALS zu einer Execution-Entscheidung. Ein Zyklus ohne valide Marktdaten ist ein uebersprungener Zyklus â€” kein Fehler, kein Alarm.

---

### Â§50.8 â€” TradingLoop-Integration

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

### Â§50.9 â€” BacktestEngine-Integration

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

### Â§50.10 â€” Adapter-Auswahl und Konfiguration

- Die Auswahl des Adapters ist **explizite Konfiguration** (Settings / Dependency Injection)
- Kein Auto-Routing zwischen Adaptern (keine Fallback-Kette)
- `MockMarketDataAdapter` ist der Default fuer alle Nicht-Live-Umgebungen (A-003 bestaetigt)
- Ein echter externer Adapter (z.B. Binance, Alpaca) MUSS `BaseMarketDataAdapter` vollstaendig implementieren
- Unvollstaendige Implementierungen MUESSEN `NotImplementedError` werfen â€” kein Silent-None

---

### Â§50.11 â€” Provider Health und Routing

- `health_check()` returning `False` bedeutet: Provider nicht erreichbar
- `health_check()` ist ein **Liveness-Signal** fuer Monitoring â€” kein Routing-Trigger
- `False` darf NICHT automatisch einen anderen Provider aktivieren
- `False` darf NICHT als "stop trading"-Signal interpretiert werden (das ist Aufgabe des RiskEngine Kill-Switch)
- Health-Check-Ergebnis KANN in Operator-Surface (`/health` â†’ MCP `get_provider_health()`) surfaced werden

---

### Â§50.12 â€” Tests (Sprint 39 Ziele)

- `tests/unit/test_mock_adapter.py` â€” MockAdapter-Tests: Determinismus, None-Handling, health_check, MarketDataPoint-Felder
- `tests/unit/test_market_data_models.py` â€” Modell-Frozen-Tests, is_stale-Semantik, Timestamp-UTC-Validierung
- `tests/unit/test_base_adapter.py` â€” ABC-Konformitaet, health_check-Default-Verhalten
- Gesamtziel: >= 15 neue Tests im Market-Data-Layer

---

### Assumptions Referenced

- A-003: MockMarketDataAdapter ist Default fuer Paper-Trading
- A-032 bis A-036 (Sprint 39) in `ASSUMPTIONS.md`

### Intelligence Invariants

- I-281 bis I-290 in `docs/intelligence_architecture.md` (Sprint 39)

### Gelieferte Dateien (Sprint 39 â€” Definition)

- `docs/contracts.md Â§50` â€” kanonischer Market-Data-Layer-Contract (dieses Dokument)
- `docs/intelligence_architecture.md` I-281â€“I-290 â€” Market-Data-Invarianten
- `ASSUMPTIONS.md` A-032â€“A-036 â€” Market-Data-Annahmen
- `AGENTS.md` P45 â€” Sprint-39-Pattern
- `TASKLIST.md` Sprint-39-Block

### Â§50.13 - Sprint 39C Runtime Consolidation (implemented)

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

---

## Â§51 â€” Paper Portfolio Read Surface & Exposure Summary (Sprint 40)

### Zweck

Definiert den einzigen kanonischen read-only Portfolio-/Positions-/Exposure-Contract.
Portfolio Surface = reine Zustandsansicht (Observation), kein Rebalancing, keine Order, keine Mutation.
Mark-to-Market = Bewertung offener Positionen zum aktuellen Preis â€” keine Execution-Freigabe.
Exposure = aggregierte Risikobeobachtung â€” kein Rebalancing-Trigger.

---

### Â§51.1 â€” Kanonisches Datenmodell: `PositionSnapshot` *(Spec-Stand; superseded by Â§51.11)*

> **Achtung**: Diese Sektion beschreibt die ursprÃ¼ngliche Spec. Implementierter Name: `PositionSummary`.
> Kanonischer Pfad: `app/execution/portfolio_read.py`. Verbindlicher Stand: Â§51.11.

**Implementierung**: ~~`app/research/portfolio_surface.py`~~ â†’ `app/execution/portfolio_read.py` (Â§51.11)

```python
@dataclass(frozen=True)
class PositionSnapshot:
    position_id: str          # "pos_" + sha1(symbol+opened_at)[:12] -- deterministisch
    symbol: str               # Kanonisches Symbol (z.B. "BTC/USDT")
    side: str                 # "long" | "short" (PaperEngine: derzeit nur "long")
    quantity: float           # Gehaltene Menge
    entry_price: float        # Durchschnittlicher Einstandspreis (avg_entry_price)
    current_price: float | None      # Aktueller Preis aus MarketDataSnapshot; None = kein MtM
    unrealized_pnl_usd: float | None # (current_price - entry_price) * quantity; None ohne MtM
    position_value_usd: float        # quantity * (current_price or entry_price)
    stop_loss: float | None
    take_profit: float | None
    opened_at: str            # UTC ISO -- Zeitpunkt des ersten Fills
    as_of: str                # UTC ISO -- Zeitpunkt der Snapshot-Erstellung
    market_data_source: str | None   # MarketDataSnapshot.provider ("coingecko", "mock", etc.)
    is_mark_to_market: bool          # True wenn current_price verfuegbar und nicht stale
    execution_enabled: bool = False
    write_back_allowed: bool = False
```

**Invarianten**:
- `frozen=True` -- unveraenderlich nach Erstellung
- `execution_enabled=False` und `write_back_allowed=False` IMMER -- kein Override erlaubt
- `position_id` ist deterministisch: sha1(symbol+opened_at)[:12], "pos_"-Prefix
- `is_mark_to_market=False` wenn `current_price is None` oder `MarketDataSnapshot.is_stale=True`
- `position_value_usd` faellt auf `quantity * entry_price` zurueck wenn kein MtM verfuegbar
- `unrealized_pnl_usd=None` wenn kein MtM -- Consumer muss None-Fall behandeln

---

### Â§51.2 â€” Kanonisches Datenmodell: `PaperPortfolioSnapshot` *(Spec-Stand; superseded by Â§51.11)*

> **Achtung**: Diese Sektion beschreibt die ursprÃ¼ngliche Spec. Implementierter Name: `PortfolioSnapshot`.
> Kanonischer Pfad: `app/execution/portfolio_read.py`. Verbindlicher Stand: Â§51.11.

**Implementierung**: ~~`app/research/portfolio_surface.py`~~ â†’ `app/execution/portfolio_read.py` (Â§51.11)

```python
@dataclass(frozen=True)
class PaperPortfolioSnapshot:
    as_of: str                        # UTC ISO -- Zeitpunkt der Snapshot-Erstellung
    initial_equity: float             # Startkapital bei Engine-Init
    cash: float                       # Aktuell verfuegbares Cash (nach Fills)
    cash_pct: float                   # cash / total_equity_usd * 100
    open_position_count: int          # Anzahl offener Positionen
    positions: tuple[PositionSnapshot, ...]  # Alle offenen Positionen (tuple = immutable)
    total_equity_usd: float           # cash + sum(position_value_usd)
    realized_pnl_usd: float           # Realisierter PnL gesamt
    unrealized_pnl_usd: float | None  # Summe aller unrealized_pnl_usd; None wenn kein MtM
    total_fees_usd: float             # Gesamte Gebuehren
    trade_count: int                  # Anzahl abgeschlossener Fills
    is_mark_to_market: bool           # True wenn mind. eine Position MtM-Preis hat
    market_data_source: str | None    # Provider-Name (aus PositionSnapshots)
    execution_enabled: bool = False
    write_back_allowed: bool = False
```

**Invarianten**:
- `frozen=True`
- `positions` ist ein `tuple` (nicht `list`) -- Unveraenderlichkeit garantiert
- `execution_enabled=False`, `write_back_allowed=False` IMMER
- `cash_pct = 0.0` wenn `total_equity_usd <= 0` (Division-by-Zero-Schutz)
- `unrealized_pnl_usd=None` wenn KEINE Position `is_mark_to_market=True`
- `is_mark_to_market=True` genau dann, wenn mind. eine Position `is_mark_to_market=True`

---

### Â§51.3 â€” Kanonisches Datenmodell: `ExposureSummary` *(Spec-Stand; superseded by Â§51.11)*

> **Achtung**: Diese Sektion beschreibt die ursprÃ¼ngliche Spec.
> Kanonischer Pfad: `app/execution/portfolio_read.py`. Verbindlicher Stand: Â§51.11.

**Implementierung**: ~~`app/research/portfolio_surface.py`~~ â†’ `app/execution/portfolio_read.py` (Â§51.11)

```python
@dataclass(frozen=True)
class ExposureSummary:
    as_of: str                         # UTC ISO
    total_exposure_usd: float          # Summe aller position_value_usd
    cash_usd: float                    # Verfuegbares Cash
    total_equity_usd: float            # total_exposure_usd + cash_usd
    cash_pct: float                    # cash_usd / total_equity_usd * 100
    position_count: int                # Anzahl offener Positionen
    largest_position_symbol: str | None  # Symbol mit groesstem position_value_usd
    largest_position_pct: float | None   # groesste Position / total_equity_usd * 100
    is_mark_to_market: bool
    market_data_source: str | None
    execution_enabled: bool = False
    write_back_allowed: bool = False
```

**Invarianten**:
- `frozen=True`
- `execution_enabled=False`, `write_back_allowed=False` IMMER
- `largest_position_symbol=None` wenn `position_count == 0`
- `cash_pct = 100.0` wenn `position_count == 0`
- `ExposureSummary` ist eine Projektion von `PaperPortfolioSnapshot` -- kein eigenstaendiger Backend-Pfad

---

### Â§51.4 â€” ~~Research-Modul: `app/research/portfolio_surface.py`~~ *(Spec-Stand; superseded by Â§51.11)*

> **Achtung**: Diese Sektion beschreibt die ursprÃ¼ngliche Spec. TatsÃ¤chliche Module: `app/execution/portfolio_read.py`
> (Operator-Surface) und `app/execution/portfolio_surface.py` (interner TradingLoop-Helper).
> Verbindlicher Stand: Â§51.11.

**Kanonisches Modul** (tatsÃ¤chlich implementiert): `app/execution/portfolio_read.py`

```python
def build_position_snapshot(
    pos: PaperPosition,
    *,
    snapshot: MarketDataSnapshot | None = None,
    as_of: str,
) -> PositionSnapshot: ...

def build_paper_portfolio_snapshot_from_audit(
    audit_path: str | Path,
    *,
    market_data_snapshots: dict[str, MarketDataSnapshot] | None = None,
) -> PaperPortfolioSnapshot: ...

def build_exposure_summary(portfolio: PaperPortfolioSnapshot) -> ExposureSummary: ...
```

**Kanonische Source of Truth**: `artifacts/paper_execution_audit.jsonl`
- `event_type == "order_filled"` Zeilen werden replayed, um aktuelle Positionen zu rekonstruieren
- Identisch zum Pattern von `load_decision_records()`, `load_signal_handoffs()` etc.
- Kein direkter Zugriff auf laufende `PaperExecutionEngine`-Instanz
- Replay ist deterministisch und idempotent bei identischem JSONL-Inhalt
- Audit-JSONL nicht vorhanden â†’ leeres Portfolio (0 Positionen, cash=0)

**Mark-to-Market (optional)**:
- Wird aktiviert, wenn der Caller `provider` angibt (nicht "mock" bei echten Preisen)
- Ruft `get_market_data_snapshot(symbol, provider)` pro gehaltener Position auf
- `MarketDataSnapshot.is_stale=True` â†’ MtM fuer diese Position verworfen (fail-closed)
- `MarketDataSnapshot.available=False` â†’ MtM fuer diese Position verworfen (fail-closed)
- MtM-Fehler verhindert NICHT den Portfolio-Snapshot -- Fallback auf `entry_price`

---

### Â§51.5 â€” MCP Tools (Sprint 40 -- neu, canonical_read)

**Implementierung**: `app/agents/mcp_server.py`

**`get_paper_portfolio_snapshot`**:
```python
async def get_paper_portfolio_snapshot(
    audit_log_path: str = "artifacts/paper_execution_audit.jsonl",
    provider: str = "mock",
    freshness_threshold_seconds: float = 120.0,
) -> dict[str, object]: ...
```
- Liest Audit-JSONL, rekonstruiert Positionen per Fill-Replay
- Optionale MtM-Bereicherung via `get_market_data_snapshot()` pro Position
- Antwort enthaelt immer `execution_enabled=False`, `write_back_allowed=False`
- In `_CANONICAL_MCP_READ_TOOL_NAMES` eingetragen

**`get_paper_positions_summary`**:
```python
async def get_paper_positions_summary(
    audit_log_path: str = "artifacts/paper_execution_audit.jsonl",
    provider: str = "mock",
    freshness_threshold_seconds: float = 120.0,
) -> dict[str, object]: ...
```
- Delegiert intern an Portfolio-Snapshot, projiziert auf PositionSummary-Liste
- Antwort enthaelt immer `execution_enabled=False`, `write_back_allowed=False`
- In `_CANONICAL_MCP_READ_TOOL_NAMES` eingetragen

**`get_paper_exposure_summary`**:
```python
async def get_paper_exposure_summary(
    audit_log_path: str = "artifacts/paper_execution_audit.jsonl",
    provider: str = "mock",
    freshness_threshold_seconds: float = 120.0,
) -> dict[str, object]: ...
```
- Delegiert intern an Portfolio-Snapshot, projiziert auf ExposureSummary
- Antwort enthaelt immer `execution_enabled=False`, `write_back_allowed=False`
- In `_CANONICAL_MCP_READ_TOOL_NAMES` eingetragen

---

### Â§51.6 â€” CLI Commands (Sprint 40 -- neu)

```bash
python -m app.cli.main research paper-portfolio-snapshot [--provider mock] [--audit-log ...]
python -m app.cli.main research paper-positions-summary [--provider mock] [--audit-log ...]
python -m app.cli.main research paper-exposure-summary [--provider mock] [--audit-log ...]
```

- Beide read-only, kein State-Change
- Registriert in `get_registered_research_command_names()`

---

### Â§51.7 â€” Telegram Surface Update (Sprint 40)

| Command | Vor Sprint 40 | Nach Sprint 40 |
|---|---|---|
| `/positions` | `get_handoff_collector_summary` (Proxy) | `get_paper_positions_summary` (MCP canonical) |
| `/exposure` | Stub (kein Backing) | `get_paper_exposure_summary` (MCP canonical) |

**Aenderungen in `telegram_bot.py`** (Codex):
- `"exposure"` wird zu `_READ_ONLY_COMMANDS` hinzugefuegt
- `TELEGRAM_CANONICAL_RESEARCH_REFS["positions"]` = `("research paper-positions-summary",)`
- `TELEGRAM_CANONICAL_RESEARCH_REFS["exposure"]` = `("research paper-exposure-summary",)`
- Neue `_get_paper_positions_summary()` und `_get_paper_exposure_summary()` Loader-Methoden
- `_cmd_positions` nutzt `_get_paper_positions_summary` (ersetzt `_get_handoff_collector_summary`)
- `_cmd_exposure` nutzt `_get_paper_exposure_summary` (ersetzt Stub)

`get_handoff_collector_summary` bleibt als eigenstaendiges MCP-Tool erhalten (kein Breaking Change).

---

### Â§51.8 â€” Fail-Closed- und Degradations-Semantik

| Szenario | Adapter-Verhalten | Consumer-Verhalten |
|---|---|---|
| Audit-JSONL nicht vorhanden | Leeres Portfolio (0 Pos., cash=0) | Anzeige: "no positions" |
| Audit-JSONL malformed | fail-closed (`available=False`) | Keine operative Nutzung des Snapshots |
| MtM-Abruf schlaegt fehl | is_mark_to_market=False fuer Position | Fallback entry_price |
| Stale MtM-Daten | is_mark_to_market=False fuer Position | Fallback entry_price |
| Provider-Fehler | MtM degradiert oder fail-closed | Nur read-only Sicht, keine Execution-Freigabe |

---

### Â§51.9 â€” Sicherheitsinvarianten (nicht verhandelbar)

- Portfolio Surface ist ausschliesslich read-only
- `PaperPortfolio` (mutable) wird NIE direkt exposed -- nur `PaperPortfolioSnapshot` (frozen)
- Kein Pfad von `/positions` oder `/exposure` zur Execution
- Mark-to-Market ist Bewertung, keine Execution-Freigabe
- Exposure-Zusammenfassung loest kein Rebalancing aus
- Audit-JSONL ist append-only -- der Read-Layer schreibt NIE zurueck

---

### Assumptions Referenced

- A-032 (Sprint 38 Addendum): Handoff-Collector-Proxy gilt bis Sprint 40 als provisional (abgeloest)
- A-040--A-044 (Sprint 40) in `ASSUMPTIONS.md`

### Intelligence Invariants

- I-291--I-300 in `docs/intelligence_architecture.md` (Sprint 40)

### Gelieferte Dateien (Sprint 40 -- Definition)

- `docs/contracts.md Â§51` (dieses Dokument)
- `docs/intelligence_architecture.md` I-291--I-300
- `ASSUMPTIONS.md` A-040--A-044
- `AGENTS.md` P46
- `TASKLIST.md` Sprint-40-Block

### Â§51.10 - Sprint 40C Runtime Consolidation (implemented)

Der kanonische Runtime-Pfad ist als read-only Surface umgesetzt:

- Backend-Projektion: `app/execution/portfolio_read.py`
- Kanonische Modelle:
  - `PortfolioSnapshot`
  - `PositionSummary`
  - `ExposureSummary`
- Datenquelle: append-only `artifacts/paper_execution_audit.jsonl` (Replay, keine Mutation)
- Optionale Mark-to-Market-Anreicherung: bestehender `app.market_data.service.get_market_data_snapshot()`

Finale CLI-Surfaces:

- `research paper-portfolio-snapshot`
- `research paper-positions-summary`
- `research paper-exposure-summary`

Finale MCP-Surfaces:

- `get_paper_portfolio_snapshot`
- `get_paper_positions_summary`
- `get_paper_exposure_summary`

Finale Telegram-Bindings:

- `/positions` -> kanonischer Positions-Read (`get_paper_positions_summary`)
- `/exposure` -> kanonischer Exposure-Read (`get_paper_exposure_summary`)

Sicherheitsgrenzen:

- Read-only only, kein Broker/Order/Execution-Pfad
- Kein Auto-Routing, kein Auto-Promote
- `execution_enabled=False` und `write_back_allowed=False` in allen Responses
- Fail-closed bei ungÃ¼ltigem Audit-Payload und vollstÃ¤ndig fehlender MtM-Bewertung offener Positionen

---

### Â§51.11 â€” Sprint 40C: Finaler Kanonischer Zustand (Consolidation)

> **Diese Sektion ueberschreibt Â§51.1â€“Â§51.9 bei allen Namens- und Pfad-Konflikten.**
> Â§51.1â€“Â§51.9 wurden vor der Implementierung geschrieben und verwendeten vorlaeuflge Namen.
> Â§51.10 und Â§51.11 sind der verbindliche implementierte Stand.

---

#### Kanonisches Modul (Operator Surfaces)

**`app/execution/portfolio_read.py`** â€” einzige kanonische Implementierung fuer MCP/CLI/Telegram

| Model | Felder | Invariante |
|---|---|---|
| `PositionSummary` (frozen) | symbol, quantity, avg_entry_price, stop_loss, take_profit, market_price, market_value_usd, unrealized_pnl_usd, provider, market_data_retrieved_at_utc, market_data_source_timestamp_utc, market_data_is_stale, market_data_freshness_seconds, market_data_available, market_data_error | market_price/value/pnl = None wenn unavailable |
| `ExposureSummary` (frozen) | priced_position_count, stale_position_count, unavailable_price_count, gross_exposure_usd, net_exposure_usd, largest_position_symbol, largest_position_weight_pct, mark_to_market_status, execution_enabled=False, write_back_allowed=False | execution_enabled=False IMMER |
| `PortfolioSnapshot` (frozen) | generated_at_utc, source, audit_path, cash_usd, realized_pnl_usd, total_market_value_usd, total_equity_usd, position_count, positions: tuple[PositionSummary], exposure_summary, available, error, execution_enabled=False, write_back_allowed=False | execution_enabled=False IMMER |

**Kanonische Funktionen**:
- `build_portfolio_snapshot(audit_path, provider, freshness_threshold_seconds, timeout_seconds)` â†’ async â†’ `PortfolioSnapshot`
- `build_positions_summary(snapshot)` â†’ `dict` (positions-only projection)
- `build_exposure_summary(snapshot)` â†’ `dict` (exposure-only projection)

---

#### Internes Modul (TradingLoop-Seite)

**`app/execution/portfolio_surface.py`** â€” NICHT fuer Operator Surfaces

- `PortfolioSummary`, `PositionSnapshot`, `ExposureSummary` â€” frozen, aber OHNE `execution_enabled`/`write_back_allowed`
- `build_portfolio_summary(portfolio, prices)` â€” arbeitet auf lebendem `PaperPortfolio`-Objekt
- `build_exposure_summary(portfolio, prices)` â€” arbeitet auf lebendem `PaperPortfolio`-Objekt
- Scope: TradingLoop-interne Formatierung (to_telegram_text, to_dict)
- DARF NICHT von MCP-Tools, CLI-Commands oder Telegram-Handlern importiert werden

---

#### Finale MCP-Tool-Namen (in `_CANONICAL_MCP_READ_TOOL_NAMES`)

| Tool | Delegiert an | Report-Type |
|---|---|---|
| `get_paper_portfolio_snapshot` | `build_portfolio_snapshot()` â†’ `PortfolioSnapshot.to_json_dict()` | `paper_portfolio_snapshot` |
| `get_paper_positions_summary` | `build_portfolio_snapshot()` â†’ `build_positions_summary()` | `paper_positions_summary` |
| `get_paper_exposure_summary` | `build_portfolio_snapshot()` â†’ `build_exposure_summary()` | `paper_exposure_summary` |

---

#### Finale CLI-Command-Namen

| Command | Parameter |
|---|---|
| `research paper-portfolio-snapshot` | `--audit-path`, `--provider`, `--freshness-threshold-seconds`, `--timeout-seconds` |
| `research paper-positions-summary` | identisch |
| `research paper-exposure-summary` | identisch |

---

#### Finale Telegram-Bindings

| Command | `_READ_ONLY_COMMANDS` | MCP-Loader | `TELEGRAM_CANONICAL_RESEARCH_REFS` |
|---|---|---|---|
| `/positions` | âœ… | `_get_paper_positions_summary()` â†’ `get_paper_positions_summary` | `("research paper-positions-summary",)` |
| `/exposure` | âœ… (seit Sprint 40) | `_get_paper_exposure_summary()` â†’ `get_paper_exposure_summary` | `("research paper-exposure-summary",)` |

---

#### Datenpfad (kanonisch, ein Pfad)

```
artifacts/paper_execution_audit.jsonl (append-only)
    â†“ _replay_paper_audit() â€” order_created + order_filled replay
    â†“ build_portfolio_snapshot() â€” async, MtM via get_market_data_snapshot()
PortfolioSnapshot (frozen, execution_enabled=False)
    â”œâ”€â”€ build_positions_summary() â†’ JSON (paper_positions_summary)
    â””â”€â”€ build_exposure_summary() â†’ JSON (paper_exposure_summary)
         â†‘ via ExposureSummary.to_json_dict()
```

---

#### Fail-Closed-Semantik (final)

| Szenario | `PortfolioSnapshot.available` | `PositionSummary.market_data_available` |
|---|---|---|
| Audit nicht vorhanden | True (leeres Portfolio) | n/a |
| Audit malformed | False + error gesetzt | n/a |
| sell ohne Position im Audit | False + error gesetzt | n/a |
| MtM-Preis unavailable | True (Position bleibt) | False |
| MtM-Preis stale | True (Position bleibt) | False, market_data_is_stale=True |
| Alle Positionen unbepreist | False + error | False fuer alle |

---

#### Tests (Sprint 40 â€” implementiert)

| Datei | Anzahl Tests | Scope |
|---|---|---|
| `tests/unit/test_portfolio_read.py` | Teil der 32 Portfolio-Tests | `portfolio_read.py` Modelle + Builder |
| `tests/unit/test_portfolio_surface.py` | Teil der 32 Portfolio-Tests | `portfolio_surface.py` (intern) |
| `tests/unit/test_mcp_portfolio_read.py` | Teil der 32 Portfolio-Tests | MCP-Tools |
| `tests/unit/test_cli_portfolio_read.py` | Teil der 32 Portfolio-Tests | CLI-Commands |
| **Gesamt Sprint 40** | **32 neue Tests** | Alle gruen |

**Test-Stand**: 1426 passed, ruff clean (2026-03-21)

---

#### Annahmen und Invarianten (Sprint 40C â€” korrigiert)

- `docs/intelligence_architecture.md` I-291â€“I-300 (Sprint 40C korrigiert)
- `ASSUMPTIONS.md` A-040â€“A-044 (Sprint 40C korrigiert)
- `AGENTS.md` P46 (Sprint 40C âœ… abgeschlossen)

---

## Â§52 Sprint 41 â€” TradingLoop Control Plane & Cycle Audit Surface

### Â§52.1 Scope & Nicht-Verhandelbar

Sprint 41 definiert und konsolidiert den kanonischen Control-Plane-Surface fÃ¼r den vorhandenen `TradingLoop`. Der Sprint ergÃ¤nzt paper- und shadow-only run-once-Execution-FunktionalitÃ¤t. Alle Live-, Broker- und autonomen Execution-Pfade bleiben verboten.

**Erlaubte Modi**: `"paper"` | `"shadow"` (`ExecutionMode.PAPER` | `ExecutionMode.SHADOW`)
**Verbotene Modi**: `"live"` und alle anderen Werte â€” immer fail-closed abgewiesen
**Control Plane = operator-triggered**: kein Daemon, kein Auto-Scheduler, keine Hintergrundschleife
**run-once = paper/shadow only**: ein MCP/CLI-Aufruf = ein Zyklus, kein Auto-Retry, kein Batching

### Â§52.2 Kanonischer Modul-Pfad

Alle Sprint-41-Kernfunktionen liegen in **`app/orchestrator/trading_loop.py`** (kein separates neues Modul).

**Relevante Module:**
- `app/orchestrator/trading_loop.py` â€” TradingLoop-Klasse + alle Control-Plane-Builder
- `app/orchestrator/models.py` â€” LoopStatusSummary, RecentCyclesSummary, LoopCycle, CycleStatus
- ~~`app/orchestrator/loop_surface.py`~~ â€” **ENTFERNT** (Sprint 41C). Ã„lteres paralleles Modul (LoopStatusReport, CycleSummary, RecentCyclesReport). Kein Code auf dem Filesystem. Nicht referenzieren.

### Â§52.3 Modelle (bereits implementiert)

#### `LoopStatusSummary` (`app/orchestrator/models.py`) âœ…

```python
@dataclass(frozen=True)
class LoopStatusSummary:
    mode: str                     # "paper" | "shadow" (ExecutionMode.value)
    run_once_allowed: bool        # True wenn mode in {paper, shadow}
    run_once_block_reason: str | None  # Blockierungsgrund oder None
    total_cycles: int
    last_cycle_id: str | None
    last_cycle_status: str | None     # CycleStatus.value oder None
    last_cycle_symbol: str | None
    last_cycle_completed_at: str | None
    audit_path: str
    auto_loop_enabled: bool = False   # invariant â€” kein autonomer Loop
    execution_enabled: bool = False   # invariant
    write_back_allowed: bool = False  # invariant
```

`to_json_dict()` â†’ `report_type: "trading_loop_status_summary"`

#### `RecentCyclesSummary` (`app/orchestrator/models.py`) âœ…

```python
@dataclass(frozen=True)
class RecentCyclesSummary:
    total_cycles: int
    status_counts: dict[str, int]
    recent_cycles: tuple[dict[str, object], ...]
    last_n: int
    audit_path: str
    auto_loop_enabled: bool = False
    execution_enabled: bool = False
    write_back_allowed: bool = False
```

`to_json_dict()` â†’ `report_type: "recent_trading_cycles_summary"`

### Â§52.4 Builder-Funktionen (bereits implementiert, `app/orchestrator/trading_loop.py`)

```python
def build_loop_status_summary(
    *, audit_path: str | Path = _AUDIT_LOG, mode: str | ExecutionMode = ExecutionMode.PAPER,
) -> LoopStatusSummary:
    """Read-only. Liest trading_loop_audit.jsonl. Never-raise."""

def build_recent_cycles_summary(
    *, audit_path: str | Path = _AUDIT_LOG, last_n: int = 20,
) -> RecentCyclesSummary:
    """Read-only. Liest trading_loop_audit.jsonl. Never-raise."""

async def run_trading_loop_once(
    *, symbol: str = "BTC/USDT", mode: str | ExecutionMode = ExecutionMode.PAPER,
    provider: str = "mock", analysis_profile: str = "conservative",
    loop_audit_path: str | Path = _AUDIT_LOG,
    execution_audit_path: str | Path = _PAPER_EXECUTION_AUDIT_LOG,
    freshness_threshold_seconds: float = 120.0, timeout_seconds: int = 10,
) -> LoopCycle:
    """Guarded. Fail-closed auf mode=live. Never-raise (Fehler im LoopCycle.status=ERROR)."""
```

`build_loop_trigger_analysis(symbol, analysis_profile)` â€” baut `AnalysisResult` aus Profil: `conservative` (kein actionable signal), `bullish`, `bearish`.

### Â§52.5 Security Contract `run_trading_loop_once`

| Bedingung | Reaktion |
|---|---|
| `mode == "live"` | `_run_once_guard()` â†’ `raise ValueError` (fail-closed) |
| `mode` nicht in {"paper","shadow"} | `_normalize_loop_mode()` â†’ `raise ValueError` |
| `provider="mock"` (Default) | `MockMarketDataAdapter` â€” kein Netzwerk |
| `analysis_profile="conservative"` (Default) | Kein actionable Signal â†’ `CycleStatus.NO_SIGNAL` |
| Interner Fehler | `LoopCycle(status=ERROR, notes=[...])` |

**Isolation**: `run_trading_loop_once` erstellt eine NEUE `PaperExecutionEngine` â€” kein Portfolio-Replay aus `paper_execution_audit.jsonl`. Wenn ein Trade simuliert wird (COMPLETED), schreibt die Engine den Fill in `paper_execution_audit.jsonl` (korrekt â€” entspricht dem Paper-Execution-Audit-Pattern).

### Â§52.6 Read-Only MCP Surfaces

#### `get_trading_loop_status` (neu â€” in `_CANONICAL_MCP_READ_TOOL_NAMES` deklariert, noch nicht implementiert ðŸ”²)

```
Input: audit_path, mode
Output: LoopStatusSummary.to_json_dict()
```

#### `get_recent_trading_cycles` (neu â€” in `_CANONICAL_MCP_READ_TOOL_NAMES` deklariert, noch nicht implementiert ðŸ”²)

```
Input: audit_path, last_n
Output: RecentCyclesSummary.to_json_dict()
```

#### `get_loop_cycle_summary` (bestehend â€” KompatibilitÃ¤ts-Alias fÃ¼r `get_recent_trading_cycles`)

### Â§52.7 Guarded-Write MCP Surface

#### `run_trading_loop_once` (neu â€” in `_GUARDED_MCP_WRITE_TOOL_NAMES` deklariert, noch nicht implementiert ðŸ”²)

```
Klassifikation: guarded_write
Input: symbol, mode="paper", provider="mock", analysis_profile="conservative",
       loop_audit_path, execution_audit_path
Output: LoopCycle.to_json_dict() + auto_loop_enabled=False + execution_enabled=False +
        write_back_allowed=False + error (None bei Erfolg, Ablehnungsgrund bei fail-closed)
```

### Â§52.8 CLI Surfaces

| Command | Status | Backing |
|---|---|---|
| `research trading-loop-status` | âœ… implementiert | `build_loop_status_summary()` |
| `research trading-loop-recent-cycles` | âœ… implementiert | JSONL direkt |
| `research loop-cycle-summary` | âœ… Alias fÃ¼r trading-loop-recent-cycles | â€” |
| `research trading-loop-run-once` | ðŸ”² in FINAL_RESEARCH_COMMAND_NAMES, nicht registriert | `run_trading_loop_once()` |

### Â§52.9 Erkannte Drift (Sprint 41 Befund)

1. **FrÃ¼he Arch-Definition** (diese Session, vor Implementierungs-Check) verwendete falsche Namen: `LoopStatus`, `loop_read.py`, `read_loop_status()`, `get_loop_status`, `run_paper_cycle`, `research loop-status`, `research run-paper-cycle` â€” alle superseded durch Â§52.
2. **Failing Test**: `test_research_command_inventory_matches_registration_and_help` â€” `trading-loop-run-once` in FINAL list, nicht registriert â†’ Pre-existing-Blocker, Sprint-41-Impl (Codex) muss CLI-Command registrieren.
3. ~~**`loop_surface.py`**~~ (LoopStatusReport, CycleSummary) â€” **ENTFERNT** (Sprint 41C). Kein Code, keine Tests mehr auf dem Filesystem. `test_loop_surface.py` ebenfalls entfernt.
4. **`get_loop_cycle_summary`** MCP â€” war frÃ¼her direkte Implementierung, ist jetzt KompatibilitÃ¤ts-Alias fÃ¼r `get_recent_trading_cycles` (noch zu implementieren).

### Â§52.10 Tests (Sprint 41 â€” Ziel)

| Datei | Scope | Ziel |
|---|---|---|
| `tests/unit/test_mcp_loop_control.py` | `get_trading_loop_status` + `get_recent_trading_cycles` + `run_trading_loop_once` | â‰¥ 8 Tests |
| `tests/unit/test_cli_loop_control.py` | CLI `trading-loop-run-once` + inventory fix | â‰¥ 5 Tests |
| **Gesamt Sprint 41** | **â‰¥ 13 neue Tests** | Ziel: 1456+ passed, 0 failed |

**Baseline**: 1442 passed, 1 failed (pre-existing: `test_research_command_inventory_matches_registration_and_help`)

### Â§52.11 Invarianten-Referenz

- `docs/intelligence_architecture.md` I-301â€“I-310 (Sprint 41)
- `ASSUMPTIONS.md` A-047â€“A-055 (Sprint 41)
- `AGENTS.md` P47 (Sprint 41)

### Â§52C Sprint 41C â€” Kanonisch Festziehen (Drift-Bereinigung)

**Sprint 41C** (2026-03-21): Konsolidierung. Alle stalen Referenzen auf `loop_surface.py` und `test_loop_surface.py` bereinigt.

#### Â§52C.1 Modulstatus (kanonisch)

| Modul | Status |
|---|---|
| `app/orchestrator/trading_loop.py` | âœ… KANONISCH â€” einziger gÃ¼ltiger Control-Plane-Pfad |
| `app/orchestrator/models.py` | âœ… KANONISCH â€” LoopStatusSummary, RecentCyclesSummary, LoopCycle |
| ~~`app/orchestrator/loop_surface.py`~~ | âŒ ENTFERNT â€” kein Code auf dem Filesystem |
| ~~`tests/unit/test_loop_surface.py`~~ | âŒ ENTFERNT â€” kein Code auf dem Filesystem |

#### Â§52C.2 Bereinigter Drift (Â§52.9 ErgÃ¤nzung)

- **Â§52.2** (Modul-Pfad): `loop_surface.py` als ENTFERNT markiert âœ…
- **Â§52.9 Punkt 3**: `loop_surface.py`/`test_loop_surface.py` als ENTFERNT markiert âœ…
- **I-307** (intelligence_architecture.md): als REMOVED markiert âœ…
- **P47** (AGENTS.md): Testanzahl 46â†’43 korrigiert, Test-Stand auf 1444/41C âœ…
- **TASKLIST.md** Sprint 41: 41.C + test_loop_surface.py-Zeile als ENTFERNT markiert âœ…

#### Â§52C.3 Finaler Teststand

| Kategorie | Stand |
|---|---|
| Gesamt Tests | 1444 passed, 0 failed |
| Loop-Tests | 43 (6 Dateien, ohne loop_surface) |
| ruff | clean |
| Datum | 2026-03-21 |

---

## Â§53 Sprint 42 â€” Telegram Webhook Hardening

**Datum**: 2026-03-21
**Sprint**: 42
**Status**: Historischer Entwurf (superseded durch Â§53C)

> **Consolidation note (Sprint 42C):** Sections Â§53.1â€“Â§53.11 are kept as
> historical design context only. The canonical, active runtime path is defined
> in Â§53C (`app/messaging/telegram_bot.py`).

### Â§53.1 Architektonische Grenzen

**Webhook Layer = Transport Hardening â€” keine Business-Logik.**

| Eigenschaft | Wert |
|---|---|
| Scope | Transport-Validierung: Secret-Check, Typ-Filter, Replay-Schutz, Audit |
| NICHT in Scope | Business-Logik, neue Commands, neue MCP-Tools, neue CLI-Commands |
| webhook â‰  | execution surface, approval engine, live path, scheduling surface |
| Entwurfsmodul (historisch) | Separates Legacy-Webhook-Modul (nicht kanonisch; durch `app/messaging/telegram_bot.py` ersetzt) |
| Downstream | `TelegramOperatorBot.process_update()` â€” unverÃ¤ndert, erhÃ¤lt nur validated updates |
| Live | immer default-off; kein Live-Pfad in Sprint 42 |

Der Webhook-Layer ist ein reiner Eingangsfilter vor `TelegramOperatorBot.process_update()`. Er prÃ¼ft, dedupliziert und loggt â€” mehr nicht.

### Â§53.2 Neue Settings

`OperatorSettings` (`app/core/settings.py`) bekommt:

```python
telegram_webhook_secret: str = Field(default="")
# Env-Var: OPERATOR_TELEGRAM_WEBHOOK_SECRET
# Leer = Webhook-Endpoint fail-closed (HTTP 403 auf jeden Request)
```

**Invariante**: Kein Webhook ohne konfigurierten Secret. `webhook_signature_required: True` in Runtime-Config ist damit operational.

### Â§53.3 WebhookValidatedUpdate (frozen dataclass)

Kanonisches Output-Modell des Webhook-Layers:

```python
@dataclass(frozen=True)
class WebhookValidatedUpdate:
    update_id: int
    chat_id: int
    user_id: int
    text: str
    received_at_utc: str          # ISO 8601 UTC
    source_verified: bool         # True wenn secret_token valid
    is_duplicate: bool            # True wenn update_id bereits gesehen
    audit_outcome: str            # siehe Â§53.7
    raw_update: dict[str, object] # originales dict, unverÃ¤nderlich
    # Safety-Invarianten (immer False â€” nie aus Webhook gesetzt)
    execution_enabled: bool = False
    write_back_allowed: bool = False
```

`to_audit_dict()` â†’ `report_type: "webhook_validated_update"`

### Â§53.4 WebhookAuditRecord

Format (`artifacts/webhook_audit.jsonl`):

```json
{
  "report_type": "webhook_audit_record",
  "timestamp_utc": "2026-03-21T10:00:00+00:00",
  "update_id": 12345678,
  "chat_id": 123456789,
  "user_id": 123456789,
  "text_preview": "/status",
  "source_verified": true,
  "is_duplicate": false,
  "audit_outcome": "accepted",
  "forwarded_to_bot": true
}
```

Regeln:
- Append-only â€” keine Zeile wird Ã¼berschrieben oder gelÃ¶scht
- Wird fÃ¼r **jeden** eingehenden Request geschrieben â€” unabhÃ¤ngig vom Outcome
- EnthÃ¤lt keine Secrets, Tokens oder Credentials
- `text_preview` auf 50 Zeichen begrenzt (kein vollstÃ¤ndiger Message-Content im Audit)

### Â§53.5 WebhookValidator

```python
class WebhookValidator:
    def __init__(
        self,
        *,
        secret_token: str,
        allowed_update_types: frozenset[str] = frozenset({"message"}),
        replay_window_size: int = 1000,
        audit_log_path: str = "artifacts/webhook_audit.jsonl",
    ) -> None: ...

    def validate(
        self,
        body: dict[str, Any],
        provided_secret: str,
    ) -> WebhookValidatedUpdate:
        """PrÃ¼ft Secret, Typ, Replay. Schreibt Audit. Gibt WebhookValidatedUpdate zurÃ¼ck.
        
        Nie raise. Gibt bei jedem Fehler rejected_* outcome zurÃ¼ck.
        """
        ...

    def is_replay(self, update_id: int) -> bool:
        """PrÃ¼ft ob update_id bereits im Replay-Buffer."""
        ...

    def mark_seen(self, update_id: int) -> None:
        """FÃ¼gt update_id in Replay-Buffer ein (deque, maxlen=replay_window_size)."""
        ...
```

**`validate()` ist niemals raise** â€” jede Fehlerklasse wird in `audit_outcome` kodiert und geloggt.

### Â§53.6 Sicherheitsinvarianten (fail-closed)

| Bedingung | audit_outcome | HTTP | dispatch |
|---|---|---|---|
| `secret_token == ""` (unkonfiguriert) | `rejected_no_secret` | 403 | Nein |
| `provided_secret != secret_token` | `rejected_invalid_secret` | 403 | Nein |
| body ohne `update_id` oder kein dict | `rejected_malformed` | 400 | Nein |
| update_id bereits gesehen | `rejected_replay` | 200 | Nein |
| kein erlaubter Update-Typ | `rejected_invalid_type` | 200 | Nein |
| alle Checks bestanden | `accepted` | 200 | Ja |

**Kernregel**: `process_update()` wird ausschliesslich mit `audit_outcome == "accepted"` aufgerufen. Kein rejected-Update erreicht den Command-Handler.

### Â§53.7 Audit Outcomes (kanonisch)

```python
_VALID_AUDIT_OUTCOMES = frozenset({
    "accepted",
    "rejected_no_secret",
    "rejected_invalid_secret",
    "rejected_malformed",
    "rejected_replay",
    "rejected_invalid_type",
})
```

### Â§53.8 Replay Protection

- In-Memory Ring Buffer: `collections.deque(maxlen=1000)` â€” default 1000 update_ids
- Kein persistenter State (kein JSONL fÃ¼r Replay-Buffer)
- Bei Neustart: leerer Buffer (safe â€” Telegram-retransmits werden als `rejected_replay` behandelt, was idempotent korrekt ist)
- Kein Thread-Locking erforderlich (single-threaded async handler)

### Â§53.9 Erlaubte Update-Typen

```python
_ALLOWED_WEBHOOK_UPDATE_TYPES: frozenset[str] = frozenset({"message"})
```

Explizit NICHT erlaubt (â†’ `rejected_invalid_type`, silently dropped nach Audit):

| Update-Typ | Grund |
|---|---|
| `edited_message` | Replay-Risiko, doppelte Command-AuslÃ¶sung mÃ¶glich |
| `channel_post` / `edited_channel_post` | kein Operator-Kanal |
| `inline_query` / `chosen_inline_result` | kein Inline-Interface |
| `callback_query` | kein Inline-Button-Interface |
| `shipping_query` / `pre_checkout_query` | kein Payment-Interface |
| `poll` / `poll_answer` | kein Poll-Interface |
| `my_chat_member` / `chat_member` | kein Membership-Event-Handler |

### Â§53.10 Tests (Sprint 42 â€” Ziel)

> **Legacy note:** The file names in this subsection describe the original
> Sprint-42 draft test plan and are superseded by Â§53C.5 canonical tests.

| Datei | Scope | Ziel |
|---|---|---|
| Separater Legacy-Webhook-Test | WebhookValidator: alle 6 outcomes + dispatch logic + audit | â‰¥ 10 Tests |

Pflicht-TestfÃ¤lle:
1. valid secret + message â†’ `accepted`, dispatched
2. invalid secret â†’ `rejected_invalid_secret`, nicht dispatched
3. leer secret (unkonfiguriert) â†’ `rejected_no_secret`, nicht dispatched
4. kein `update_id` â†’ `rejected_malformed`, nicht dispatched
5. `edited_message` statt `message` â†’ `rejected_invalid_type`, nicht dispatched
6. gleiche `update_id` zweimal â†’ zweiter: `rejected_replay`, nicht dispatched
7. `accepted` â†’ `process_update()` aufgerufen (mock)
8. jedes rejected â†’ `process_update()` NICHT aufgerufen (mock)
9. `webhook_audit.jsonl` append bei `accepted`
10. `webhook_audit.jsonl` append bei `rejected_invalid_secret`

**Baseline**: 1444 passed, 0 failed | Ziel: 1454+ passed, 0 failed

### Â§53.11 Invarianten-Referenz

- `docs/intelligence_architecture.md` I-311â€“I-320 (Sprint 42)
- `ASSUMPTIONS.md` A-056â€“A-062 (Sprint 42)
- `AGENTS.md` P48 (Sprint 42)
- `TELEGRAM_INTERFACE.md` â€” Abschnitt "Webhook Transport Layer"

### Â§53C Sprint 42C â€” Kanonisch Festziehen (Drift-Bereinigung)

**Sprint 42C** (2026-03-21): Konsolidierung. Â§53 verwendete falsche Modul-, Klassen- und Methodennamen. Die Implementierung (Codex) integrierte den Webhook-Guard direkt in `telegram_bot.py` â€” einfacher und korrekt.

#### Â§53C.1 Implementierungs-Delta (Â§53 war falsch)

| Â§53 Contract | TatsÃ¤chlich implementiert (kanonisch) |
|---|---|
| Neues separates Webhook-Modul | **Integriert in `app/messaging/telegram_bot.py`** (kein separates Modul) |
| Klasse `WebhookValidator` | **Methoden in `TelegramOperatorBot`** |
| Methode `validate()` | **`process_webhook_update()`** |
| Resultat `WebhookValidatedUpdate` | **`TelegramWebhookProcessResult`** (frozen, `accepted`, `processed`, `rejection_reason`, `update_id`, `update_type`) |
| Audit `artifacts/webhook_audit.jsonl` (alle Requests) | **`artifacts/telegram_webhook_rejections.jsonl`** (Rejections only) |
| `edited_message` verboten | **`edited_message` erlaubt per Default** (konfigurierbar via `webhook_allowed_updates`) |
| `deque(maxlen=1000)` | **`OrderedDict` FIFO, `maxlen=2048`** |
| 6 abstrakte Rejection-Reasons | **12 spezifische Rejection-Reasons** |

#### Â§53C.2 Kanonische Modulstruktur (final)

```
app/messaging/telegram_bot.py  â† EINZIGE Datei, kein separates Legacy-Webhook-Modul
â”œâ”€â”€ _WEBHOOK_ALLOWED_UPDATES_DEFAULT = ("message", "edited_message")
â”œâ”€â”€ _WEBHOOK_MAX_BODY_BYTES_DEFAULT = 64_000
â”œâ”€â”€ _WEBHOOK_MAX_SEEN_UPDATE_IDS_DEFAULT = 2_048
â”œâ”€â”€ _WEBHOOK_REJECTION_AUDIT_LOG_DEFAULT = "artifacts/telegram_webhook_rejections.jsonl"
â”œâ”€â”€ TelegramWebhookProcessResult (frozen dataclass)
â”‚   â”œâ”€â”€ accepted: bool
â”‚   â”œâ”€â”€ processed: bool
â”‚   â”œâ”€â”€ rejection_reason: str | None
â”‚   â”œâ”€â”€ update_id: int | None
â”‚   â””â”€â”€ update_type: str | None
â””â”€â”€ TelegramOperatorBot
    â”œâ”€â”€ __init__(webhook_secret_token, webhook_rejection_audit_log, webhook_allowed_updates,
    â”‚           webhook_max_body_bytes, webhook_max_seen_update_ids, ...)
    â”œâ”€â”€ webhook_configured: bool (property)
    â”œâ”€â”€ get_webhook_status_summary() â†’ dict (read-only, execution_enabled=False)
    â”œâ”€â”€ process_webhook_update(method, content_type, content_length,
    â”‚                          header_secret_token, update) â†’ TelegramWebhookProcessResult
    â”œâ”€â”€ _constant_time_secret_match(candidate) â†’ bool  [hmac.compare_digest]
    â”œâ”€â”€ _extract_allowed_update_type(update) â†’ str | None
    â”œâ”€â”€ _track_webhook_update_id(update_id) â†’ None  [OrderedDict FIFO]
    â”œâ”€â”€ _audit_webhook_rejection(...) â†’ None  [telegram_webhook_rejections.jsonl]
    â””â”€â”€ _reject_webhook(...) â†’ TelegramWebhookProcessResult  [never-raise]
```

#### Â§53C.3 Kanonische Rejection-Reasons (final, 12 Werte)

| rejection_reason | AuslÃ¶ser |
|---|---|
| `webhook_secret_not_configured` | `webhook_secret_token` leer/None â€” fail-closed |
| `invalid_http_method` | Methode != POST |
| `invalid_content_type` | Content-Type nicht `application/json` |
| `missing_content_length` | Content-Length Header fehlt |
| `invalid_content_length` | Content-Length â‰¤ 0 |
| `payload_too_large` | Content-Length > `webhook_max_body_bytes` (64_000) |
| `missing_secret_token_header` | `X-Telegram-Bot-Api-Secret-Token` Header leer/fehlt |
| `invalid_secret_token` | Header-Token != konfigurierter Token (constant-time) |
| `missing_or_invalid_update_body` | `update` ist kein dict |
| `invalid_update_id` | `update_id` fehlt, ist kein int oder ist negativ |
| `disallowed_update_type` | kein erlaubter Update-Typ im Body |
| `duplicate_update_id` | `update_id` bereits im Replay-Buffer |

**Erfolgspfad**: kein `rejection_reason` â†’ `accepted=True`, `processed=True` â†’ dispatch an `process_update()`

#### Â§53C.4 `edited_message` â€” korrigierte Semantik

**Â§53 war falsch**: `edited_message` als grundsÃ¤tzlich verboten.
**TatsÃ¤chlich**: `edited_message` ist im Default erlaubt (`_WEBHOOK_ALLOWED_UPDATES_DEFAULT`). Operatoren kÃ¶nnen es per `webhook_allowed_updates=("message",)` ausschliessen. Die Implementierung lÃ¤sst `edited_message`-Commands durch `process_update()` zu â€” dies ist bewusst, da editierte Operator-Commands keine Sicherheitsrisiken darstellen, wenn Replay-Schutz (update_id-Deduplication) aktiv ist.

#### Â§53C.5 Audit-Strategie (korrigiert)

**Â§53 war falsch**: Audit fÃ¼r alle Requests.
**TatsÃ¤chlich**: `artifacts/telegram_webhook_rejections.jsonl` â€” nur fÃ¼r abgewiesene Requests. Accepted requests werden via `artifacts/operator_commands.jsonl` (bestehender Bot-Layer-Audit) geloggt.

Format eines Rejection-Audit-Eintrags:
```json
{
  "timestamp_utc": "2026-03-21T10:00:00+00:00",
  "event": "telegram_webhook_rejected",
  "reason": "invalid_secret_token",
  "method": "POST",
  "content_type": "application/json",
  "content_length": 64,
  "update_id": null,
  "update_type": null,
  "allowed_updates": ["message", "edited_message"],
  "execution_enabled": false,
  "write_back_allowed": false
}
```

#### Â§53C.6 Finaler Teststand

| Kategorie | Stand |
|---|---|
| Gesamt Tests | 1456 passed, 0 failed |
| Webhook-Tests in test_telegram_bot.py | 15 neue Tests (589â€“828) |
| Gesamt test_telegram_bot.py | 43 Tests (war: 28) |
| ruff | clean |
| Datum | 2026-03-21 |

Bereinigter Drift:
- ~~Legacy Webhook Cache-Artefakt~~ â€” stale Cache-Datei (kein `.py` auf Filesystem; pytest lÃ¤dt nur `.py`) â€” inaktiv, kein Handlungsbedarf
- kein separates Legacy-Webhook-Modul (war nie erstellt â€” korrekt, da integriert)

### Â§53D Sprint 42D â€” Finales Einfrieren (Documentation Freeze)

**Sprint 42D** (2026-03-21): Alle Restdrift-Referenzen bereinigt. Â§53 + Â§53C + Â§53D bilden zusammen die vollstÃ¤ndige kanonische Dokumentation des Telegram-Webhook-Hardening-Pfads.

#### Â§53D.1 Einziger kanonischer Webhook-Transport-Pfad (eingefroren)

```
app/messaging/telegram_bot.py
â””â”€â”€ TelegramOperatorBot.process_webhook_update(
        method, content_type, content_length,
        header_secret_token, update
    ) â†’ TelegramWebhookProcessResult
```

**Keine weiteren Webhook-Module**: kein separates Legacy-Webhook-Modul, kein separates Guard-Modul.
**Â§53.1â€“Â§53.11** = historische Entwurfs-Dokumentation, superseded durch Â§53C.
**Â§53C** = kanonische Implementierungs-Dokumentation.
**Â§53D** = finaler Einfrierungs-Nachweis.

#### Â§53D.2 Bereinigter Drift (Sprint 42D)

| Dokument | Drift | Fix |
|---|---|---|
| `ASSUMPTIONS.md` A-056 | separates Legacy-Webhook-Modul als Plan | â†’ `telegram_bot.py` integriert |
| `ASSUMPTIONS.md` A-057 | `OperatorSettings.telegram_webhook_secret` | â†’ Konstruktor-Parameter |
| `TASKLIST.md` Sprint 42 | `"pending (Codex)"` | â†’ `âœ… vollstÃ¤ndig` |
| `TASKLIST.md` Sprint 42 Scope | historischer Plan ohne Korrekturvermerk | â†’ durchgestrichene Originalplanung |

#### Â§53D.3 Finaler Teststand (eingefroren)

| Metrik | Wert |
|---|---|
| Gesamt-Tests | 1456 passed, 0 failed |
| Webhook-Tests | 15 in `test_telegram_bot.py` (Zeilen 589â€“828) |
| ruff | clean |
| Eingefroren | 2026-03-21 |

---

## Â§54 Sprint 43 â€” FastAPI Operator API Surface

**Datum**: 2026-03-21  
**Status**: Implementiert (kanonischer API-Expose-Layer auf bestehende Surfaces)

### Â§54.1 Scope und Sicherheitsgrenzen

- Keine neue Business-Logik im API-Layer.
- Read-only Endpunkte exposen ausschliesslich bestehende kanonische Summaries.
- Genau ein guarded Endpunkt: TradingLoop run-once (paper/shadow only via bestehende Guards).
- Kein Live-/Broker-/Trading-Feature-Ausbau.

### Â§54.2 Auth-/Guard-Kontrakt

- `/operator/*` nutzt Bearer-Token-Guard auf Basis `APP_API_KEY`.
- Fail-closed:
  - `APP_API_KEY` leer/nicht gesetzt â†’ `503`.
  - fehlender/ungueltiger Authorization Header â†’ `401`.
  - falscher Token â†’ `403`.
- Tokenvergleich erfolgt constant-time via `secrets.compare_digest`.

### Â§54.3 Kanonische Endpunkte

Read-only:

- `GET /operator/status` â†’ `mcp_server.get_operational_readiness_summary()`
- `GET /operator/readiness` â†’ `mcp_server.get_operational_readiness_summary()`
- `GET /operator/decision-pack` â†’ `mcp_server.get_decision_pack_summary()`
- `GET /operator/portfolio-snapshot` â†’ `mcp_server.get_paper_portfolio_snapshot(...)`
- `GET /operator/exposure-summary` â†’ `mcp_server.get_paper_exposure_summary(...)`
- `GET /operator/trading-loop/status` â†’ `mcp_server.get_trading_loop_status(...)`
- `GET /operator/trading-loop/recent-cycles` â†’ `mcp_server.get_recent_trading_cycles(...)`

Guarded:

- `POST /operator/trading-loop/run-once` â†’ `mcp_server.run_trading_loop_once(...)`

### Â§54.4 Guarded run-once Invarianten

- `mode` wird nicht lokal im Router erweitert oder interpretiert.
- Alle Mode-Checks bleiben im kanonischen TradingLoop-Backbone.
- `mode=live` bleibt fail-closed (kontrollierte Ablehnung, keine Seiteneffekte).
- Kein Scheduler, kein Background-Worker, kein Auto-Loop.

### Â§54.5 Testabdeckung (Sprint 43)

`tests/unit/test_api_operator.py` verifiziert:

- Auth fail-closed Verhalten (503/401/403).
- Read-only Endpunkt-Mapping und Payload-Passthrough.
- Guarded run-once fuer `paper` und `shadow`.
- Live-Mode fail-closed am guarded Endpunkt.
- Keine Broker-/Trading-Semantik auf den Read-Surfaces.

---

## Â§54 Sprint 43 â€” FastAPI Operator API Surface (Historischer Entwurf)

> **Sprint 43C (2026-03-21):** Dieser Block war der ursprÃ¼ngliche Sprint-43-Definitions-Entwurf. Er enthÃ¤lt falsche Endpunkt-Namen und dokumentiert Webhook-Endpoints (`POST /operator/webhook`, `GET /operator/webhook-status`) die in Sprint 43 NICHT implementiert wurden. Kanonischer Stand: Â§54C.

**Datum**: 2026-03-21
**Sprint**: 43
**Status**: ~~Definition âœ… â€” Implementierung pending (Codex)~~ **Historischer Entwurf (superseded by Â§54C)**

### Â§54.1 Architektonische Grenzen

**API Surface = Exposition kanonischer Surfaces â€” keine neue Business-Logik.**

| Eigenschaft | Wert |
|---|---|
| Scope | Exposition: read-only Operator-Status + guarded paper/shadow Webhook-Transport |
| NICHT in Scope | Neue Business-Logik, UI, Scheduler, Remote Automation, Live-Trading |
| API â‰  | live control plane, broker gateway, execution surface, approval engine |
| Neues Modul | `app/api/routers/operator.py` |
| Auth | Bearer-Token (`APP_API_KEY`) via bestehendes `app/security/auth.py` |
| Webhook-Bypass | `POST /operator/webhook` von Bearer-Auth ausgenommen â€” Telegram-Secret-Token ist die eigene Auth |
| Live | immer default-off; kein Live-Pfad in Sprint 43 |

### Â§54.2 Auth-Modell

| Endpoint | Bearer-Token | Telegram-Secret-Token |
|---|---|---|
| `GET /operator/status` | âœ… Pflicht (wenn `APP_API_KEY` gesetzt) | â€” |
| `GET /operator/portfolio` | âœ… Pflicht | â€” |
| `GET /operator/loop-status` | âœ… Pflicht | â€” |
| `GET /operator/webhook-status` | âœ… Pflicht | â€” |
| `POST /operator/webhook` | âŒ ausgenommen | âœ… via `TelegramOperatorBot.process_webhook_update()` |

`POST /operator/webhook` MUSS in `app/security/auth.py` zur Bearer-Bypass-Liste hinzugefÃ¼gt werden (analog `/health`).

### Â§54.3 Endpoints (kanonisch, read-only)

#### `GET /operator/status`
- **Source of Truth**: `build_operational_readiness_report()` (`app/research/operational_readiness.py`)
- **Fail-Closed**: Bei Exception â†’ HTTP 200 mit `available=False, execution_enabled=False, write_back_allowed=False`
- **Invarianten**: `execution_enabled=False`, `write_back_allowed=False` immer im Response

#### `GET /operator/portfolio`
- **Source of Truth**: `build_portfolio_snapshot()` (`app/execution/portfolio_read.py`)
- **Fail-Closed**: Bei Exception â†’ HTTP 200 mit `execution_enabled=False, write_back_allowed=False`
- **Invarianten**: `execution_enabled=False`, `write_back_allowed=False` immer im Response

#### `GET /operator/loop-status`
- **Source of Truth**: `build_loop_status_summary()` (`app/orchestrator/trading_loop.py`)
- **Fail-Closed**: Bei Exception â†’ HTTP 200 mit `execution_enabled=False, write_back_allowed=False`
- **Invarianten**: `execution_enabled=False`, `write_back_allowed=False`, `auto_loop_enabled=False` immer im Response

#### `GET /operator/webhook-status`
- **Source of Truth**: Statische Webhook-Konfiguration â€” `report_type="webhook_status"`, `secret_token_required=True`, `allowed_updates=["message"]`, `execution_enabled=False`, `write_back_allowed=False`
- **Fail-Closed**: Kein Backing-System nÃ¶tig â€” rein statisch
- **Hinweis**: Kein Live-Zustand â€” zeigt Konfigurationsabsicht, nicht Laufzustand

### Â§54.4 Endpoint (guarded â€” Telegram Webhook Transport)

#### `POST /operator/webhook`
- **Source of Truth**: `TelegramOperatorBot.process_webhook_update()` (`app/messaging/telegram_bot.py`)
- **Voraussetzung**: `app.state.telegram_bot` muss gesetzt sein
- **Inputs**: JSON-Body (Telegram Update), Header `X-Telegram-Bot-Api-Secret-Token`
- **Kein Bot konfiguriert**: HTTP 503 `{"reason": "bot_not_configured"}`
- **UngÃ¼ltiges JSON**: HTTP 400 `{"reason": "invalid_json"}`
- **Accepted**: HTTP 200 `{"status": "ok"}`
- **Rejected** (invalid_secret_token etc.): HTTP 403 `{"reason": "<rejection_reason>"}`
- **Verboten im Payload**: kein `execution_enabled=True`, keine Trading-Semantik
- **VollstÃ¤ndige Rejection-Logik**: delegiert an `TelegramOperatorBot.process_webhook_update()` (Sprint 42D â€” 12 Rejection-Reasons)

### Â§54.5 Sicherheitsinvarianten

1. `execution_enabled=False` in JEDEM `/operator/*`-Response â€” ausnahmslos
2. `write_back_allowed=False` in JEDEM `/operator/*`-Response â€” ausnahmslos
3. Kein `/operator/trade`, `/operator/execute`, `/operator/order`, `/operator/fill`, `/operator/broker`, `/operator/live` â€” diese Pfade sind explizit verboten
4. `POST /operator/webhook` delegiert Validierung vollstÃ¤ndig an `TelegramOperatorBot.process_webhook_update()` â€” kein eigener Security-Check
5. Alle read-only Endpoints sind fail-closed: Exceptions â†’ HTTP 200 mit `available=False/execution_enabled=False`, nicht HTTP 500
6. `auto_loop_enabled=False` in `/operator/loop-status` â€” invariant (aus `LoopStatusSummary`)
7. `POST /operator/webhook` ist kein Trading-/Approval-/Execution-Gateway â€” es ist Transport-Delegation

### Â§54.6 Failure Semantics

| Endpoint | Exception | HTTP | Body |
|---|---|---|---|
| `GET /operator/status` | jede | 200 | `{available: false, execution_enabled: false, write_back_allowed: false}` |
| `GET /operator/portfolio` | jede | 200 | `{execution_enabled: false, write_back_allowed: false}` |
| `GET /operator/loop-status` | jede | 200 | `{execution_enabled: false, write_back_allowed: false}` |
| `GET /operator/webhook-status` | n/a (statisch) | 200 | statisches Objekt |
| `POST /operator/webhook` (kein Bot) | n/a | 503 | `{reason: "bot_not_configured"}` |
| `POST /operator/webhook` (bad JSON) | JSON parse | 400 | `{reason: "invalid_json"}` |
| `POST /operator/webhook` (rejected) | n/a | 403 | `{reason: "<rejection_reason>"}` |

**Kein HTTP 500 von `/operator/*`-Endpoints nach auÃŸen.**

### Â§54.7 Implementierungs-Tasks (Codex)

1. `app/api/routers/operator.py` â€” neu erstellen mit Endpoints Â§54.3â€“Â§54.4
2. `app/api/main.py` â€” `operator.router` einbinden + `app.state.telegram_bot` optional aus Settings befÃ¼llen
3. `app/security/auth.py` â€” `/operator/webhook` und `/operator/webhook/` zur Bypass-Liste hinzufÃ¼gen

### Â§54.8 Tests (Sprint 43 â€” Ziel)

| Datei | Scope | Stand |
|---|---|---|
| `tests/unit/test_operator_api.py` | 9 Tests: status fail-closed, portfolio fail-closed, loop-status fail-closed, webhook-status, webhook (no-bot/invalid-JSON/accepted/rejected), no-trading-routes | bereits geschrieben (Codex), 8 failing (router fehlt) |

**Baseline**: 1457 passed, 8 failed | Ziel: 1465+ passed, 0 failed

### Â§54.9 Invarianten-Referenz

- `docs/intelligence_architecture.md` I-321â€“I-330 (Sprint 43)
- `ASSUMPTIONS.md` A-066â€“A-072 (Sprint 43)
- `AGENTS.md` P49 (Sprint 43)
- Backing-Surfaces: Â§49 (Telegram), Â§50 (MarketData), Â§51 (Portfolio), Â§52 (TradingLoop), Â§53+D (Webhook)

---

## Â§54C Sprint 43 â€” FastAPI Operator API Surface (Konsolidierung â€” kanonisch)

> **Sprint 43C (2026-03-21):** Â§54 (Historischer Entwurf) enthielt falsche Endpunkt-Namen und plante Webhook-Endpoints die nicht implementiert wurden. Dieser Block dokumentiert den tatsÃ¤chlichen Implementierungsstand. Â§54 (erster Block, korrekt) und Â§54C sind zusammen die kanonische Referenz.

**Datum**: 2026-03-21
**Status**: âœ… vollstÃ¤ndig implementiert + konsolidiert

### Â§54C.1 Drift-Tabelle (Â§54-Entwurf â†’ tatsÃ¤chliche Implementierung)

| Â§54-Entwurf | TatsÃ¤chliche Implementierung | Korrektur |
|---|---|---|
| `GET /operator/portfolio` | `GET /operator/portfolio-snapshot` | Endpunkt umbenannt |
| `GET /operator/loop-status` | `GET /operator/trading-loop/status` | Pfad umstrukturiert |
| `GET /operator/webhook-status` | **NICHT implementiert** (Sprint 43+) | Aus Scope entfernt |
| `POST /operator/webhook` | **NICHT implementiert** (Sprint 43+) | Aus Scope entfernt |
| Endpoint-Liste: 4 Endpoints | Endpoint-Liste: 8 Endpoints | +readiness, +decision-pack, +recent-cycles, +run-once |
| Auth via `app/security/auth.py` Bearer-Middleware | Auth via `require_operator_api_token` (Router-Dependency, DI) | Anderer Auth-Pfad |
| `test_operator_api.py` 9 Tests als Referenz | `test_api_operator.py` 13 Tests als kanonische Referenz | Andere Testdatei |

### Â§54C.2 Kanonische Endpunkte (tatsÃ¤chlich implementiert)

| Endpunkt | MCP-Backing | Surface-Klasse |
|---|---|---|
| `GET /operator/status` | `get_operational_readiness_summary()` | read_only |
| `GET /operator/readiness` | `get_operational_readiness_summary()` (Alias) | read_only |
| `GET /operator/decision-pack` | `get_decision_pack_summary()` | read_only |
| `GET /operator/portfolio-snapshot` | `get_paper_portfolio_snapshot(...)` | read_only |
| `GET /operator/exposure-summary` | `get_paper_exposure_summary(...)` | read_only |
| `GET /operator/trading-loop/status` | `get_trading_loop_status(...)` | read_only |
| `GET /operator/trading-loop/recent-cycles` | `get_recent_trading_cycles(...)` | read_only |
| `POST /operator/trading-loop/run-once` | `run_trading_loop_once(...)` | guarded_write |

### Â§54C.3 Auth-Implementierung (tatsÃ¤chlich)

`require_operator_api_token` â€” FastAPI-Dependency-Funktion in `app/api/routers/operator.py`:
- Leerer `APP_API_KEY` â†’ HTTP 503 "fail-closed"
- Kein Authorization-Header â†’ HTTP 401 "Missing Authorization header"
- Falsches Schema â†’ HTTP 401 "Invalid Authorization scheme"
- Falscher Token â†’ HTTP 403 "Invalid API key"
- Tokenvergleich: `secrets.compare_digest` (constant-time)
- Dependency wird als `dependencies=[Depends(require_operator_api_token)]` auf dem gesamten Router gesetzt â€” kein separater Middleware-Bypass nÃ¶tig

### Â§54C.4 Bekannte Testdrift (Sprint 43 â†’ Sprint 43+)

`tests/unit/test_operator_api.py` (9 Tests, 8 failing):
- Beschreibt Endpunkte aus dem Â§54-Entwurf (`/operator/portfolio`, `/operator/loop-status`, `/operator/webhook-status`, `POST /operator/webhook`)
- Diese Endpunkte existieren nicht in der tatsÃ¤chlichen Implementierung â†’ 404/503 statt erwartetem Verhalten
- `test_no_trading_routes` passiert (1 Test) â€” prÃ¼ft nur, dass Verbots-Pfade nicht vorhanden sind

`tests/unit/test_api_operator.py` (13 Tests, alle passing) = kanonische Implementierungsreferenz

**Sprint 43+ Backlog**: Webhook-Delegation (`GET /operator/webhook-status`, `POST /operator/webhook`, `app.state.telegram_bot`) und Korrektur von `test_operator_api.py`.

### Â§54C.5 Finaler Teststand (Sprint 43+43C)

| Metrik | Wert |
|---|---|
| `test_api_operator.py` | 13 Tests, alle passing âœ… |
| `test_operator_api.py` | 9 Tests, 8 failing (stale spec) âŒ |
| Gesamt | **1470 passed, 8 failed** |
| ruff | clean âœ… |
| Implementiertes Modul | `app/api/routers/operator.py` |


---

## Â§55 Sprint 44 â€” Operator API Hardening & Request Governance (Historischer Entwurf)

> **Sprint 44C (2026-03-22):** Dieser Block war der ursprÃ¼ngliche Sprint-44-Definitions-Entwurf. EnthÃ¤lt Drift zur tatsÃ¤chlichen Implementierung: falsches request_id-Format (UUID4 statt req_<hex>), optionale statt required Idempotency, falscher Header-Name (X-Idempotency-Key statt Idempotency-Key), flache statt verschachtelte Error-Shape, falscher Audit-Log-Name, fehlende Correlation-ID, fehlender Rate-Limiter. Kanonischer Stand: Â§55C.

**Datum**: 2026-03-21
**Sprint**: 44
**Status**: ~~Definition âœ… â€” Implementierung pending (Codex)~~ **Historischer Entwurf (superseded by Â§55C)**

### Â§55.1 Scope und Sicherheitsgrenzen

**API Hardening = Transport-/Governance-Layer, keine neue Business-Logik.**

| Eigenschaft | Wert |
|---|---|
| Scope | Request-Identity, Idempotency-Guard, Audit-Surface, Error-Shape-Standardisierung |
| NICHT in Scope | Neue Endpoints, neue Business-Logik, UI, Scheduler, Live-Trading, Broker-Integration |
| Hardening â‰  | Neue Execution-Pfade, Workflow-Engine, Rate-Limiting-Framework, Trading-Semantik |
| Guarded POST â‰  | Trading Execution â€” mode=live bleibt fail-closed |
| Idempotency â‰  | Scheduling â€” keine wiederholte Ausfuehrung, Schutz gegen Doppel-Submit |
| Basis | app/api/routers/operator.py bleibt das einzige Operator-API-Modul |

### Â§55.2 Request-Identity-Kontrakt

Jeder /operator/*-Request traegt eine request_id:

- **Server-generiert**: UUID4 via uuid.uuid4() â€” Standard, wenn kein Client-Header gesetzt
- **Client-gesetzt**: Header X-Request-Id â€” akzeptiert wenn valide UUID4, sonst ignoriert und server-generiert
- **Propagation**: request_id wird in JEDEM Response-Body zurueckgegeben als request_id-Feld auf Top-Level
- **Response-Header**: X-Request-Id: <uuid> in jedem Response
- **Constraint**: request_id darf niemals leer, None oder nicht-UUID sein

### Â§55.3 Idempotency-Kontrakt (guarded POST)

Gilt ausschliesslich fuer POST /operator/trading-loop/run-once:

- **Optional Client-Header**: X-Idempotency-Key (max 128 Zeichen)
- **Wenn gesetzt**: In-memory-Buffer prueft ob Key bereits gesehen â€” falls ja: HTTP 409 {error: duplicate_idempotency_key, detail: ..., request_id: <uuid>}
- **Wenn nicht gesetzt**: Kein Idempotency-Check â€” normaler Ablauf
- **Buffer**: In-memory OrderedDict mit FIFO-Eviction (maxlen=256) â€” NICHT persistent
- **Restart**: Leerer Buffer â€” akzeptiert (analog Telegram Replay-Buffer)
- **Idempotency != Scheduling**: Buffer verhindert Doppel-Submit, startet keine wiederholten Zyklen

### Â§55.4 Operator API Audit Surface

**Neues Audit-Log**: artifacts/operator_api_audit.jsonl â€” append-only.

**Wann**: Fuer JEDEN /operator/*-Request der die Auth passiert hat (post-auth, pre-dispatch).

**Kanonisches Audit-Format**:

```json
{
  "timestamp_utc": "2026-03-21T10:00:00+00:00",
  "request_id": "a1b2c3d4-...",
  "method": "POST",
  "path": "/operator/trading-loop/run-once",
  "endpoint_class": "guarded_write",
  "idempotency_key": "my-key-123",
  "outcome": "ok",
  "http_status": 200,
  "execution_enabled": false,
  "write_back_allowed": false
}
```

**endpoint_class**: read_only (GET) oder guarded_write (POST run-once)
**outcome**: ok (2xx) | error (4xx ausser 409) | blocked (409) | internal_error (5xx)

Regeln: Append-only, keine Secrets/Tokens/Credentials, Audit-Fehler nicht fatal (log WARNING).
Separates Log von operator_commands.jsonl (Telegram) und telegram_webhook_rejections.jsonl.

### Â§55.5 Failure Contract / Error Shape

**Kanonische Fehler-Shape fuer ALLE /operator/*-Fehler**:

```json
{
  "error": "<error_code>",
  "detail": "<human-readable-message>",
  "request_id": "<uuid>"
}
```

**Kanonische error_code-Werte**:

| Code | HTTP | Trigger |
|---|---|---|
| api_key_not_configured | 503 | Leerer APP_API_KEY |
| missing_auth_header | 401 | Kein Authorization-Header |
| invalid_auth_scheme | 401 | Kein Bearer-Schema |
| invalid_api_key | 403 | Falscher Token |
| invalid_request | 400 | Pydantic-Validation oder ungueltige Params |
| mode_not_allowed | 400 | mode=live (ValueError aus TradingLoop-Guard) |
| duplicate_idempotency_key | 409 | Idempotency-Buffer-Hit |
| internal_error | 500 | Unbehandelte Exception |

Kein HTTP 500 ohne request_id. Jeder Fehler traegt error + detail + request_id.

### Â§55.6 Sicherheitsinvarianten (Sprint 44 â€” kanonisch, nicht verhandelbar)

| Nr | Invariante |
|---|---|
| 1 | Kein unkorrelierter guarded Request â€” jeder POST /operator/trading-loop/run-once hat request_id im Response |
| 2 | Kein doppelter run-once auf gleichem X-Idempotency-Key ohne definierte Behandlung (HTTP 409) |
| 3 | Keine ungeregelten Fehlerantworten â€” alle Fehler folgen der kanonischen Error-Shape (Â§55.5) |
| 4 | Kein Live-Pfad â€” mode=live bleibt mode_not_allowed (HTTP 400) |
| 5 | Keine Trading-Semantik in Audit, Error-Shape oder Request-Identity |
| 6 | Audit-Log enthaelt keine Secrets, Tokens, Credentials oder Nutzlast-Details |
| 7 | execution_enabled=False und write_back_allowed=False in JEDEM Response â€” invariant |
| 8 | Idempotency-Buffer ist in-memory, nie persistent â€” Restart = leerer Buffer |
| 9 | request_id im Response-Header X-Request-Id â€” immer gesetzt, nie leer |
| 10 | operator_api_audit.jsonl ist von Telegram-Audits getrennt â€” kein Merge, kein Cross-Write |

### Â§55.7 Kanonische Audit-Log-Felder (vollstaendig)

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| timestamp_utc | ISO-8601 UTC | Ja | Zeitpunkt des Request-Eingangs |
| request_id | UUID4-String | Ja | Server- oder Client-generiert |
| method | String | Ja | HTTP-Methode (GET, POST) |
| path | String | Ja | Endpoint-Pfad |
| endpoint_class | String | Ja | read_only oder guarded_write |
| idempotency_key | String oder null | Ja | Client-Header oder null |
| outcome | String | Ja | ok / error / blocked / internal_error |
| http_status | Integer | Ja | HTTP-Statuscode der Response |
| execution_enabled | Boolean | Ja | Immer false |
| write_back_allowed | Boolean | Ja | Immer false |

### Â§55.8 Implementierungs-Tasks (Codex)

1. **app/api/routers/operator.py** â€” get_request_id() Dependency:
   - Liest X-Request-Id Header (validiert UUID4), sonst uuid.uuid4()
   - Response-Header X-Request-Id wird gesetzt

2. **app/api/routers/operator.py** â€” Idempotency-Buffer fuer POST /operator/trading-loop/run-once:
   - Modul-Level _idempotency_seen: OrderedDict[str, None] maxlen=256 FIFO
   - X-Idempotency-Key Header: wenn gesetzt und gesehen â†’ HTTP 409 canonical error shape
   - Nach erfolgreichem Call: Key in Buffer eintragen

3. **app/api/routers/operator.py** â€” _audit_operator_request() Helper:
   - Append-only nach artifacts/operator_api_audit.jsonl
   - Felder aus Â§55.7 â€” never-raise (catch + log WARNING)
   - Aufgerufen post-auth, pre-dispatch

4. **app/api/routers/operator.py** â€” Standardisierte Error-Shapes:
   - require_operator_api_token liefert strukturierte Fehler-Bodies gemaess Â§55.5
   - ValueError-Handler nutzt error_code=mode_not_allowed
   - Unbehandelte Exceptions â†’ error_code=internal_error (HTTP 500, nie nackter Stacktrace)

5. **tests/unit/test_operator_governance.py** â€” neue Testdatei:
   - request_id in Response-Body und X-Request-Id-Header vorhanden
   - Idempotency-409 bei Duplikat-Key
   - Canonical Error-Shape fuer alle Fehlertypen
   - Audit-Log-Eintrag bei erfolgreichem Request
   - Keine Trading-Semantik in Error-Shapes

### Â§55.9 Invarianten-Referenz

- docs/intelligence_architecture.md I-331â€“I-340 (Sprint 44)
- ASSUMPTIONS.md A-073â€“A-078 (Sprint 44)
- AGENTS.md P50 (Sprint 44)
- Basis: Â§54/Â§54C (Sprint 43), Â§53D (Sprint 42D)


---

## Â§55C Sprint 44 â€” Operator API Hardening & Request Governance (Konsolidierung â€” kanonisch)

> **Sprint 44C (2026-03-22):** Â§55 (Historischer Entwurf) enthielt Drift zur tatsÃ¤chlichen Implementierung durch Codex. Dieser Block dokumentiert den finalen, implementierten Stand. TatsÃ¤chliche Implementierung in `app/api/routers/operator.py` (597 Zeilen). Kanonischer Referenzstand nach S45C Freeze: 1498 Tests passing.

**Datum**: 2026-03-22
**Status**: âœ… vollstÃ¤ndig implementiert + konsolidiert

### Â§55C.1 Drift-Tabelle (Â§55-Entwurf â†’ tatsÃ¤chliche Implementierung)

| Â§55-Entwurf | TatsÃ¤chliche Implementierung | Korrektur |
|---|---|---|
| request_id = UUID4 | request_id = `req_<uuid4_hex>` (prefix-Format) | Anderes Format |
| Header: `X-Request-Id` | Header: `X-Request-ID` (Gross-D) | Kapitalisierung |
| Kein Correlation-ID | `X-Correlation-ID` Header (defaults auf request_id) | Hinzugekommen |
| Idempotency: optional | Idempotency: **REQUIRED** â€” fehlt â†’ 400 `missing_idempotency_key` | Semantik-Ã„nderung |
| Header: `X-Idempotency-Key` | Header: `Idempotency-Key` | Anderer Header-Name |
| Idempotency: 409 bei Duplikat | Idempotency: **Replay** bei gleichem Key+Payload; 409 `idempotency_key_conflict` bei unterschiedlichem Payload | Anderes Verhalten |
| Rate-Limiting: nicht definiert | Sliding-Window Rate-Limiter: 5 req/30s pro operator_subject (token-fingerprint) â†’ 429 `guarded_rate_limited` | Hinzugekommen |
| Error-Shape: `{error: "<code>", detail: "<msg>", request_id: "<uuid>"}` | Error-Shape: `{error: {code, message, request_id, correlation_id}, execution_enabled: false, write_back_allowed: false}` | Verschachtelt |
| Audit-Log: `artifacts/operator_api_audit.jsonl` (alle Requests) | Audit-Log: `artifacts/operator_api_guarded_audit.jsonl` (nur guarded POST) | Anderer Name + Scope |
| Auth-Code: `api_key_not_configured` | Auth-Code: `operator_api_disabled` | Anderer Error-Code |
| Auth-Code: `missing_auth_header` | Auth-Code: `missing_authorization_header` | Anderer Error-Code |
| Auth-Code: `invalid_auth_scheme` | Auth-Code: `invalid_authorization_scheme` | Anderer Error-Code |

### Â§55C.2 Kanonische Request-Identity (tatsÃ¤chlich implementiert)

**Dependency**: `bind_operator_request_context` (Router-Level)
- Liest `X-Request-ID` Header â€” validiert via `^[A-Za-z0-9._:-]{1,128}$`; sonst `_new_context_id("req")` = `req_<uuid4_hex>`
- Liest `X-Correlation-ID` Header â€” wenn nicht gesetzt, default = request_id
- Speichert in `request.state.operator_request_id` und `request.state.operator_correlation_id`
- Response-Header: `X-Request-ID` und `X-Correlation-ID` via `_set_context_headers()`

### Â§55C.3 Kanonische Error-Shape (tatsÃ¤chlich implementiert)

```json
{
  "error": {
    "code": "<error_code>",
    "message": "<human-readable>",
    "request_id": "<req_hex>",
    "correlation_id": "<corr_hex>"
  },
  "execution_enabled": false,
  "write_back_allowed": false
}
```

**Kanonische error_codes**: `operator_api_disabled` (503) / `missing_authorization_header` (401) / `invalid_authorization_scheme` (401) / `invalid_api_key` (403) / `missing_idempotency_key` (400) / `invalid_idempotency_key` (400) / `idempotency_key_conflict` (409) / `guarded_rate_limited` (429) / `guarded_request_rejected` (400) / `guarded_request_failed` (503) / endpoint-spezifische read-only codes (503).

### Â§55C.4 Kanonischer Idempotency-Kontrakt (tatsÃ¤chlich implementiert)

- **Header**: `Idempotency-Key` (nicht `X-Idempotency-Key`)
- **Pflicht** fÃ¼r `POST /operator/trading-loop/run-once` â€” fehlt â†’ HTTP 400 `missing_idempotency_key`
- **Validierung**: Regex `^[A-Za-z0-9._:-]{1,128}$` â€” ungÃ¼ltig â†’ HTTP 400 `invalid_idempotency_key`
- **Replay**: Gleicher Key + gleicher Payload (SHA256-Fingerprint) â†’ gespeicherte Response mit `idempotency_replayed=True` zurÃ¼ckgegeben (HTTP 200, keine erneute AusfÃ¼hrung)
- **Konflikt**: Gleicher Key + anderer Payload â†’ HTTP 409 `idempotency_key_conflict`
- **Buffer**: In-memory `OrderedDict[str, _IdempotencyRecord]`, maxlen=256, FIFO-Eviction, Thread-safe (Lock)
- **Payload-Fingerprint**: `hashlib.sha256(json.dumps(payload.model_dump()).encode()).hexdigest()`

### Â§55C.5 Kanonischer Rate-Limiter (tatsÃ¤chlich implementiert)

- **Scope**: Nur `POST /operator/trading-loop/run-once`
- **Fenster**: 5 Requests pro 30 Sekunden pro `operator_subject` (= `token_<sha256[:16]>` des Bearer-Tokens)
- **Implementierung**: Sliding-Window mit `deque[float]` (Timestamps), Thread-safe (Lock)
- **Ãœberschreitung**: HTTP 429 `guarded_rate_limited`
- **Trigger-Reihenfolge**: Idempotency-Replay â†’ Rate-Limit-Check (Replay zÃ¤hlt NICHT gegen Rate-Limit)
- **In-memory**: Nicht persistent â€” Restart = leerer State

### Â§55C.6 Kanonisches Audit-Log (tatsÃ¤chlich implementiert)

**Datei**: `artifacts/operator_api_guarded_audit.jsonl` (nur guarded POST, nicht alle Requests)
**Felder**: `timestamp_utc`, `event="operator_guarded_request"`, `endpoint`, `request_id`, `correlation_id`, `idempotency_key`, `outcome` (accepted/rejected/failed/idempotency_replay), `error_code` (oder null), `idempotency_replayed`, `symbol`, `mode`, `provider`, `analysis_profile`, `execution_enabled=false`, `write_back_allowed=false`
**Regeln**: Append-only, never-raise (OSError â†’ silent return), keine Secrets/Tokens

### Â§55C.7 Sicherheitsinvarianten (kanonisch, Sprint 44+44C)

1. Kein unkorrelierter guarded Request â€” `X-Request-ID` und `X-Correlation-ID` immer in Response-Headers
2. `Idempotency-Key` REQUIRED fÃ¼r `POST /operator/trading-loop/run-once` â€” fail-closed bei Fehlen
3. Idempotency-Replay schÃ¼tzt vor Doppel-Execution â€” gleicher Key+Payload â†’ cached Response, keine zweite AusfÃ¼hrung
4. Rate-Limit schÃ¼tzt vor Overload â€” 5/30s pro token-fingerprint, 429 bei Ãœberschreitung
5. Alle Fehler folgen `{"error": {code, message, request_id, correlation_id}, execution_enabled: false, write_back_allowed: false}`
6. Audit-Log enthÃ¤lt keine Secrets, Tokens, Bearer-Werte
7. `execution_enabled=False`, `write_back_allowed=False` in JEDEM Response â€” invariant
8. Kein Live-Pfad â€” `mode=live` â†’ `guarded_request_rejected` (HTTP 400)

### Â§55C.8 Finaler Teststand (Sprint 44+44C)

| Metrik | Wert |
|---|---|
| `test_api_operator.py` | 20 Tests (inkl. Sprint-44-Tests), alle passing âœ… |
| `test_operator_api.py` | 7 Tests (neu geschrieben), alle passing âœ… |
| `test_operator_action_queue.py` | 5 Tests, alle passing âœ… |
| Gesamt | **1498 passed, 0 failed** (kanonischer Referenzstand nach S45C Freeze) |
| ruff | clean âœ… |
| Implementiertes Modul | `app/api/routers/operator.py` (597 Zeilen) |


---

## Â§56 â€” Daily Operator View / get_daily_operator_summary (Sprint 45)

**Sprint**: S45_OPERATOR_USABILITY_BASELINE
**Datum**: 2026-03-22
**Typ**: MCP-Tool-Aggregation + Multi-Surface-Exposition

### Â§56.1 Leitfrage und Nutzenanker

**Leitfrage**: Was muss ein Operator in 30 Sekunden ueber sein Paper-System wissen?

**Antwort**: Ein Daily Operator View, der ohne JSON-Parsing die fuenf operativen Kernfragen beantwortet:

1. Ist das System bereit? (Readiness)
2. Was ist heute passiert? (Zyklen, letzter Status)
3. Wie ist die Markt-Exposition? (Positionen, Exposure)
4. Wie ist die Signallage? (Decision-Pack Status)
5. Gibt es offene Vorfaelle? (Incidents / Journal)

### Â§56.2 MCP-Tool-Contract

**Tool**: `get_daily_operator_summary(...)` (kanonischer MCP-Read-Tool, artifact-path + provider parameterisiert)

**Aggregationsreihenfolge** (Delegation, keine neuen Datenpfade):

| Schritt | Delegiertes Tool | Zielfelder |
|---|---|---|
| 1 | `get_operational_readiness_summary()` | `readiness_status` |
| 2 | `get_recent_trading_cycles(loop_audit_path, last_n=50)` + 24h-Filter | `cycle_count_today`, `last_cycle_status`, `last_cycle_symbol`, `last_cycle_at` |
| 3 | `get_paper_portfolio_snapshot(portfolio_audit_path, market_data_provider)` | `position_count`, `total_equity_usd` |
| 4 | `get_paper_exposure_summary(portfolio_audit_path, market_data_provider)` | `total_exposure_pct`, `mark_to_market_status` |
| 5 | `get_decision_pack_summary(...)` | `decision_pack_status` |
| 6 | `get_review_journal_summary(review_journal_path)` | `open_incidents` |

**Kanonisches Output-Schema**:

```json
{
  "report_type": "daily_operator_summary",
  "readiness_status": "ok | warning | error",
  "cycle_count_today": 0,
  "last_cycle_status": "no_signal | executed | error | null",
  "last_cycle_symbol": "BTC/USDT | null",
  "last_cycle_at": "ISO8601 | null",
  "position_count": 0,
  "total_exposure_pct": 0.0,
  "mark_to_market_status": "ok | stale | unavailable",
  "decision_pack_status": "clear | blocked | warning",
  "open_incidents": 0,
  "execution_enabled": false,
  "write_back_allowed": false,
  "aggregated_at": "ISO8601",
  "sources": ["readiness_summary", "recent_cycles", "portfolio_snapshot", "exposure_summary", "decision_pack_summary", "review_journal_summary"]
}
```

**Aggregations-Invarianten**:

- `execution_enabled` ist immer `false` â€” keine Ausnahme.
- `write_back_allowed` ist immer `false` â€” keine Ausnahme.
- `report_type` ist immer `"daily_operator_summary"`.
- Aggregation ist best-effort: wenn ein Sub-Tool eine Exception wirft, gibt der View degradierte Felder zurueck (Fallback-Werte) und `sources` listet nur erfolgreiche Beitraege.
- Kein neuer externer Datenpfad â€” ausschliesslich Delegation an bestehende MCP-Tools.
- Kein Write-Back, kein Side Effect.

### Â§56.3 CLI-Contract

**Command**: `trading-bot research daily-summary`

**Ausgabe-Format** (menschenlesbar, kein JSON-Dump):

```
=== Daily Operator View ===
Readiness:      ok
Cycles today:   3  (last: no_signal | BTC/USDT | 14:32)
Portfolio:      2 positions | 12.5% exposure | MTM: ok
Decision Pack:  clear
Incidents:      0 open
Aggregated at:  2026-03-22T14:35:00Z
```

**Optionaler Flag**: `--json` gibt das kanonische JSON-Schema aus (fuer Scripting).

### Â§56.4 API-Contract

**Endpoint**: `GET /operator/daily-summary`

- Gleiche Auth-Guardrails wie alle `/operator/*`-Endpoints (Bearer, fail-closed).
- Gleiche Request/Correlation-ID-Propagation.
- Gleiche Error-Shape bei Fehler.
- Response-Body = kanonisches Output-Schema aus Â§56.2.
- `execution_enabled: false`, `write_back_allowed: false` sind Pflichtfelder im Response.

### Â§56.5 Telegram-Contract

**Command**: `/daily_summary`

**Ausgabe-Format** (kompakt, menschenlesbar, kein Raw-JSON):

```
=== Daily Operator View ===
Readiness: ok
Cycles: 3 (last: no_signal, BTC/USDT)
Portfolio: 2 pos | 12.5% exp | MTM: ok
Decision: clear
Incidents: 0 open
```

Diese Ausgabe nutzt dasselbe `get_daily_operator_summary` MCP-Tool.
Keine separate Aggregations-Logik in `telegram_bot.py`.

### Â§56.6 Surface-Delegation-Invariante

Alle vier Surfaces (MCP, CLI, API, Telegram) nutzen **exakt denselben MCP-Tool-Call**.
Keine separate Aggregations-Logik in CLI, API-Router oder Telegram-Bot.
Keine Surface-Drift per Konstrukt.

### Â§56.7 Test-Anforderungen

| Test | Pruefpunkt |
|---|---|
| `test_get_daily_operator_summary_canonical_payload` | report_type, execution_enabled=false, write_back_allowed=false, sources-Liste |
| `test_get_daily_operator_summary_best_effort_degradation` | Sub-Tool-Fehler fuehrt zu degradierten Feldern, nicht zu Exception |
| `test_cli_daily_summary_readable_output` | Ausgabe ist menschenlesbar, enthalt kein raw JSON |
| `test_api_daily_summary_passthrough` | GET /operator/daily-summary, Auth, Request-ID-Headers, Payload |
| `test_telegram_daily_summary_uses_mcp_tool` | TelegramOperatorBot delegiert an get_daily_operator_summary |

### Â§56.8 Non-Goals Sprint 45

- Kein Alerting-System.
- Kein Dashboard-UI.
- Keine neuen Datenquellen.
- Keine LLM-Integration.
- Keine DB-Schema-Aenderungen.
- Kein Live-Pfad.
- Kein `mode=live`-Unterstuetzung (fail-closed wie alle anderen Surfaces).



---

> **Sprint 45C (2026-03-22):** Â§56 wurde als Sprint-45-Definitionsvertrag geschrieben.
> Die Codex-Implementierung weicht in 4 Punkten ab. Kanonische Korrekturen:

## Â§56C â€” Daily Operator Summary: Kanonische Implementierung (Sprint 45C)

**Datum**: 2026-03-22
**Status**: Eingefrorener kanonischer Stand. Ersetzt Â§56 als verbindliche Referenz.

### Â§56C.1 Architektur-Split (korrekt, nicht Drift)

Die Implementierung trennt sauber in zwei Verantwortlichkeiten:

| Schicht | Datei | Rolle |
|---|---|---|
| Reines Modell + Builder | `app/research/operational_readiness.py` | `DailyOperatorSummary` Dataclass + `build_daily_operator_summary(...)` (pure, kein I/O) |
| I/O-Orchestrator | `app/agents/mcp_server.py` | `get_daily_operator_summary(...)` (async, ruft Sub-Tools via `_safe_daily_surface_load`, uebergibt Ergebnisse an `build_daily_operator_summary`) |

Dies ist keine Surface-Drift. Es ist korrekte Trennung von Transformations-Logik und I/O.

### Â§56C.2 Tatsaechliche Parameter-Signatur

```python
async def get_daily_operator_summary(
    handoff_path: str | None = None,
    acknowledgement_path: str = ...,
    state_path: str = ...,
    abc_output_path: str | None = None,
    alert_audit_dir: str = ...,
    stale_after_hours: int = 24,
    artifacts_dir: str = ...,
    retention_stale_after_days: float = 30.0,
    loop_audit_path: str = ...,
    loop_last_n: int = 50,
    portfolio_audit_path: str = ...,
    market_data_provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    review_journal_path: str = ...,
) -> dict[str, object]: ...
```

Â§56 hatte `(mode, provider, analysis_profile)` â€” das ist eine vereinfachte Spezifikation.
Die tatsaechliche Signatur nutzt Dateipfad-Parameter fuer deterministische Testbarkeit.

### Â§56C.3 6 Sub-Tools (nicht 5 wie in Â§56)

| # | Sub-Tool | source_name in `sources` | Zielfeld |
|---|---|---|---|
| 1 | `get_operational_readiness_summary` | `"readiness_summary"` | `readiness_status` |
| 2 | `get_recent_trading_cycles` | `"recent_cycles"` | `cycle_count_today`, `last_cycle_*` |
| 3 | `get_paper_portfolio_snapshot` | `"portfolio_snapshot"` | `position_count` |
| 4 | `get_paper_exposure_summary` | `"exposure_summary"` | `total_exposure_pct`, `mark_to_market_status` |
| 5 | `get_decision_pack_summary` | `"decision_pack_summary"` | `decision_pack_status` |
| 6 | `get_review_journal_summary` | `"review_journal_summary"` | `open_incidents` |

### Â§56C.4 Vollstaendiges Output-Schema

```json
{
  "report_type": "daily_operator_summary",
  "readiness_status": "ok | warning | error | unknown",
  "cycle_count_today": 0,
  "last_cycle_status": "no_signal | executed | error | null",
  "last_cycle_symbol": "BTC/USDT | null",
  "last_cycle_at": "ISO8601 | null",
  "position_count": 0,
  "total_exposure_pct": 0.0,
  "mark_to_market_status": "ok | stale | unavailable | unknown",
  "decision_pack_status": "clear | blocked | warning | unknown",
  "open_incidents": 0,
  "aggregated_at": "ISO8601",
  "sources": ["readiness_summary", "recent_cycles", ...],
  "interface_mode": "read_only",
  "execution_enabled": false,
  "write_back_allowed": false
}
```

**Korrekturen gegenueber Â§56**: `interface_mode: "read_only"` ergaenzt. `sources`-Eintraege sind
`"recent_cycles"` (nicht `"recent_cycles_summary"`). Schema kann `"unknown"` als Status-Wert
enthalten (Fallback bei Sub-Tool-Fehler).

### Â§56C.5 Telegram-Format (tatsaechlich implementiert)

Telegram `/daily_summary` gibt Markdown-Format aus (via `_inline`-Methode):

```
*Daily Summary (Canonical Operator View)*
readiness_status=`ok`
cycle_count_today=`3`
position_count=`2`
total_exposure_pct=`12.5`
decision_pack_status=`clear`
open_incidents=`0`
execution_enabled=`False`
write_back_allowed=`False`
```

Kein roher JSON-Dump. Alle Werte via `self._inline()` escaped.

### Â§56C.6 Kanonische Surface-Delegation-Chain

```
CLI / API / Telegram
        |
        v
mcp_server.get_daily_operator_summary()   [I/O-Orchestrierung]
        |
        +---> get_operational_readiness_summary()
        +---> get_recent_trading_cycles()
        +---> get_paper_portfolio_snapshot()
        +---> get_paper_exposure_summary()
        +---> get_decision_pack_summary()
        +---> get_review_journal_summary()
        |
        v
operational_readiness.build_daily_operator_summary()   [pure Transformation]
        |
        v
DailyOperatorSummary.to_json_dict()   [serialisiert]
```

Kein Parallelpfad. Kein zweiter Backbone.

### Â§56C.7 Teststand Sprint 45C

| Metrik | Wert |
|---|---|
| Gesamt | **1498 passed, 0 failed** (stabil, 2x bestaetigt) |
| `test_get_daily_operator_summary_aggregates_canonical_surfaces` | passing |
| `test_get_daily_operator_summary_degrades_fail_closed_on_surface_error` | passing |
| `test_build_daily_operator_summary_projects_canonical_fields` | passing |
| `test_build_daily_operator_summary_fail_closed_on_partial_inputs` | passing |
| `test_research_daily_summary_prints_human_readable_output` | passing |
| `test_research_daily_summary_json_flag_prints_canonical_payload` | passing |
| `test_operator_read_endpoints_passthrough_canonical_payloads[/operator/daily-summary-...]` | passing |
| `test_read_command_mapping_uses_canonical_surfaces[/daily_summary-...]` | passing |
| ruff | clean |



---

## Â§57 â€” Operator Dashboard Baseline (Sprint 46)

**Sprint**: S46_OPERATOR_DASHBOARD_BASELINE
**Datum**: 2026-03-22
**Typ**: Minimale visuelle Read-Only Operator-Sicht via FastAPI HTMLResponse
**Status**: Implementiert (Codex)
**Referenz-Teststand**: 1503 passed, ruff clean

### Â§57.1 Leitfrage und Grenzen

**Leitfrage**: Wie sieht der Daily Operator View in einer visuellen Sicht aus,
die ohne Telegram oder CLI nutzbar ist?

**Architektur-Grenzen** (nicht verhandelbar):

- Kein zweiter Aggregatpfad. Dashboard liest ausschliesslich via
  `mcp_server.get_daily_operator_summary()`.
- Kein Business-Logic im UI. Nur Praesentation.
- Read-only. Keine guarded Actions via Dashboard.
- Keine neue externe Dependency (kein Jinja2, kein JS-Framework, kein Chart-Lib).
- Kein separater Frontend-Build-Step.

### Â§57.2 Route und Rendering

**Endpoint**: `GET /dashboard`

**Rendering**: FastAPI `HTMLResponse` mit f-string HTML-Template (server-side).
`Content-Type: text/html; charset=utf-8`.

**Auto-Refresh**: HTML `<meta http-equiv="refresh" content="60">` â€” 60 Sekunden.

**Kein Browser-seitiger Auth-Header** erforderlich. Das Dashboard ist ein
lokales Operator-Tool (nicht fuer externe Exposition vorgesehen).

### Â§57.3 Auth-Modell (fail-closed, lokal)

| Zustand | Verhalten |
|---|---|
| `APP_API_KEY` leer | HTTP 503 `dashboard_disabled` (fail-closed, wie alle anderen Endpoints) |
| `APP_API_KEY` gesetzt | Dashboard wird ausgeliefert, kein Bearer-Token im Browser erforderlich |

**Rationale**: Das Dashboard ruft `mcp_server.get_daily_operator_summary()` server-side
auf (gleicher Prozess). Der Browser greift nicht direkt auf die `/operator/*` API zu.
Der `APP_API_KEY`-Check verhindert versehentliche Exposition auf offenen Servern.

### Â§57.4 HTML-Content-Contract

Das Dashboard zeigt in einem einzigen HTML-Dokument:

| Sektion | Quelle | Felder |
|---|---|---|
| Status-Header | `daily_operator_summary` | `readiness_status`, `aggregated_at` |
| Tagesueberblick | `daily_operator_summary` | `cycle_count_today`, `last_cycle_status`, `last_cycle_symbol`, `last_cycle_at` |
| Portfolio | `daily_operator_summary` | `position_count`, `total_exposure_pct`, `mark_to_market_status` |
| Signallage | `daily_operator_summary` | `decision_pack_status` |
| Sicherheitsstatus | `daily_operator_summary` | `execution_enabled`, `write_back_allowed` |
| Vorfaelle | `daily_operator_summary` | `open_incidents` |

**Farb-Konvention** (CSS-only, kein Framework):

- `readiness_status == "ok"` â†’ gruener Indikator
- `readiness_status == "warning"` â†’ oranger Indikator
- `readiness_status == "error" | "unknown"` â†’ roter Indikator

**Kein Chart, kein Diagramm, kein WebSocket, kein JavaScript.**
Nur statisches HTML mit Inline-CSS.

### Â§57.5 Fehler-Verhalten

| Fehlerfall | Verhalten |
|---|---|
| `mcp_server.get_daily_operator_summary()` wirft Exception | HTML-Fehlerseite mit Status "unavailable" â€” kein 500 Stack-Trace |
| `APP_API_KEY` leer | HTTP 503 mit JSON-Error-Body (fail-closed) |

### Â§57.6 Router-Einbindung

Das Dashboard wird als separater FastAPI-Router `app/api/routers/dashboard.py` implementiert.
`app/api/main.py` includet den Router.

**Kein neues MCP-Tool.** Kein neuer CLI-Command. Kein neuer Telegram-Command.
Dashboard ist ausschliesslich eine HTML-Praesentation der bestehenden Daily-Operator-Summary.

### Â§57.7 Test-Anforderungen

| Test | Pruefpunkt |
|---|---|
| `test_dashboard_disabled_when_api_key_missing` | HTTP 503 bei leerem APP_API_KEY |
| `test_dashboard_returns_html_response` | Content-Type text/html, HTTP 200 |
| `test_dashboard_shows_readiness_status` | HTML enthaelt readiness_status-Wert |
| `test_dashboard_shows_execution_disabled` | HTML enthaelt "execution_enabled" und "False" |
| `test_dashboard_degrades_on_summary_error` | Exception in get_daily_operator_summary â†’ HTML-Fehlerseite |

### Â§57.8 Non-Goals Sprint 46

- Kein Charting / Diagramme.
- Kein WebSocket / Real-time.
- Kein JavaScript.
- Kein Login-Formular oder Session-Management.
- Keine guarded Actions.
- Kein separater Frontend-Build.
- Keine neue externe Dependency.
- Kein zweiter Aggregat-Pfad neben `get_daily_operator_summary`.



---

## Â§57C â€” S46 Operator Dashboard Freeze (Canonical Implementation Record)

**Sprint**: S46_OPERATOR_DASHBOARD_BASELINE  
**Freeze-Date**: 2026-03-22  
**Baseline**: 1503 passed, ruff clean  
**Status**: FROZEN

### Â§57C.1 â€” Scope

This section documents the actual Codex implementation of the Sprint-46 dashboard
vs. the Â§57 contract spec. No spec drift detected; Â§57C records the implementation
as authoritative canonical state.

### Â§57C.2 â€” Route

```
GET /dashboard
Response-Class: HTMLResponse (200)
```

### Â§57C.3 â€” Auth Model (fail-closed)

- `APP_API_KEY` empty â†’ `HTTPException(503, dashboard_disabled)` â€” no HTML rendered
- No Bearer token required in browser (server-side rendering, no client-side API calls)

### Â§57C.4 â€” Data Source (single canonical chain)

```
GET /dashboard
  â””â”€â”€ mcp_server.get_daily_operator_summary()   [async I/O orchestrator]
        â””â”€â”€ build_daily_operator_summary(...)    [pure function, no I/O]
```

No second aggregate path. No parallel aggregation logic.

### Â§57C.5 â€” HTML Implementation

- `fastapi.responses.HTMLResponse` + f-string template â€” no Jinja2, no external dependency
- Inline CSS only â€” no JavaScript
- `<meta http-equiv="refresh" content="60">` â€” auto-refresh 60 s
- All values passed through `_safe_text()` (html.escape, quote=True) â€” XSS-safe
- `_readiness_class()` maps `ok/warning/*` â†’ CSS class

### Â§57C.6 â€” Dashboard Fields Rendered

| Field | Source Key | Default |
|---|---|---|
| Readiness | `readiness_status` | `unknown` |
| Cycles Today | `cycle_count_today` | `0` |
| Last Cycle | `last_cycle_status`, `last_cycle_symbol`, `last_cycle_at` | `n/a` |
| Portfolio | `position_count`, `total_exposure_pct`, `mark_to_market_status` | `0`, `0.0`, `unknown` |
| Decision Pack | `decision_pack_status` | `unknown` |
| Open Incidents | `open_incidents` | `0` |
| Safety Flags | `execution_enabled`, `write_back_allowed` | `False`, `False` |
| Aggregated At | `aggregated_at` | `unknown` |

### Â§57C.7 â€” Unavailable State

When `get_daily_operator_summary()` raises or returns non-dict:
- `_render_unavailable_html(reason)` â€” distinct error page, status 200
- Shows: `execution_enabled=False | write_back_allowed=False`

### Â§57C.8 â€” Tests (5 new, total baseline 1503)

1. `test_dashboard_disabled_when_api_key_missing` â€” 503 when APP_API_KEY empty
2. `test_dashboard_returns_html_response` â€” 200, HTML response with refresh meta tag
3. `test_dashboard_shows_readiness_status` â€” rendered HTML contains readiness status value
4. `test_dashboard_shows_execution_disabled` â€” rendered HTML contains `execution_enabled=False` and `write_back_allowed=False`
5. `test_dashboard_degrades_on_summary_error` â€” mcp_server exception degrades to unavailable HTML page

### Â§57C.9 â€” Ruff Fix (post-Codex)

Codex implementation had 1 ruff issue (extra blank line in import block â†’ I001).
Fixed by `ruff check --fix`. No logic changes.

### Â§57C.10 â€” Invariant Confirmation

All I-351..I-360 invariants confirmed satisfied by implementation:

| Invariant | Confirmed |
|---|---|
| I-351: single GET /dashboard route | âœ… |
| I-352: fail-closed on empty API key (503) | âœ… |
| I-353: delegates to get_daily_operator_summary() only | âœ… |
| I-354: no second aggregate path | âœ… |
| I-355: HTMLResponse, no JavaScript | âœ… |
| I-356: auto-refresh 60 s | âœ… |
| I-357: all values HTML-escaped | âœ… |
| I-358: unavailable page on exception or invalid payload | âœ… |
| I-359: no new external dependency (no Jinja2) | âœ… |
| I-360: 5 tests covering auth, render, readiness, safety flags, unavailable | âœ… |


---

## Â§57D â€” S46D Dashboard Truth Verification (Pre-Sprint-47 Gate)

**Sprint**: S46D_DASHBOARD_TRUTH_VERIFICATION  
**Date**: 2026-03-22  
**Scope**: Verification only (no feature expansion)

### Â§57D.1 Goal

Confirm semantic truth alignment between:

- `GET /operator/daily-summary` (canonical JSON read surface)
- `GET /dashboard` (canonical HTML projection)

### Â§57D.2 Canonical Truth Rules

1. `GET /dashboard` remains the only dashboard route.
2. `/static/dashboard.html` does not exist and is not routed.
3. `GET /dashboard` delegates only to `mcp_server.get_daily_operator_summary()`.
4. Dashboard field values are projected from the same canonical daily payload used by `/operator/daily-summary`.
5. `execution_enabled=False` and `write_back_allowed=False` remain visible in dashboard output.

### Â§57D.3 Verification Tests

1. `test_dashboard_truth_matches_operator_daily_summary_payload`
2. `test_dashboard_route_inventory_is_canonical_in_main_app`

### Â§57D.4 Validation Snapshot

| Metric | Value |
|---|---|
| S46C freeze baseline | 1503 passed, ruff clean |
| S46D verification run | 1506 passed, ruff clean |
| Dashboard routes | `/dashboard` only |


---

## Â§58 â€” S46D Dashboard Truth Verification Contract

**Sprint**: S46D_DASHBOARD_TRUTH_VERIFICATION  
**Status**: CLOSED (resolved in S46D verification run: 1506 passed, ruff clean)  
**Owner**: Claude Code (Contract), Codex (Tests), Antigravity (E2E)  
**Predecessor**: Â§57C (S46C frozen 2026-03-22)

### Â§58.1 â€” Scope

S46D closes the final acceptance gap for the Sprint-46 dashboard: verifying that
`GET /dashboard` renders exactly the fields it claims to render, with values that
match the canonical `DailyOperatorSummary` payload used by `GET /operator/daily-summary`.

No new UI features. No new routes. Only truth alignment and test coverage.

### Â§58.2 â€” Field Alignment Matrix

`DailyOperatorSummary.to_json_dict()` produces 16 fields.

| Field key | In `to_json_dict()` | Rendered by Dashboard | Notes |
|---|---|---|---|
| `report_type` | âœ… | âŒ | Not displayed (meta-field) |
| `readiness_status` | âœ… | âœ… | CSS class + value |
| `cycle_count_today` | âœ… | âœ… | Card: Cycles Today |
| `last_cycle_status` | âœ… | âœ… | mono line in Cycles card |
| `last_cycle_symbol` | âœ… | âœ… | mono line in Cycles card |
| `last_cycle_at` | âœ… | âœ… | mono line in Cycles card |
| `position_count` | âœ… | âœ… | Card: Portfolio |
| `total_exposure_pct` | âœ… | âœ… | mono line in Portfolio card |
| `mark_to_market_status` | âœ… | âœ… | mono line in Portfolio card |
| `decision_pack_status` | âœ… | âœ… | Card: Decision Pack |
| `open_incidents` | âœ… | âœ… | Card: Open Incidents |
| `aggregated_at` | âœ… | âœ… | Header block |
| `sources` | âœ… | âŒ | Not displayed (debug-field) |
| `interface_mode` | âœ… | âŒ | Not displayed (always read_only) |
| `execution_enabled` | âœ… | âœ… | Card: Safety Flags |
| `write_back_allowed` | âœ… | âœ… | Card: Safety Flags |

**13 fields rendered. 3 deliberately omitted** (`report_type`, `sources`, `interface_mode`).

### Â§58.3 â€” Codex Verification Implementation (completed)

Implemented tests in `tests/unit/test_api_dashboard.py`:

1. `test_dashboard_truth_matches_operator_daily_summary_payload`
2. `test_dashboard_route_inventory_is_canonical_in_main_app`

Earlier draft test name `test_dashboard_renders_all_fields` is superseded by the
two finalized S46D verification tests above.

Historical field matrix intent (covered by the implemented tests):
- Use `_daily_payload()` fixture (existing helper)
- Mock `mcp_server.get_daily_operator_summary()` â†’ fixture
- Call `GET /dashboard`
- Assert ALL 13 rendered field values appear in `response.text`:
  - `readiness_status` â†’ fixture value "warning"
  - `cycle_count_today` â†’ "2"
  - `last_cycle_status` â†’ "no_signal"
  - `last_cycle_symbol` â†’ "BTC/USDT"
  - `last_cycle_at` â†’ "2026-03-22T12:00:00+00:00"
  - `position_count` â†’ "1"
  - `total_exposure_pct` â†’ "12.5"
  - `mark_to_market_status` â†’ "ok"
  - `decision_pack_status` â†’ "warning"
  - `open_incidents` â†’ "1"
  - `aggregated_at` â†’ "2026-03-22T12:05:00+00:00"
  - `execution_enabled` â†’ "False"
  - `write_back_allowed` â†’ "False"

No new production code changes unless a field is found missing from the HTML template.

### Â§58.4 â€” Antigravity E2E Protocol

Manual verification against running instance (requires `APP_API_KEY` set):

```bash
# Step 1: Get canonical JSON
curl -s -H "Authorization: Bearer $APP_API_KEY"   http://localhost:8000/operator/daily-summary | python3 -m json.tool

# Step 2: Get dashboard HTML
curl -s http://localhost:8000/dashboard | grep -E "class="value|class="mono"

# Step 3: Cross-check each field value appears in both outputs
```

Acceptance criteria for Antigravity:
- `readiness_status` matches in JSON and HTML
- `cycle_count_today` value in HTML matches JSON integer
- `execution_enabled` and `write_back_allowed` both show `False` in HTML
- `aggregated_at` timestamp in HTML header matches JSON field
- No field in the HTML shows "unknown" or "n/a" when JSON has a real value

### Â§58.5 â€” Exit Criteria

- [x] `test_dashboard_truth_matches_operator_daily_summary_payload` â€” alle 13 Felder cross-surface âœ…
- [x] `test_dashboard_route_inventory_is_canonical_in_main_app` â€” kein `/static/dashboard.html` âœ…
- [x] `python -m pytest` â†’ **1506 passed** (1503 + 2 S46D tests + 1 startup-regression test) âœ…
- [x] `python -m ruff check .` clean âœ…
- [x] Live-Instanz E2E cross-check gegen laufende Instanz (kein Mock) âœ…
- [x] Field alignment matrix (Â§58.2) formal bestÃ¤tigt als authoritative âœ…

**S46D final: GELIEFERT (2026-03-22)**. Live-Instanz-E2E erfolgreich abgeschlossen.

### Â§58.6 â€” Out of Scope

- No new dashboard cards or fields
- No `sources` or `interface_mode` display (explicitly omitted, not a bug)
- No feature flags, no live/broker expansion
- No second aggregate path


### Â§58.7 â€” Antigravity Live-Instanz E2E Ergebnis (2026-03-22)

**Methode**: realer Uvicorn-Live-Prozess (`python -m uvicorn app.api.main:app`), kein Mock auf `mcp_server`.  
**Baseline**: 1506 passed, ruff clean.

#### Â§58.7.1 â€” Field Alignment (Live)

| Field | JSON-Wert | HTML-Rendered | Status |
|---|---|---|---|
| `readiness_status` | "critical" | âœ… sichtbar | OK |
| `cycle_count_today` | 0 | âœ… sichtbar | OK |
| `last_cycle_status` | `None` | "n/a" | OK (null â†’ default) |
| `last_cycle_symbol` | `None` | "n/a" | OK (null â†’ default) |
| `last_cycle_at` | `None` | "n/a" | OK (null â†’ default) |
| `position_count` | 0 | "0 positions" | OK |
| `total_exposure_pct` | 0.0 | "0.0%" | OK |
| `mark_to_market_status` | "ok" | âœ… sichtbar | OK |
| `decision_pack_status` | "blocking" | âœ… sichtbar | OK |
| `open_incidents` | 0 | âœ… sichtbar | OK |
| `aggregated_at` | Tâ‚ | Tâ‚‚ (â‰  Tâ‚) | SEE Â§58.7.2 |
| `execution_enabled` | `False` | "execution_enabled=False" | OK |
| `write_back_allowed` | `False` | "write_back_allowed=False" | OK |

#### Â§58.7.2 â€” Befund: aggregated_at Timing-Charakteristik (kein Bug)

`DailyOperatorSummary.aggregated_at` wird bei **jedem** Aufruf von
`mcp_server.get_daily_operator_summary()` neu generiert (`datetime.now(UTC).isoformat()`).

`GET /operator/daily-summary` und `GET /dashboard` rufen den Aggregator **unabhÃ¤ngig** auf.
Die zwei Timestamps weichen daher typischerweise um Millisekunden bis Sekunden ab.

**Bewertung**: Kein Bug. Das Dashboard rendert `aggregated_at` korrekt â€” der Wert entspricht
dem Aggregations-Zeitstempel des Dashboard-Calls, nicht dem des JSON-API-Calls.
Beide sind gÃ¼ltige kanonische Momentaufnahmen.

**Konsequenz**: Cross-Surface-Vergleich des `aggregated_at`-Feldes ist nicht sinnvoll.
Alle anderen 12 Felder sind deterministische Zustandswerte und mÃ¼ssen Ã¼bereinstimmen.

#### Â§58.7.3 â€” Safety und Routing

- `execution_enabled=False` und `write_back_allowed=False` in HTML: âœ… bestÃ¤tigt
- `/dashboard` in App-Routen: âœ…
- `/static/dashboard.html` nicht in App-Routen: âœ…
- `canonical source: get_daily_operator_summary` in HTML: âœ…

#### Â§58.7.4 â€” Â§58 Exit Criteria (final)

- [x] `test_dashboard_truth_matches_operator_daily_summary_payload` âœ…
- [x] `test_dashboard_route_inventory_is_canonical_in_main_app` âœ…
- [x] `python -m pytest` â†’ 1506 passed âœ…
- [x] `python -m ruff check .` â†’ clean âœ…
- [x] Live-Instanz-Abgleich: semantische Wahrheit bestÃ¤tigt, `aggregated_at` als Timing-Charakteristik dokumentiert âœ…
- [x] Field alignment matrix Â§58.2 als autoritativ bestÃ¤tigt âœ…

**S46D GESCHLOSSEN â€” 2026-03-22**


---

## Â§59 â€” S47 Operator Drilldown & History Baseline Contract

**Sprint**: S47_OPERATOR_DRILLDOWN_HISTORY_BASELINE  
**Status**: CLOSED (Codex delivery merged; live-instance verification completed)  
**Predecessor**: Â§58 / S46D (CLOSED 2026-03-22)

### Â§59.1 â€” Scope

S47 schlieÃŸt zwei verbleibende Surface-LÃ¼cken im Operator-API:
`GET /operator/review-journal` und `GET /operator/resolution-summary`.

Beide sind bereits als MCP-Tools (`get_review_journal_summary`, `get_resolution_summary`)
und als CLI-Kommandos (`review-journal-summary`, `resolution-summary`) vorhanden.
S47 macht sie Ã¼ber die REST-API konsistent erreichbar â€” pure Delegation, kein neues Aggregat.

### Â§59.2 â€” Neue Routen

| Methode | Pfad | MCP-Delegation | Beschreibung |
|---|---|---|---|
| GET | `/operator/review-journal` | `mcp_server.get_review_journal_summary()` | Read-only Operator-Review-Journal-Zusammenfassung |
| GET | `/operator/resolution-summary` | `mcp_server.get_resolution_summary()` | Per-Source-AuflÃ¶sungsstatus aus dem Review-Journal |

### Â§59.3 â€” Implementierungsregeln (Delegation-Only)

- Kein neues Aggregat-Modell. Kein neues Datenmodell. Keine neue Businesslogik.
- Beide Endpoints delegieren 1:1 an bestehende MCP-Tools.
- Gleiche Error-Shape wie alle anderen Operator-Endpoints:
  `{"detail": {"error": {"code": "...", "message": "..."}, "execution_enabled": false, "write_back_allowed": false}}`
- Gleiche Auth-Anforderung: `Authorization: Bearer <APP_API_KEY>` (kein Whitelist-Eintrag).
- `X-Request-ID` / `X-Correlation-ID` Header in Response (wie alle anderen Operator-Endpoints).
- `execution_enabled: false`, `write_back_allowed: false` in Response body.

### Â§59.4 â€” Query-Parameter

| Endpoint | Parameter | Default | Bedeutung |
|---|---|---|---|
| `/operator/review-journal` | `journal_path` | `artifacts/operator_review_journal.jsonl` | Pfad zum Append-Only Journal |
| `/operator/resolution-summary` | `journal_path` | `artifacts/operator_review_journal.jsonl` | Gleiche Quelle wie review-journal |

### Â§59.5 â€” Kanonische Drilldown-Kette (dokumentiert, nicht neu gebaut)

Der Operator folgt dieser Kette von der Daily Summary bis zum Detail:

```
GET /operator/daily-summary           â† Tageseinstieg (S45/S46)
GET /dashboard                        â† Visueller Ãœberblick (S46)

  â†“ Drilldown (bereits vorhanden, jetzt dokumentiert)

GET /operator/readiness               â† Issues-Liste, Severity
GET /operator/decision-pack           â† Blocking-Entscheidungen
GET /operator/trading-loop/status     â† Loop-State
GET /operator/trading-loop/recent-cycles?last_n=50  â† Cycle-History
GET /operator/portfolio-snapshot      â† Positionen
GET /operator/exposure-summary        â† Exposure-Breakdown

  â†“ History / Journal (NEU in S47)

GET /operator/review-journal          â† Operator-Review-EintrÃ¤ge
GET /operator/resolution-summary      â† Per-Source-AuflÃ¶sungsstatus
```

### Â§59.6 â€” Explizit Out of Scope

- Kein Browser-Navigation im Dashboard (keine `<a href>` zu auth-Endpoints ohne JS)
- Keine neuen Aggregationsmodelle
- Keine neuen Datenspeicher
- Kein `decision-journal` Endpoint (intern, kein Operator-PrimÃ¤r-Surface)
- Keine Live/Broker/Execution-Features
- Keine zweite Daily-Aggregat-Kette

### Â§59.7 â€” Work Orders

- **Claude Code**: Â§59 Contract und Doku-Sync (diese Sektion)
- **Codex**:
  - Neue Endpoints in `app/api/routers/operator.py` (analog zu `get_operator_trading_loop_status`) âœ…
  - Tests: je 2 Tests pro Endpoint (success + error-degradation) âœ…
  - `app/api/routers/operator.py` Prefix: `/operator`
  - Error-Code: `review_journal_unavailable` / `resolution_summary_unavailable`
- **Antigravity**: Live-Instanz-Check beider neuer Endpoints (leerer Journal â†’ leer aber 200)

### Â§59.8 â€” Exit Criteria

- [x] `GET /operator/review-journal` liefert 200 und delegiert an `mcp_server.get_review_journal_summary()`
- [x] `GET /operator/resolution-summary` liefert 200 und delegiert an `mcp_server.get_resolution_summary()`
- [x] Error-Shape identisch mit anderen Operator-Endpoints bei Failure
- [x] 4 neue Tests (2 Ã— 2) in `tests/unit/test_api_operator.py` oder neue Datei
- [x] `python -m pytest` â†’ **1510 passed** âœ…
- [x] `python -m ruff check .` clean âœ…
- [x] Drilldown-Kette in RUNBOOK.md Â§5d dokumentiert âœ…
- [x] Live E2E: 200, `execution_enabled=False`, `write_back_allowed=False`, X-Request-ID âœ…

**S47 GELIEFERT (2026-03-22) â€” Live-Check bestanden.**

### Â§59.9 â€” Invarianten

| # | Invariant |
|---|---|
| I-364 | `GET /operator/review-journal` delegiert ausschliesslich an `mcp_server.get_review_journal_summary()` â€” kein eigenes Aggregat |
| I-365 | `GET /operator/resolution-summary` delegiert ausschliesslich an `mcp_server.get_resolution_summary()` â€” kein eigenes Aggregat |
| I-366 | Beide Endpoints erfordern `Authorization: Bearer <APP_API_KEY>` â€” kein Whitelist-Eintrag |
| I-367 | `execution_enabled: false` und `write_back_allowed: false` sind in der Response beider Endpoints sichtbar |
| I-368 | Die Drilldown-Kette (Â§59.5) ist dokumentiert und implementiert â€” kein zweiter Daily-Aggregat-Pfad entsteht |


## Â§60 â€” S47 Reconciliation Record

**Sprint**: S47_OPERATOR_DRILLDOWN_HISTORY_BASELINE (CLOSED)  
**Reconciliation Date**: 2026-03-22

### Â§60.1 â€” Geplant vs. geliefert

| Aspekt | Â§59-Spezifikation | TatsÃ¤chlich geliefert |
|---|---|---|
| `GET /operator/review-journal` | âœ… geplant | âœ… geliefert |
| `GET /operator/resolution-summary` | âœ… geplant | âœ… geliefert |
| Error-Shape | identisch mit anderen Endpoints | âœ… korrekt |
| Tests | â‰¥ 4 neue Tests | âœ… >4 (parametrisch + MCP + CLI) |
| Baseline | â‰¥ 1510 | âœ… 1510 passed |
| Dashboard Drilldown-Navigation | im Entwurf erwÃ¤hnt | âŒ auf S48 verschoben (kein JS mÃ¶glich) |
| Telegram `/resolution` / `/decision_pack` | im Entwurf erwÃ¤hnt | âŒ auf S48 verschoben |

### Â§60.2 â€” S47 Scope-EinschrÃ¤nkung (bewusste Entscheidung)

Dashboard-Navigation zu auth-geschÃ¼tzten Endpoints ist ohne JavaScript technisch nicht
mÃ¶glich (Browser kann keinen Bearer-Token bei `<a href>` senden). Telegram-Kommandos
fÃ¼r neue S47-Surfaces wurden bewusst auf S48 verschoben, um S47 minimal zu halten.

### Â§60.3 â€” Kanonischer S47-Stand (definitiv)

- `GET /operator/review-journal` âœ…
- `GET /operator/resolution-summary` âœ…
- Beide: 200, `execution_enabled=False`, `write_back_allowed=False`, X-Request-ID
- Baseline: **1510 passed, ruff clean**

---

## Â§61 â€” S48 Operator Surface Completion Contract

**Sprint**: S48_OPERATOR_SURFACE_COMPLETION  
**Status**: CLOSED (surface parity reached; baseline validated)  
**Contract Freeze**: Â§61 ist kanonisch; frÃ¼here Dashboard-Subpage-EntwÃ¼rfe sind superseded.  
**Predecessor**: Â§60 / S47 (CLOSED 2026-03-22)

### Â§61.1 â€” Scope

S48 schlieÃŸt die verbleibenden Surface-LÃ¼cken aus S47:
1. Telegram: `/resolution` und `/decision_pack` Kommandos (API/CLI-ParitÃ¤t)
2. Dashboard: statische Drilldown-Referenz-Sektion (kein JS, kein zweiter Backend-Call)

Kein neues Aggregat. Keine neue Businesslogik. Keine neuen Datenmodelle.

### Â§61.2 â€” Surface Completion Matrix

| Surface | daily_summary | readiness | decision_pack | review_journal | resolution | portfolio | exposure | trading_loop |
|---|---|---|---|---|---|---|---|---|
| **API** | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… |
| **CLI** | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… |
| **Telegram** | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | âœ… | n/a |
| **Dashboard** | âœ… (cards) | âœ… (visual) | âœ… (status) | n/a | n/a | âœ… (count) | âœ… (%) | âœ… (last) |

### Â§61.3 â€” Neue Telegram-Kommandos

#### `/resolution` (neu)

```
Handler: _cmd_resolution
Loader:  mcp_server.get_resolution_summary()
Output:  per-source resolution status (latest state per source)
Format:  Markdown inline (identisch mit /journal Pattern)
```

Pflichtfelder im Output:
- `resolution_status` (overall)
- `resolved_count`, `open_count`, `total_count`
- `execution_enabled=False`, `write_back_allowed=False`

#### `/decision_pack` (neu)

```
Handler: _cmd_decision_pack
Loader:  mcp_server.get_decision_pack_summary()
Output:  canonical decision pack overview
Format:  Markdown inline
```

Pflichtfelder im Output:
- `decision_pack_status`
- `blocking_count`, `operator_action_count`
- `execution_enabled=False`, `write_back_allowed=False`

### Â§61.4 â€” Dashboard Drilldown-Referenz (statisch)

Eine neue `<section class="drilldown-ref">` am Ende des Dashboard-HTML, **kein** zweiter
Backend-Call, **kein** JavaScript. Statischer Text mit API-Pfaden fÃ¼r den Operator:

```html
<section class="drilldown-ref">
  <p class="label">Drilldown (Bearer required)</p>
  <ul class="mono">
    <li>/operator/readiness</li>
    <li>/operator/decision-pack</li>
    <li>/operator/trading-loop/recent-cycles</li>
    <li>/operator/review-journal</li>
    <li>/operator/resolution-summary</li>
  </ul>
</section>
```

CSS-only, keine neuen Styles nÃ¶tig (`.mono` bereits vorhanden).

### Â§61.5 â€” Implementierungsregeln

- Telegram-Commands folgen exakt dem `_load_canonical_surface` Pattern (wie `/journal`)
- Dashboard-Sektion ist reines HTML im f-string Template â€” kein zusÃ¤tzlicher Backend-Call
- Gleiche Error-Shape fÃ¼r neue Telegram-Commands bei Loader-Fehler
- `execution_enabled=False`, `write_back_allowed=False` in allen neuen Telegram-Outputs

### Â§61.6 â€” Work Orders

- **Claude Code**: Â§61 Contract + Doku-Sync (diese Sektion)
- **Codex**:
  - `_cmd_resolution` in `app/messaging/telegram_bot.py` + Routing in command-map
  - `_cmd_decision_pack` in `app/messaging/telegram_bot.py` + Routing
  - `_get_resolution_summary` private loader (analog zu `_get_review_journal_summary`)
  - `_get_decision_pack_summary` private loader
  - Drilldown-Referenz-Sektion in `_render_dashboard_html()` (f-string, kein JS)
  - Tests: je 2 pro neuem Telegram-Command + 1 Test fÃ¼r Dashboard-Drilldown-Sektion
- **Antigravity**: Live-Instanz-Check beider Telegram-Commands + Dashboard-HTML-Verifikation

### Â§61.7 â€” Exit Criteria

- [x] `/resolution` Telegram-Command â€” 200, `execution_enabled=False`, `write_back_allowed=False`
- [x] `/decision_pack` Telegram-Command â€” 200, gleiche Invarianten
- [x] Dashboard HTML enthÃ¤lt statische Drilldown-Referenz-Sektion (kein JS, kein zweiter Call)
- [x] 5 neue Tests (2 + 2 + 1)
- [x] `python -m pytest` >= 1515 passed
- [x] `python -m ruff check .` clean
- [x] Surface Completion Matrix Â§61.2 vollstÃ¤ndig grÃ¼n (Telegram: decision_pack + resolution)
- [x] Antigravity E2E Surface-Parity-Check (live instance) dokumentiert

### Â§61.8 â€” Invarianten

| # | Invariant |
|---|---|
| I-369 | `/resolution` Telegram-Command delegiert ausschliesslich an `mcp_server.get_resolution_summary()` |
| I-370 | `/decision_pack` Telegram-Command delegiert ausschliesslich an `mcp_server.get_decision_pack_summary()` |
| I-371 | Dashboard Drilldown-Referenz-Sektion enthÃ¤lt keinen JavaScript-Code und keinen zweiten Backend-Call |
| I-372 | Alle neuen Telegram-Outputs zeigen `execution_enabled=False` und `write_back_allowed=False` |
| I-373 | Surface Completion Matrix Â§61.2 ist die kanonische Wahrheit Ã¼ber Surface-ParitÃ¤t |


---

## Â§61B â€” Historical Note (superseded)

Dieser frÃ¼here Scope-Freeze-Block ist historisch und **nicht bindend**.
FÃ¼r Sprint 48 gilt ausschlieÃŸlich der kanonische Contract-Strang:

- Â§60 (S47 Reconciliation)
- Â§61 (S48 Operator Surface Completion Contract)

---

## Â§62 â€” S49 Operator Alerting/Digest Baseline Contract

**Sprint**: S49_OPERATOR_ALERTING_DIGEST_BASELINE  
**Status**: ACTIVE  
**Predecessor**: Â§61 / S48 (CLOSED 2026-03-22)

### Â§62.1 â€” Scope

S49 fÃ¼gt eine read-only Operator-Sicht auf den Alert-Audit-Trail hinzu.
Keine Ã„nderung am Pipeline-Alerting, kein Digest-Scheduling, keine Ã„nderung am
Daily-Backbone (`DailyOperatorSummary`).

**Exakt 3 Deliverables:**
1. MCP-Tool `get_alert_audit_summary()` â€” liest `artifacts/alert_audit.jsonl`
2. `GET /operator/alert-audit` â€” delegiert an neues MCP-Tool
3. Telegram `/alert_status` â€” delegiert an dasselbe MCP-Tool

### Â§62.2 â€” Bestehende Infrastruktur (nicht verÃ¤ndern)

| Komponente | Pfad | Status |
|---|---|---|
| `AlertService` | `app/alerts/service.py` | âœ… vorhanden â€” nicht verÃ¤ndern |
| `DigestCollector` | `app/alerts/digest.py` | âœ… vorhanden â€” nicht aktivieren |
| `ThresholdEngine` | `app/alerts/threshold.py` | âœ… vorhanden â€” nicht verÃ¤ndern |
| `AlertAuditRecord` | `app/alerts/audit.py` | âœ… vorhanden â€” read-only lesen |
| `_build_alert_dispatch_summary` | `app/research/operational_readiness.py` | âœ… vorhanden â€” wiederverwenden |
| `POST /alerts/test` | `app/api/routers/alerts.py` | âœ… vorhanden â€” nicht verÃ¤ndern |
| `ALERT_DRY_RUN=true` | Default | âœ… bleibt Standard |

### Â§62.3 â€” Neues MCP-Tool: `get_alert_audit_summary`

Implementierung in `app/agents/mcp_server.py` (nach `get_resolution_summary`):

- Liest `alert_audit.jsonl` via `load_alert_audits(resolved)`
- Ruft `_build_alert_dispatch_summary(audits)` auf (bereits vorhanden in `operational_readiness.py`)
- Gibt zurÃ¼ck: `report_type="alert_audit_summary"`, `total_count`, `digest_count`, `by_channel`, `latest_dispatched_at`, `alert_audit_dir`, `interface_mode="read_only"`, `execution_enabled=False`, `write_back_allowed=False`

### Â§62.4 â€” Neuer API-Endpoint: `GET /operator/alert-audit`

- Error-Code: `alert_audit_unavailable`
- Query-Param: `alert_audit_dir` (default: `artifacts/alert_audit`)
- Auth: Bearer required (kein Whitelist-Eintrag)
- Pattern: identisch zu `GET /operator/review-journal`

### Â§62.5 â€” Neuer Telegram-Command: `/alert_status`

- Handler: `_cmd_alert_status`
- Loader: `_get_alert_audit_summary` â†’ `mcp_server.get_alert_audit_summary()`
- Pattern: identisch zu `_cmd_journal` / `_cmd_resolution`
- Output: `total_count`, `digest_count`, `latest_dispatched_at`, Safety-Flags

### Â§62.6 â€” Explizit Out of Scope

| Idee | Entscheidung |
|---|---|
| Digest-Scheduling / Cron-Trigger | âŒ zu komplex fÃ¼r S49 |
| Ã„nderung an `DailyOperatorSummary` | âŒ Daily-Backbone ist frozen |
| Ã„nderung an `pipeline/service.py` | âŒ kein Pipeline-Change |
| Aktivierung `ALERT_DIGEST_ENABLED=true` | âŒ bleibt default-off |
| Neue Alert-Regeln / Thresholds | âŒ ThresholdEngine nicht verÃ¤ndern |
| Dashboard Alert-Card | âŒ wÃ¼rde zweiten Backend-Call erfordern |
| Operator-Alerting als Auto-Trigger | âŒ Phase-2-Guardrail: no unvalidated critical execution |

### Â§62.7 â€” Exit Criteria

- [ ] `get_alert_audit_summary()` MCP-Tool in `app/agents/mcp_server.py`
- [ ] `GET /operator/alert-audit` â†’ 200, delegiert an MCP-Tool, `execution_enabled=False`
- [ ] Telegram `/alert_status` â†’ korrekte Felder, Safety-Flags, Degradation-Handling
- [ ] 4 neue Tests: 2 x API (success + error) + 2 x Telegram (success + error)
- [ ] `python -m pytest` â†’ >= 1519 passed
- [ ] `python -m ruff check .` â†’ clean
- [ ] Live E2E: `GET /operator/alert-audit` â†’ 200, leerer Audit-Trail â†’ `total_count=0`
- [ ] `ALERT_DRY_RUN=true` bleibt Default â€” kein reales Alerting ausgelÃ¶st

### Â§62.8 â€” Invarianten

| # | Invariant |
|---|---|
| I-378 | `get_alert_audit_summary()` liest nur `alert_audit.jsonl` â€” kein neues Aggregat |
| I-379 | `GET /operator/alert-audit` erfordert Bearer-Auth â€” kein Whitelist-Eintrag |
| I-380 | `execution_enabled=False`, `write_back_allowed=False` in allen S49-Responses |
| I-381 | `ALERT_DRY_RUN=true` bleibt Standard â€” S49 verÃ¤ndert keine Alert-Trigger |
| I-382 | `DailyOperatorSummary` und `build_daily_operator_summary` werden in S49 nicht verÃ¤ndert |
| I-383 | Kein Digest-Scheduling, kein Cron-Trigger, kein Auto-Dispatch in S49 |

---

<a name="s50a-canonical-inventory"></a>

## Phase-3 Closed Contracts (§§63–§66) — S50 Canonical Consolidation

> Canonical Phase-3 contracts. §§63–§64 closed. §65 frozen. §66 frozen.

---

### Â§63 â€” S50A_CANONICAL_PATH_INVENTORY (Phase 3)

**Sprint**: `S50A_CANONICAL_PATH_INVENTORY`
**Phase**: 3 â€” Canonical Consolidation
**Status**: active
**Baseline**: 1519 passed, ruff clean

#### Â§63.1 â€” Mission

Produce a canonical path inventory across all operator-facing surfaces.
No code changes. No new features. No tests added or removed.
The sole deliverable is a complete, role-readable inventory document.

The inventory answers three questions for every surface entry:
1. Is this path canonical, an alias, or superseded?
2. Where is it implemented?
3. Where is it tested?

#### Â§63.2 â€” Surface Scope

| Surface | Canonical Source | Scope |
|---|---|---|
| MCP tools | `app/agents/mcp_server.py` | all read tools and the one guarded tool |
| Operator API endpoints | `app/api/routers/operator.py` | all `/operator/*` routes |
| Dashboard endpoint | `app/api/routers/dashboard.py` | `GET /dashboard` |
| CLI research commands | `app/cli/main.py` | all `research` subcommands |
| Telegram commands | `app/messaging/telegram_bot.py` | all read-only + audit-only + guarded commands |

#### Â§63.3 â€” Classification Vocabulary

| Class | Meaning |
|---|---|
| `canonical` | Primary, documented, and tested path. Single source of truth for its function. |
| `alias` | Secondary name for a canonical path. Backed by the same implementation. Not a duplicate backbone. |
| `superseded` | Formerly active path replaced by a canonical successor. Code may still exist but is no longer the preferred entry. |
| `provisional` | Implemented and registered, but not yet accepted into the locked final canonical inventory. |

#### Â§63.4 â€” Deliverable Format

The inventory is produced as a structured Markdown table per surface.
Each row contains: surface, path/name, classification, implementation file, test file, contract Â§, notes.

Inventory file: `CANONICAL_SURFACE_INVENTORY.md` (root of repo).

#### Â§63.5 â€” Role-Aligned Work Orders

**Codex**:
- enumerate all MCP tools, API endpoints, CLI commands, Telegram commands from source files
- classify each entry per Â§63.3
- write `CANONICAL_SURFACE_INVENTORY.md` with one section per surface
- flag any alias or superseded entries that still appear in canonical docs (naming drift)
- no code changes; no test changes

**Antigravity**:
- review `CANONICAL_SURFACE_INVENTORY.md` for operator readability and onboarding clarity
- confirm no gap between inventory and what a new team member would encounter at runtime
- note any undocumented entry or unexpected gap (report only, no fix in this sprint)

**Claude Code**:
- define this contract (Â§63)
- update governance docs with S50A sprint state
- review inventory for governance consistency with RUNBOOK.md, ONBOARDING.md, contracts.md

#### Â§63.6 â€” Explicitly Out of Scope

| Idea | Decision |
|---|---|
| Any code change in `app/` | âŒ S50A is doc-only |
| New API endpoints or CLI commands | âŒ no new product features in S50 |
| Refactoring or renaming existing surfaces | âŒ changes go to a later S50 sub-sprint after inventory |
| New test files | âŒ test baseline stays at 1519 |
| Changes to daily backbone or aggregation | âŒ frozen |
| Removing superseded code | âŒ flagging only; removal is a separate gate |

#### Â§63.7 â€” Exit Criteria

- [x] `CANONICAL_SURFACE_INVENTORY.md` created at repo root
- [x] All 5 surfaces covered: MCP, API, Dashboard, CLI, Telegram
- [x] Every entry classified as canonical / alias / superseded / provisional
- [x] Each entry has implementation file reference
- [x] Naming drift between inventory and governance docs flagged (if any)
- [x] `python -m pytest` â†’ 1519 passed (unchanged)
- [x] `python -m ruff check .` â†’ clean (unchanged)
- [x] Claude Code governance-review complete â€” PASS (2026-03-22); F-S50A-001 carried to S50B
- [x] Antigravity readability review complete â€” PASS (2026-03-22)
- [x] S50A formal freeze closure completed (2026-03-22)

**Freeze status**: inventory snapshot frozen 2026-03-22 (Claude + Antigravity PASS). S50A is formally closed. F-S50A-001 (15 provisional CLI) is carried as active S50B work (not a freeze blocker).

#### Â§63.8 â€” Invariants

| # | Invariant |
|---|---|
| I-384 | S50A produces zero code changes â€” `git diff app/` must be empty |
| I-385 | Test baseline must remain 1519 passed after S50A |
| I-386 | No new aggregation backbone introduced in S50A |
| I-387 | `CANONICAL_SURFACE_INVENTORY.md` is read-only audit doc â€” not a runtime contract |
| I-388 | Classification vocabulary is fixed at Â§63.3 â€” no extra custom category beyond canonical/alias/superseded/provisional |

#### Â§63.9 â€” Final Review and Freeze Gate (Historical, closed 2026-03-22)

Gate completion record:

- Antigravity readability/onboarding review complete (PASS)
- Claude governance consistency review complete (PASS)
- freeze decision recorded across phase/sprint/governance docs
- S50A formally closed; S50B permitted to open

S50B now governs command-by-command disposition of the provisional CLI set.

#### Â§63.10 â€” Freeze Closure Update (2026-03-22)

- Antigravity readability/onboarding review: PASS
- S50A formal freeze closure: complete
- `CANONICAL_SURFACE_INVENTORY.md` is the frozen S50A inventory artifact
- S50B is now allowed to open with narrow governance scope
- F-S50A-001 remains open as S50B work, but is not a freeze blocker

---

### Â§64 â€” S50B Provisional CLI Governance (Sync/freeze completed 2026-03-22)

Status:
- S50A is formally closed.
- Claude governance review: PASS.
- Antigravity readability/onboarding review: PASS.
- `CANONICAL_SURFACE_INVENTORY.md` is the frozen S50A inventory artifact.
- S50B was narrowed to a sync/freeze sprint for command-classification consistency.
- F-S50A-001 is materially resolved by classification sync.
- S50B is now closed after command-classification sync.
- Next sprint target was defined as `S50C_CLI_CONTRACT_FREEZE` at S50B closeout; it is opened in Â§65.

S50B objective:
- classify all 15 provisional CLI commands with explicit governance decisions
- keep scope narrow and governance-led
- avoid broad CLI refactoring before command-by-command decisions are complete

Allowed decision outcomes per command:
- `promote_to_canonical`
- `keep_provisional`
- `alias_to_canonical`
- `supersede`

Mandatory decision criteria:
1. operator relevance for canonical day-to-day flow
2. naming clarity and ambiguity risk
3. overlap with canonical commands (duplicate surface risk)
4. maintenance burden and coverage confidence
5. governance/safety impact if promoted

Guardrails:
- no new product feature logic
- no execution or trading semantic expansion
- no bulk rename/removal before per-command decisions are recorded

Classification rationale:
- 15/15 confirmed test coverage; no canonical command overlap; 3/15 are MCP-backed.
- 12/15 are internal pipeline/governance tools where MCP is not required.
- Decision: promote all 15 to canonical (2026-03-22). F-S50A-001 resolved.

Command classification worklist (15) â€” resolved:

| Command | Previous class | S50B decision | Notes |
|---|---|---|---|
| `backtest-run` | provisional | `promote_to_canonical` | internal pipeline; test coverage confirmed |
| `benchmark-companion` | provisional | `promote_to_canonical` | internal pipeline; test coverage confirmed |
| `benchmark-companion-run` | provisional | `promote_to_canonical` | internal pipeline; test coverage confirmed |
| `brief` | provisional | `promote_to_canonical` | operator-facing summary; test coverage confirmed |
| `check-promotion` | provisional | `promote_to_canonical` | governance tool; test coverage confirmed |
| `dataset-export` | provisional | `promote_to_canonical` | pipeline export; test coverage confirmed |
| `decision-journal-append` | provisional | `promote_to_canonical` | MCP-backed (`append_decision_instance`); test coverage confirmed |
| `decision-journal-summary` | provisional | `promote_to_canonical` | MCP-backed (`get_decision_journal_summary`); test coverage confirmed |
| `evaluate` | provisional | `promote_to_canonical` | evaluation pipeline; test coverage confirmed |
| `evaluate-datasets` | provisional | `promote_to_canonical` | evaluation pipeline; test coverage confirmed |
| `prepare-tuning-artifact` | provisional | `promote_to_canonical` | training pipeline; test coverage confirmed |
| `record-promotion` | provisional | `promote_to_canonical` | governance tracking; test coverage confirmed |
| `shadow-report` | provisional | `promote_to_canonical` | internal shadow/audit projection; test coverage confirmed |
| `signals` | provisional | `promote_to_canonical` | internal research projection; test coverage confirmed |
| `watchlists` | provisional | `promote_to_canonical` | MCP-backed (`get_watchlists`); test coverage confirmed |

Exit criteria:

- all 15 provisional CLI commands have an explicit, recorded governance decision
- `CANONICAL_SURFACE_INVENTORY.md` updated to reflect final classifications
- no new provisional entries introduced during S50B
- `python -m pytest` remains green (baseline: 1519 passed)
- `python -m ruff check .` remains clean

All Â§64 exit criteria are met (2026-03-22).

Invariants:

- **I-389**: No CLI command may be renamed, removed, or promoted without an explicit S50B governance decision recorded in this contract and in `CANONICAL_SURFACE_INVENTORY.md`.
- **I-390**: S50B scope is strictly governance-led; no new product feature logic, execution semantic, or operator surface may be introduced.
- **I-391**: The test baseline (1519 passed, ruff clean) must remain unchanged throughout S50B unless a separate remediation sprint is opened.

S50B status: **closed** (2026-03-22). All exit criteria met. Â§64 is frozen.

---

### Â§65 â€” S50C CLI Contract Freeze (Closed 2026-03-22)

Status:
- S50B is formally closed.
- S50C is formally closed.
- All 15 provisional CLI commands are canonical (D-29).
- CLI canonical count: 53. Provisional set: 0.
- F-S50A-001: resolved.

S50C objective:
- freeze the final CLI canonical contract
- confirm `CANONICAL_SURFACE_INVENTORY.md` reflects the post-S50B canonical state
- ensure all governance docs are internally consistent and contradiction-free
- no new feature work, no new execution semantics, no new aggregation backbone

Frozen CLI canonical set (53 commands):

`signal-handoff`, `handoff-acknowledge`, `handoff-collector-summary`, `readiness-summary`, `provider-health`, `drift-summary`, `gate-summary`, `remediation-recommendations`, `artifact-inventory`, `artifact-rotate`, `artifact-retention`, `cleanup-eligibility-summary`, `protected-artifact-summary`, `review-required-summary`, `escalation-summary`, `blocking-summary`, `operator-action-summary`, `action-queue-summary`, `blocking-actions`, `prioritized-actions`, `review-required-actions`, `decision-pack-summary`, `daily-summary`, `operator-runbook`, `runbook-summary`, `runbook-next-steps`, `review-journal-append`, `review-journal-summary`, `resolution-summary`, `market-data-quote`, `market-data-snapshot`, `paper-portfolio-snapshot`, `paper-positions-summary`, `paper-exposure-summary`, `trading-loop-status`, `trading-loop-recent-cycles`, `trading-loop-run-once`, `alert-audit-summary`, `backtest-run`, `benchmark-companion`, `benchmark-companion-run`, `brief`, `check-promotion`, `dataset-export`, `decision-journal-append`, `decision-journal-summary`, `evaluate`, `evaluate-datasets`, `prepare-tuning-artifact`, `record-promotion`, `shadow-report`, `signals`, `watchlists`

Aliases (4): `consumer-ack` â†’ `handoff-acknowledge`, `handoff-summary` â†’ `handoff-collector-summary`, `operator-decision-pack` â†’ `decision-pack-summary`, `loop-cycle-summary` â†’ `trading-loop-recent-cycles`

Superseded (1): `governance-summary` (removed from final inventory)

Exit criteria:
- `CANONICAL_SURFACE_INVENTORY.md` reflects 53 canonical commands and 0 provisional
- all governance docs consistent with this Â§65 freeze record
- `python -m pytest` remains green (baseline: 1519 passed)
- `python -m ruff check .` remains clean

Invariants:
- **I-392**: The CLI canonical set is frozen at 53 commands as of S50C. Any addition or removal requires an explicit governance decision and a new contract section.
- **I-393**: S50C introduces no new product feature logic, execution semantics, or operator surfaces.
- **I-394**: The test baseline (1519 passed, ruff clean) must remain unchanged throughout S50C.

S50C status: **closed** (2026-03-22). §65 is frozen.

---

### Â§66 â€” S50D Doc Hygiene and Structure (Structure Rules Frozen 2026-03-22)

Status:
- S50C is closed with no remaining technical blocker.
- S50D is closed as a historical Phase-3 sub-sprint.
- Scope is governance/documentation structure only.
- Baseline remains: 1519 passed, ruff clean.
- `S50D_STRUCTURE_RULES_FREEZE` is recorded as completed in this section.
- Next required step at close: `PHASE3_CLOSEOUT_AND_NEXT_PHASE_GATE` (completed GO).

S50D objective:
- define canonical document-structure rules for large governance docs
- apply those rules to reduce onboarding friction and ambiguity
- keep runtime/product behavior unchanged

Canonical structure rules for large governance docs:
1. Every governance file must expose exactly one current sprint state line (`current_phase`, `current_sprint`, `next_required_step`, baseline) at the top.
2. Active and closed sprint sections must be clearly separated; closed sections must be labeled historical/closed and never use present-tense "active" wording.
3. Long command inventories remain anchored in canonical sources (`CANONICAL_SURFACE_INVENTORY.md`, `docs/contracts.md`) and are referenced from other docs instead of duplicated.
4. Each large governance doc must keep a compact "guardrails and out-of-scope" block to prevent accidental feature drift during documentation sprints.
5. Structural edits are allowed; runtime/business logic, command behavior, and API/MCP/CLI surfaces are not changed in S50D.
6. Cross-doc sprint tokens must match exactly (`S50D_DOC_HYGIENE_AND_STRUCTURE`) until the sprint is formally closed.

Structure-rule freeze record (`S50D_STRUCTURE_RULES_FREEZE`):
- Freeze timestamp: 2026-03-22
- Freeze scope: rule text in this section (items 1-6), not runtime behavior
- Change policy: rule changes require an explicit decision-log entry and contract update
- Implementation order: apply rules first to `TASKLIST.md`, then to broader governance docs
- Next structural action: prepare a guarded split/trim plan for `docs/contracts.md` (structure-only, no semantic contract edits)

S50D.3 guarded split/trim plan for `docs/contracts.md`:
1. Build a section inventory map (`section_id`, title, start/end line, sprint tag, status: active/historical).
2. Mark freeze-critical blocks that must stay in place during S50D (`§65`, `§66`, invariants).
3. Define split candidates by age/status only (historical closed sprint blocks first), not by changing semantic ownership.
4. Define trim policy: move verbose historical narrative into archived companion docs while keeping canonical contract statements in the main file.
5. Produce a reference-preservation table before any move (`old_anchor`, `new_location`, `status`).
6. Apply restructuring only after plan review is documented in changelog/knowledge base.

Reference strategy freeze (`S50D.3`):
- No contract statement is deleted; it can only be relocated with a trace entry.
- Every moved block must keep its original section identifier reference in the new location.
- All cross-doc references must continue to resolve via either same section id or explicit mapping in the preservation table.
- Historical traceability is mandatory; "trim" never means "erase".

Target docs for S50D structural sync:
- `TASKLIST.md`
- `AGENTS.md`
- `DECISION_LOG.md`
- `KNOWLEDGE_BASE.md`
- `PHASE_PLAN.md`
- `SPRINT_LEDGER.md`

Exit criteria:
- Â§66 structure rules are documented, frozen, and referenced by governance docs
- `S50D.3_CONTRACTS_SPLIT_TRIM_PLAN` is documented and reference strategy is frozen before any major contracts restructuring
- S50D target docs are synchronized to one sprint state and one baseline statement
- no `app/` code changes introduced as part of S50D
- `python -m pytest` remains green (baseline: 1519 passed)
- `python -m ruff check .` remains clean

Invariants:
- **I-395**: S50D is documentation-governance only; any `app/` code change is out of scope.
- **I-396**: S50D introduces no new product features, no new command/tool surfaces, and no execution semantics.
- **I-397**: Baseline integrity remains mandatory (`1519 passed`, `ruff clean`).
- **I-398**: Governance docs must not advertise multiple active sub-sprints at once.
- **I-399**: S50D must reduce ambiguity, not add parallel governance structures.
- **I-400**: Structure-rule changes are blocked unless a new governance decision and contract diff are recorded.
- **I-401**: Contracts split/trim actions are blocked until the reference-preservation table is defined.
- **I-402**: S50D contracts trim cannot remove historical contract evidence; only controlled relocation is allowed.

Structure rules status: **frozen** (2026-03-22). Rules apply to all S50D target docs.

S50D status: **closed** (2026-03-22).

---

<a name="s67-ph4a-signal-quality-audit-baseline"></a>

## §67 — PH4A_SIGNAL_QUALITY_AUDIT_BASELINE

**Sprint**: `PH4A_SIGNAL_QUALITY_AUDIT_BASELINE`
**Phase**: 4 (closed baseline)
**Opened**: 2026-03-22
**Decision**: D-42
**Freeze**: completed (2026-03-22)
**Closed**: 2026-03-22 (D-53)
**Next required step**: `PH4B_RESULTS_REVIEW`

### Freeze Record (normative)

Frozen metrics set:
- score distribution (`relevance`, `sentiment`, `novelty`, `impact`)
- signal-to-noise ratio (`actionable` vs. non-actionable)
- alert precision proxy (high-priority audit share)
- tier coverage ratio (Tier 3 invocation vs. Tier 1 fallback)

Frozen data slice:
- analyzed records only (`status=analyzed`)
- fixed at execution-start boundary (single freeze timestamp)
- no new source/provider data admitted into the PH4A baseline slice after freeze

Frozen output artifacts:
- machine-readable metrics artifact
- machine-readable gap catalog (minimum 3 ranked gaps)
- operator-readable summary with explicit pass/fail statement

Frozen non-goals:
- no new data sources
- no new LLM/provider integrations
- no scoring algorithm or threshold changes
- no new operator surfaces or architecture changes

### Scope

Audit the quality of existing analysis outputs (Tier 1 RuleAnalyzer + Tier 3 LLM provider) before any Phase-4 source or provider expansion. Establish measurable quality baseline metrics as a reference anchor for future expansion decisions.

### Non-Goals

- No new data sources added during PH4A
- No new LLM/provider integrations during PH4A
- No new operator surfaces (CLI commands, MCP tools, API endpoints, Telegram commands)
- No changes to the three-tier intelligence stack architecture

### Quality Audit Target Areas

| Area | What to Measure |
|---|---|
| Score distribution | Relevance, sentiment, novelty, impact across real documents |
| Signal-to-noise | Ratio of actionable vs. noise documents reaching research outputs |
| Alert precision | Ratio of alerts flagged as genuinely high-priority vs. total alerts |
| Tier coverage | How often Tier 3 (LLM) is invoked vs. Tier 1 fallback |
| Gap identification | Top 3+ concrete quality gaps ranked by operator impact |

### Acceptance Criteria

- Score distribution audit across Tier 1 + Tier 3 outputs is documented
- Top quality gaps identified and ranked (minimum 3 concrete gaps)
- Quality baseline metrics recorded as future comparison anchor
- No new data sources added during sprint
- No new LLM/provider integrations added during sprint
- `python -m pytest` green (baseline: 1519 passed)
- `python -m ruff check .` clean

### Invariants

- **I-403**: No new data sources are added during PH4A. Source expansion is blocked until the quality baseline is accepted.
- **I-404**: No new LLM/provider integrations are added during PH4A.
- **I-405**: Quality metrics (score distribution, signal-to-noise, alert precision) must be defined and baselined before any Phase-4 expansion sprint opens.

### Contract Freeze (2026-03-22)

**Status**: frozen — execution may now begin.

#### Frozen Metrics (exact definitions)

| Metric | Definition | Granularity |
|---|---|---|
| Score distribution | Mean, median, p10, p90 for each field: `relevance_score`, `sentiment_score`, `novelty_score`, `impact_score`, `priority_score` | Per tier (Tier 1 separate from Tier 3) |
| Tier coverage | % of documents in the data slice that have a Tier 3 result vs. Tier 1 fallback | Aggregate |
| Signal-to-noise | % of analyzed documents where `priority_score` meets or exceeds the current research-output inclusion threshold | Aggregate |
| Alert precision | % of triggered alerts in the data slice that are classified as operator-relevant (high-priority) | Aggregate; proxy metric acceptable if no labeled set exists |
| Gap catalog | Named quality gaps, each with: gap description, affected tier(s), estimated operator impact (high/medium/low) | Ranked list; minimum 3 entries |

#### Frozen Data Slice

- Source: all `CanonicalDocument` records with an existing `AnalysisResult` at audit start date
- No new document ingestion during audit execution
- Tier 1 and Tier 3 results measured separately; cross-tier summary produced
- Slice is closed at audit start — no retroactive additions

#### Frozen Output Artifacts

1. Score distribution table (per metric, per tier)
2. Tier coverage ratio (single number + breakdown)
3. Signal-to-noise ratio (single number)
4. Alert precision ratio (single number or proxy)
5. Gap catalog (≥ 3 gaps, ranked by operator impact)
6. Quality baseline summary — operator-readable, one coherent document

#### Frozen Non-Goals (enforcement)

- No new data sources added (I-403)
- No new LLM/provider integrations (I-404)
- No scoring algorithm changes during the audit (changes belong in a follow-on sprint)
- No alert threshold adjustments during the audit
- No new operator surfaces
- No Tier 2 implementation
- No ML training or fine-tuning

PH4A contract status: **closed baseline** (2026-03-22). Reference baseline is frozen.

---

<a name="s68-ph4b-tier3-coverage-expansion"></a>

## §68 — PH4B_TIER3_COVERAGE_EXPANSION

**Sprint**: `PH4B_TIER3_COVERAGE_EXPANSION`
**Phase**: 4
**Opened**: 2026-03-22
**Decision**: D-54
**Status**: closed (D-62)
**Current step**: `none`

### Scope

Increase Tier-3 overlap on existing analyzed documents using current canonical surfaces and runtime paths. PH4B re-routes documents that were Tier-1 only through the Tier-3 (external LLM) analysis path. This creates paired records (same document analyzed by both tiers) that enable meaningful cross-tier comparison. PH4B is structural and overlap-first — not a provider, source, or model expansion sprint.

### Baseline Inputs (frozen from PH4A §67)

| Metric | PH4A Value | PH4B Target |
|---|---|---|
| audited_records | 74 | same corpus (no expansion) |
| tier3_coverage | 6.76% (5/74) | measurably higher |
| paired_count | 0 | > 0 (primary gate) |
| signal_to_noise (priority >= 8) | 0.00% | measured post-expansion |
| benchmark_status | needs_more_data | data (at least 1 pair) |

### Non-Goals

- No new data sources (I-406)
- No new LLM providers or models (I-406)
- No scoring algorithm changes (I-407)
- No alert threshold changes (I-407)
- No new operator surfaces
- No Tier-2 implementation
- No trading/execution semantics

### Contract and Acceptance Freeze Target

#### Primary Acceptance Criterion

**`paired_count > 0`** — at least one document must have both a Tier-1 and a Tier-3 `AnalysisResult`. This is the minimum gate. Coverage ratio improvement alone does not satisfy acceptance.

#### Required Output Artifacts

1. Updated tier coverage ratio (post-PH4B execution, same 74-record slice)
2. Updated paired_count (must be > 0 to pass)
3. Score comparison table — Tier-1 vs Tier-3 scores on the same documents (mean/median per metric)
4. Updated signal-to-noise ratio (priority >= 8, same threshold as PH4A)
5. Benchmark comparison artifact (`benchmark_rule_vs_teacher.json` updated with actual pairs)
6. PH4B execution summary (operator-readable; pass/fail relative to PH4A baseline)

#### Acceptance Gates (freeze-to-execution)

- [x] `paired_count > 0` in benchmark artifact (`69`)
- [x] tier_coverage_ratio > 6.76% (PH4B: `100.0%`)
- [x] No scoring/threshold/source/provider/model changes made (execution_observations confirmed)
- [x] `python -m pytest` passes (baseline: 1519 passed)
- [x] `python -m ruff check .` clean

#### Non-Goals (enforcement)

- No new sources added (I-406)
- No new providers or models added (I-406)
- No scoring algorithm changes during PH4B (I-407)
- No threshold adjustments during PH4B (I-407)
- PH4A baseline artifacts remain unchanged as comparison anchor

### Invariants

- **I-406**: PH4B must operate on the existing document corpus using existing providers; no source or model expansion.
- **I-407**: PH4B must not change scoring or threshold contracts; PH4A baseline comparability must be preserved.
- **I-408**: PH4B acceptance requires real overlap evidence (`paired_count > 0`), not coverage-only ratio improvement.

### Execution Result Snapshot (2026-03-23)

- `paired_count`: `69` (PH4A baseline: `0`)
- `tier3_coverage`: `100.0%` (PH4A baseline: `6.76%`)
- `signal_to_noise`: `5.80%` (PH4A baseline: `0.00%`)
- Score divergence on paired set: `priority_mae=3.13`, `relevance_mae=0.41`, `impact_mae=0.32`

PH4B contract status: **closed (D-62)**. Execution and review artifacts are frozen as comparison anchors.

---

## §69 — PH4B_RESULTS_REVIEW

**Sprint**: `PH4B_TIER3_COVERAGE_EXPANSION (results-review gate)`
**Phase**: 4
**Opened**: 2026-03-23
**Decision**: D-58, D-59, D-61
**Status**: closed (D-62)

### Purpose

Review PH4B execution artifacts against PH4A baseline to explain the Tier-1 vs Tier-3 divergence, cluster gap patterns, and select the next narrow quality-improvement sprint. This is a review gate — no execution, scoring, or provider changes are permitted within this gate.

### Review Reference (locked 2026-03-23)

| Metric | PH4A | PH4B | Delta |
|---|---|---|---|
| tier3_coverage | 6.76% | 100.0% | +93.24% |
| paired_count | 0 | 69 | +69 |
| signal_to_noise | 0.00% | 5.80% | +5.80% |
| priority_mae | — | 3.13 | — |
| relevance_mae | — | 0.41 | — |
| impact_mae | — | 0.32 | — |
| severe_priority_gap_cases | — | 18 | — |
| tag_overlap_mean | — | 0.00% | — |

### Review Observations (locked)

- Tier-1 frequently falls back to default scores when no keyword match exists.
- The paired divergence profile indicates rule coverage gaps are likely primary before any threshold tuning.

### Review Tasks

1. Cluster documents by divergence magnitude (Tier-1 priority vs Tier-3 priority delta)
2. Identify document classes where Tier-1 consistently underestimates
3. Identify top rule-signal gaps driving the priority_mae of 3.13
4. Evaluate whether divergence reflects rule underestimation or LLM over-scoring
5. Select the next narrow sprint (candidate: rule-gap audit)

### Non-Goals (enforcement during review)

- No scoring algorithm changes
- No threshold changes
- No new sources, providers, or models
- No re-execution of shadow analysis
- No broad quality reform before gap diagnosis is complete

### Acceptance Gates

- [x] Divergence patterns clustered and explained
- [x] Top rule-signal gaps identified
- [x] Follow-up sprint candidate defined narrowly (`PH4C_RULE_KEYWORD_COVERAGE_AUDIT`)
- [x] Governance sync complete

### Invariants (inherited from §68)

- I-406, I-407, I-408 remain active through review gate
- PH4A (§67) and PH4B (§68) artifacts remain immutable comparison anchors

§69 status: **closed (D-62 — 2026-03-23)**

---

## §70 — PH4C_RULE_KEYWORD_COVERAGE_AUDIT

**Sprint**: `PH4C_RULE_KEYWORD_COVERAGE_AUDIT`
**Phase**: 4
**Opened**: 2026-03-23
**Decision**: D-63, D-64, D-65
**Status**: closed (D-66)

### Purpose

Diagnostic audit of Tier-1 keyword and pattern coverage against the 69 paired documents from PH4B. Goal is to identify which rule patterns fail to match the severe divergence cases and produce a ranked coverage matrix. This is a diagnostic sprint — no rule changes, no scoring changes, no threshold changes are permitted.

### Scope

- Input: 69 paired documents from PH4B (Tier-1 + Tier-3 analyzed)
- Focus: the 18 severe divergence cases (|priority_delta| >= 5) are the primary diagnostic target
- Output: keyword gap list per rule, ranked by sum of priority delta across unmatched documents; coverage matrix (rules × documents)

### Execution Result Snapshot (locked)

- KeywordEngine indexed terms: `507`
- analyzed paired documents: `69`
- hit distribution:
  - zero-hit: `29/69`
  - low-hit: `27/69`
  - good-hit: `13/69`
- low-hit bucket carries the largest average delta (`+3.4`)
- top missing thematic categories:
  - macro/finance
  - regulatory/legal
  - AI/technology

### Non-Goals (hard freeze)

- No keyword additions or edits to Tier-1 rules
- No scoring algorithm changes
- No threshold changes
- No new sources, providers, or models
- No re-execution of PH4B shadow analysis

### Acceptance Gates

- [x] Keyword hit analysis run across all 69 paired documents
- [x] Coverage matrix produced (rules × documents, zero-hit cells highlighted)
- [x] Top-N missing keyword/topic categories identified and ranked
- [x] Operator summary produced with gap list and PH4D scope recommendation
- [x] Governance sync complete

### Invariants (inherited from §68)

- I-406: no new sources inside PH4C
- I-407: no new providers or models inside PH4C
- I-408: no scoring or threshold changes inside PH4C
- PH4A (§67), PH4B (§68, §69) artifacts remain immutable comparison anchors

§70 status: **closed (D-66 — 2026-03-23)**

---

## §71 — PH4D_TARGETED_KEYWORD_EXPANSION_BASELINE

**Sprint**: `PH4D_TARGETED_KEYWORD_EXPANSION_BASELINE`
**Phase**: 4
**Opened**: 2026-03-23
**Decision**: D-67, D-68, D-69
**Status**: closed (D-68)

### Purpose

Targeted keyword expansion for Tier-1 rules covering the 3 confirmed category gaps identified in PH4C: macro/finance, regulatory/legal, AI/technology. Goal is to reduce the 29 zero-hit and 27 low-hit documents by adding focused keyword terms. A baseline comparison run follows keyword additions to measure impact before any further expansion.

### Scope

- Target: the 29/69 zero-hit + 27/69 low-hit documents from PH4C
- Expansion: add keyword terms to existing Tier-1 rule patterns for exactly 3 categories
- Categories: (1) macro/finance, (2) regulatory/legal, (3) AI/technology
- Comparison: before/after hit-rate run on the same 69 paired documents

### Non-Goals (hard freeze)

- No scoring algorithm changes
- No threshold changes
- No new sources, providers, or models
- No categories beyond the 3 confirmed gaps (requires new D-decision)
- No re-execution of PH4B or PH4C analysis

### Acceptance Gates

- [x] Keyword terms added for all 3 categories (macro/finance, regulatory/legal, AI/technology)
- [x] Before/after hit-rate comparison run on 69 paired documents
- [x] Zero-hit and low-hit counts re-measured post-expansion
- [x] Impact documented: delta in hit rates per category
- [x] Governance sync complete

### Invariants (inherited from §68)

- I-406: no new sources inside PH4D
- I-407: no new providers or models inside PH4D
- I-408: no scoring or threshold changes inside PH4D
- PH4A (§67), PH4B (§68, §69), PH4C (§70) artifacts remain immutable comparison anchors

### Execution Result Snapshot (D-69, locked)

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| zero-hit | 29 | 26 | -3 |
| low-hit | 27 | 25 | -2 |
| good-hit | 13 | 18 | +5 |

Additional evidence:
- transitions: `zero->low=3`, `low->good=5`, `regressions=0`
- keyword index terms: `507 -> 555` (`+48`)
- category target-set hit rates (56 legacy zero+low docs):
  - macro/finance: `10.71%`
  - regulatory/legal: `7.14%`
  - AI/technology: `12.50%`
- remaining zero-hit docs: `26`
  - true rule gaps: `5`
  - correctly low-value noise: `21`

§71 status: **closed (formalized in PH4D_CLOSE_AND_PH4E_DEFINITION)**

---

## §72 — Phase 4 Interim Review and Next Sprint Selection

**Sprint**: `PHASE4_INTERIM_REVIEW_AND_NEXT_SPRINT_SELECTION`
**Phase**: 4
**Opened**: 2026-03-23
**Decision**: D-64
**Status**: closed (review complete; superseded by PH4D_CLOSE_AND_PH4E_DEFINITION)

### Purpose
Review the PH4A–PH4D evidence arc as a whole to determine the highest-leverage next Phase-4 sprint. Keyword expansion has delivered most of its value. The next sprint must be selected from remaining quality levers.

### Scope
- Review PH4A–PH4D evidence arc: paired_count=69, priority_mae=3.13, keyword index 507→555, zero-hit 29→26
- Analyze remaining quality levers: scoring calibration, sentiment extraction, data volume/diversity
- Identify the highest-leverage next sprint target
- Produce a narrow, actionable PH4E scope recommendation

### Non-Goals (hard freeze)
- No scoring algorithm changes inside this review sprint
- No threshold changes
- No new keyword expansion without evidence that the 5 remaining true gaps are the highest-leverage target
- No PH4E definition before review output is documented

### Acceptance Gates
- [x] Phase 4 arc summary reviewed and documented
- [x] Remaining quality levers ranked by leverage
- [x] Next sprint scope selected with rationale
- [x] Governance sync complete

§72 status: **closed (review complete; handoff to PH4D_CLOSE_AND_PH4E_DEFINITION)**

---

## §73 — PH4E_SCORING_CALIBRATION_AUDIT

**Sprint**: `PH4E_SCORING_CALIBRATION_AUDIT`
**Phase**: 4
**Opened**: 2026-03-23
**Decision**: D-70
**Status**: closed (execution complete; results accepted)

### Purpose
Diagnostic audit of per-field scoring inputs to identify the root cause of priority_mae=3.13 (approximately 2× error threshold). Keyword expansion has reached diminishing returns; scoring calibration is the next highest-leverage quality lever.

### Scope
- Per-field analysis: relevance, impact, novelty, actionable, sentiment scores
- Identify which AnalysisResult field(s) drive the priority divergence
- Classify root cause: default value assignment vs calibration drift vs missing signal
- Produce divergence cluster analysis on the 69 paired documents

### Non-Goals (hard freeze)
- No scoring formula changes
- No weight or threshold changes
- No rule changes
- No new data sources, providers, or models
- No keyword expansion

### Contract Freeze Record (2026-03-23)

- Frozen input slice: 69 paired PH4B documents (no corpus expansion in PH4E).
- Frozen purpose: explain priority divergence root causes before any intervention sprint.
- Frozen constraints: diagnostic-only execution; no runtime behavior changes.

### Freeze Gates (definition-to-execution)

- [x] Scope narrowed to scoring divergence diagnostics only
- [x] Input slice locked to existing paired set
- [x] Non-goals locked explicitly in contract text
- [x] Governance synchronization completed

### Acceptance Gates
- [x] Per-field score distribution analyzed across 69 paired documents
- [x] Top-3 scoring failure modes identified and ranked by divergence impact
- [x] Root cause classified (defaults / calibration / missing signals)
- [x] Governance sync complete
- [x] PH4F scope recommendation documented

### Execution Findings (locked)

- `relevance_score` contributes `41.2%` of the priority gap.
- `impact_score` contributes `32.6%` of the priority gap.
- `novelty_score` contributes `26.1%` of the priority gap.
- Rule `relevance_score=0` in `81.2%` of paired documents.
- Rule `actionable` is never set in the paired set.
- Primary bottleneck: input completeness, not score calibration tuning.

§73 status: **closed (next step completed via PH4E_CLOSE_AND_PH4F_DEFINITION)**

---

<a name="s74-ph4f-rule-input-completeness-audit"></a>

## §74 — PH4F_RULE_INPUT_COMPLETENESS_AUDIT

**Sprint**: `PH4F_RULE_INPUT_COMPLETENESS_AUDIT`
**Phase**: 4
**Opened**: 2026-03-23
**Decision**: D-68
**Status**: closed

### Purpose
Diagnostic audit of rule-input completeness gaps that drive Tier-1 under-specification. PH4F isolates missing input fields before any intervention sprint is allowed.

### Scope
- Analyze missing/empty rule input fields on the 69 paired documents.
- Quantify per-field completeness and relation to priority divergence.
- Cluster documents by input-gap pattern (not by score tuning outcome).
- Produce ranked input-field gap list and evidence-linked operator summary.

### Non-Goals (hard freeze)
- No direct rule changes
- No scoring formula or threshold changes
- No provider/source/model expansion
- No runtime behavior changes
- No auto-remediation

### Contract Freeze Record (2026-03-23)

- Frozen input slice: same 69 paired documents used in PH4E.
- Frozen objective: explain rule-input incompleteness as root-cause layer.
- Frozen execution mode: diagnostic-only, read-only artifacts.

### Freeze Gates (definition-to-execution)

- [x] Scope limited to rule-input completeness diagnostics
- [x] Input slice locked to PH4E paired set
- [x] Non-goals explicitly frozen
- [x] Governance synchronization completed

### Acceptance Gates
- [x] Per-field completeness matrix produced for paired set
- [x] Top-3 missing input-field classes identified and ranked
- [x] Gap-to-divergence linkage documented with evidence refs
- [x] Operator-readable PH4F summary produced
- [x] PH4G candidate recommendation documented
- [x] PH4F formal closeout review completed

### Execution Findings (locked)

- `RuleAnalyzer.analyze()` is not the production Tier-1 path.
- Production Tier-1 path is fallback analysis in `app/analysis/pipeline.py`.
- `actionable` missing in `69/69` paired docs.
- `market_scope` unknown in `69/69` paired docs.
- `tags` empty in `69/69` paired docs.
- `relevance_score` default-floor in `56/69` paired docs.

§74 status: **closed (frozen diagnostic anchor for PH4G)**

---

<a name="s75-ph4g-fallback-input-enrichment-baseline"></a>

## §75 — PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE

**Sprint**: `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE`
**Phase**: 4
**Opened**: 2026-03-23
**Decision**: D-72
**Status**: active (ready to close)

### Purpose
Narrow, measurement-first fallback-path enrichment. PH4G closes the top-3 field gaps identified in PH4F without broad rule reform. Each enrichment step is preceded by a baseline measurement and followed by MAE re-measurement so changes remain interpretable.

### Scope
- Establish per-field baseline on 69 paired docs (fallback path): measure actionable, market_scope, tags, relevance_score, and priority MAE.
- Apply targeted enrichment to fallback path at exactly `1-3` intervention points in first pass:
  1. `actionable_not_populated` — add conservative actionable heuristic in fallback analysis.
  2. `context_unknown_and_assetless` — improve market_scope/tag context inference for keyword-miss docs.
  3. relevance-related fallback enrichment — reduce default-floor relevance dependence when context evidence exists.
- Re-measure priority MAE after enrichment step to confirm improvement vs 3.13 baseline.
- Defer broader impact/novelty interventions until after PH4G review.

### Non-Goals (hard freeze)
- No changes to scoring formula or priority weights
- No changes to LLM providers or their configuration
- No threshold changes
- No broad rule engine or pipeline refactor
- No changes to more than 3 input fields in a single iteration

### Contract Freeze Record (completed; 2026-03-23)

- Input slice locked: same 69 paired documents used in PH4E/PH4F.
- MAE anchor locked: `3.13` (PH4B, unchanged through PH4F).
- Test baseline anchor: `1538 passed, ruff clean`.
- Frozen constraint: narrow (`1-3` interventions), measurement-first, no scoring formula changes.
- Objective: fallback-path enrichment with policy-safe intervention traceability.

### Freeze Gates (definition-to-execution)

- [x] PH4F formally closed; §74 immutable anchor confirmed
- [x] PH4G activated as next sprint in definition mode
- [x] Scope frozen to exactly `1-3` intervention points for first pass
- [x] Non-goals explicitly frozen in all governance docs
- [x] Input slice and MAE/test anchors locked
- [x] Governance synchronization completed

### Acceptance Gates

- [x] Per-field baseline measurement produced for 69 paired docs (all 5 gap fields)
- [x] Enrichment applied to ≤3 fields with policy-safe rollback on blocked path
- [x] Intervention outcome documented: relevance floor retained; actionable heuristic reverted
- [x] No regressions: `ruff clean`, `1538+ passed`
- [x] PH4H policy-review recommendation documented

### Execution Findings (policy-constrained)

- Relevance-floor fallback intervention was successfully applied.
- Actionable-heuristic intervention was reverted after policy review in-sprint.
- Invariant `I-13` remains active and prevents rule-only priority from exceeding `5`.
- Remaining leverage is policy-governed (`I-13` / actionability), not broad fallback implementation depth.

§75 status: **active (ready to close; next step PH4G_CLOSE_AND_PH4H_POLICY_REVIEW)**

---

<a name="s76-ph4h-rule-only-ceiling-and-actionability-policy-review"></a>

## §76 — PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW

**Sprint**: `PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW`
**Phase**: 4
**Opened**: 2026-03-23
**Decision**: D-70
**Status**: candidate (not active)

### Purpose
Review-only policy sprint. No code changes permitted. PH4G revealed that `I-13` (rule-only priority ceiling ≤ 5) blocks actionability in fallback mode. PH4H decides the canonical policy before any further intervention.

### Scope
- Review policy options for the I-13 ceiling constraint:
  1. Relax I-13: allow rule-only priority > 5 under specific conditions.
  2. Accept actionable as permanently LLM-only: document as architectural constraint.
  3. Hybrid gate: rule-only actionable allowed only with explicit keyword evidence threshold.
- Document policy decision with rationale in DECISION_LOG.md.
- Update I-13 invariant entry in intelligence_architecture.md.

### Non-Goals (hard freeze)
- No code changes of any kind during PH4H
- No I-13 relaxation before policy decision is recorded
- No scoring formula changes
- No provider/source/model expansion
- No new interventions on fallback fields

### Acceptance Criteria
- [ ] Policy options enumerated with risk/benefit evidence
- [ ] One policy option selected with explicit rationale (D-decision recorded)
- [ ] I-13 status updated in governance docs based on decision
- [ ] PH4I (next sprint) defined based on policy outcome
- [ ] Baseline confirmed unchanged: 1538 passed, ruff clean

### Freeze Gates (definition-to-execution)
- [ ] PH4G formally closed; §75 immutable anchor confirmed
- [ ] PH4H activated as next sprint in definition mode
- [ ] Scope frozen to review-only (zero code changes)
- [ ] Policy options enumerated before execution

§76 status: **candidate only (not active; opens after PH4G formal closeout)**

---

