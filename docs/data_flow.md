# Data Flow Architecture

## Purpose
This document defines the exact end-to-end data flow of the system.

It ensures that all agents (Codex, Claude Code, Antigravity) work on the same pipeline
without ambiguity or architectural drift.

The guiding principles:
- Simple but Powerful
- Security First

---

## High-Level Flow

```
Source → Ingestion → Persistence → Normalization → Analysis → Scoring → Storage → Downstream
```

Downstream: Alerts / Research / Signals

---

## Step-by-Step Flow

### 1. Source Layer

Sources include:
- RSS feeds
- websites
- news APIs
- YouTube channels
- podcasts
- social APIs (future)

Each source must be classified before use.

Output:
- Source metadata
- Source type
- Fetch configuration

---

### 2. Ingestion Layer

Responsible for:
- fetching raw data
- handling retries and timeouts
- returning structured fetch results

Output object:
- `FetchResult`

Constraints:
- no persistence
- no analysis
- no deduplication (only minimal metadata)

---

### 3. Persistence Layer

Responsible for:
- converting FetchResult into CanonicalDocument
- storing documents
- assigning lifecycle status

Output object:
- `CanonicalDocument`

Document lifecycle starts here:
- pending
- persisted

Security:
- validate inputs
- normalize URLs
- enforce field limits

---

### 4. Normalization Layer

Responsible for:
- cleaning text
- normalizing metadata
- extracting base fields

Examples:
- remove HTML
- normalize timestamps
- unify language codes

---

### 5. Deduplication Layer

Responsible for:
- identifying duplicate content

Methods:
- normalized URL comparison
- content hash
- title similarity

Output:
- duplicate flag OR canonical merge decision

Lifecycle update:
- duplicate → skip analysis or merge

---

### 6. Analysis Layer

Triggered via:
- CLI (`analyze-pending`)
- scheduler (future)

Responsible for:
- processing pending documents
- generating structured analysis

Input:
- CanonicalDocument

Output:
- `AnalysisResult`

---

### 7. Scoring Layer

Part of analysis pipeline.

Responsible for:
- computing:
  - relevance
  - impact
  - novelty
  - confidence

Scoring must:
- be deterministic where possible
- be reproducible
- not depend on external side effects

---

### 8. Storage of Analysis

Responsible for:
- storing AnalysisResult scores on the document
- linking analysis back to document
- updating lifecycle state

Lifecycle:
- analyzed
- failed

Implementation note:
- `AnalysisResult` is in-memory only — no separate table
- Scores are denormalized back to `canonical_documents` via `update_analysis()`
- This is intentional: simple queries, no joins needed

---

### 9. Downstream Layers

After analysis completes, the pipeline dispatches to:

**Alerting** (`app/alerts/`) — implemented (Sprint 3)
- `AlertService.process_document(doc, result, spam_probability)` is called from `run_rss_pipeline()`
- Gate: `ThresholdEngine.should_alert()` — only documents above `min_priority` are dispatched
- Channels: Telegram (httpx async) + Email (smtplib via executor)
- Dry-run mode: always active by default (`ALERT_DRY_RUN=true`)

**Research generation** — planned (Sprint 4)
**Signal candidates** — planned (Sprint 4)

---

## Document Lifecycle

Every document MUST follow explicit states:

```
pending → persisted → analyzed
         ↘ failed
         ↘ duplicate
```

Rules:
- no silent transitions
- no implicit success
- failures must be logged and traceable

Implementation: `DocumentStatus` enum in `app/core/enums.py`

| Status | Meaning | Set by |
|---|---|---|
| `pending` | in-memory, not yet in DB | `prepare_ingested_document()` |
| `persisted` | saved to DB, waiting for analysis | `DocumentRepository.save()` |
| `analyzed` | analysis complete, scores written | `DocumentRepository.update_analysis()` |
| `failed` | non-recoverable error, kept for audit | ingest or analysis error handler |
| `duplicate` | blocked at dedup gate | `persist_fetch_result()` |

---

## Failure Handling

At each step:
- failure must not crash pipeline
- failure must be isolated
- status must reflect failure

Examples:
- fetch fails → no document created
- persist fails → retry or mark failed
- analysis fails → mark failed, keep document

---

## Idempotency

All steps must be idempotent:
- same RSS item should not create duplicates
- same analysis should not create inconsistent states

---

## Observability

Each step should log:
- input
- output
- errors
- timing

---

## Key Rule

The pipeline must always be:

- deterministic
- traceable
- testable

If any step breaks these rules, it must be refactored.

---

## Module Ownership

| Layer | Module | Owner |
|---|---|---|
| Ingestion | `app/ingestion/` | Codex |
| Persistence | `app/storage/document_ingest.py` | Claude Code |
| Normalization | `app/normalization/` | Codex |
| Deduplication | `app/enrichment/deduplication/` | Codex |
| Analysis | `app/analysis/` | Claude Code |
| LLM Provider | `app/integrations/` | Claude Code |
| Storage | `app/storage/repositories/` | Claude Code |
| Pipeline orchestration | `app/pipeline/service.py` | Claude Code |
| CLI / Scheduling | `app/cli/` | Antigravity |
| Downstream (Alerts) | `app/alerts/` | Claude Code |
