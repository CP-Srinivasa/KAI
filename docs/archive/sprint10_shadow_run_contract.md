# Sprint 10 Contract — Companion Shadow Run

> **Canonical reference** for Sprint-10 companion shadow run under real conditions,
> audit-only divergence capture, and offline divergence reporting.
>
> Upstream contracts: `docs/contracts.md §21`, invariants I-51–I-55.
> Upstream Sprint-9: `docs/sprint9_promotion_audit_contract.md`.

---

## Purpose

Sprint 10 introduces the **companion shadow run** — a mechanism to execute companion inference
in parallel with the primary provider on real, already-analyzed documents, without influencing
any production analysis path.

The shadow run is **purely auditing**:
- Primary analysis results are never overridden.
- The offline shadow path does not write to `canonical_documents`.
- Live shadow may attach audit metadata, but it never owns primary persistence or routing.
- Companion is called explicitly, regardless of `APP_LLM_PROVIDER` setting.
- Output goes to standalone audit artifacts only.

---

## Core Separation — Non-Negotiable

| Concept | What it is | What it is NOT |
|---------|-----------|----------------|
| **Primary analysis** | Provider result in DB — owner of `canonical_documents` scores | Not shadow input |
| **Shadow companion result** | Companion output on the same document — audit only | Not a pipeline result |
| **Divergence summary** | Computed diff between primary and companion — informational | Not a gate, not a routing signal |
| **Shadow JSONL** | Standalone audit artifact, one record per line | Not evaluation report, not training data |

---

## Sprint-10 Scope

### What Sprint 10 delivers

1. **New module `app/research/shadow.py`**:
   - `ShadowRunRecord` (dataclass) — one per document
   - `DivergenceSummary` (dataclass) — computed diff
   - `compute_divergence(doc, companion_output) -> DivergenceSummary`
   - `write_shadow_record(record, path) -> None` — appends one JSON line
   - `run_shadow_batch(documents, companion, output_path) -> list[ShadowRunRecord]`

2. **CLI: `research shadow-run [--count N] [--output PATH]`**:
   - Loads last N analyzed documents from DB (default: 20, max: 100)
   - Requires `companion_model_endpoint` to be set (exits with message if missing)
   - Calls `InternalCompanionProvider.analyze()` for each document
   - Writes one `ShadowRunRecord` per document to output JSONL
   - Never touches `apply_to_document()`, `repo.update_analysis()`, or any score field

3. **CLI: `research shadow-report <path>`**:
   - Reads shadow JSONL
   - Prints per-document divergence table (document_id, primary_provider, sentiment_match,
     priority_diff, relevance_diff, impact_diff, actionable_match, tag_overlap)
   - Prints aggregate summary: avg_priority_diff, avg_relevance_diff, sentiment_agreement_rate,
     actionable_agreement_rate, total_records

4. **Tests `tests/unit/test_shadow.py`** — unit tests for shadow module (no DB, no HTTP)

5. **CLI tests in `tests/unit/test_cli.py`** — shadow-run + shadow-report CLI tests (mocked)

6. **This contract document** + `docs/contracts.md §21` + I-51–I-55 +
   `docs/intelligence_architecture.md` Sprint-10 update

### What Sprint 10 does NOT deliver

- No second production pipeline and no shadow-owned mutation path
- No new analysis tier, no new provider, no factory change
- No DB migration, no new DB tables or columns
- No routing change — `APP_LLM_PROVIDER` is never modified
- No shadow result enters any research output (brief, signals, watchlists)
- No trading execution
- No training or weight modification

---

## Data Model

### ShadowRunRecord

```python
@dataclass
class ShadowRunRecord:
    document_id: str              # str(CanonicalDocument.id)
    run_at: str                   # ISO 8601 datetime (UTC)
    primary_provider: str         # doc.provider or "unknown"
    primary_analysis_source: str  # doc.analysis_source or "unknown"
    companion_endpoint: str       # endpoint used (for traceability)
    companion_model: str          # model name (for traceability)
    primary_result: dict          # snapshot of DB-stored scores (see below)
    companion_result: dict        # snapshot of LLMAnalysisOutput fields (see below)
    divergence: dict              # DivergenceSummary.to_dict()
```

**`primary_result` fields** (sourced from `CanonicalDocument` stored scores):
```python
{
    "sentiment_label": str,         # doc.sentiment_label (stored value)
    "sentiment_score": float,       # doc.sentiment_score
    "relevance_score": float,       # doc.relevance_score
    "impact_score": float,          # doc.impact_score
    "actionable": bool,             # doc.actionable
    "priority_score": int,          # doc.priority_score
    "tags": list[str],              # doc.tags
}
```

**`companion_result` fields** (sourced from `LLMAnalysisOutput`):
```python
{
    "sentiment_label": str,         # output.sentiment_label
    "sentiment_score": float,       # output.sentiment_score
    "relevance_score": float,       # output.relevance_score
    "impact_score": float,          # output.impact_score (already capped at 0.8 by I-17)
    "actionable": bool,             # output.actionable
    "recommended_priority": int,    # output.recommended_priority
    "tags": list[str],              # output.tags
}
```

### DivergenceSummary

```python
@dataclass
class DivergenceSummary:
    sentiment_match: bool     # primary.sentiment_label == companion.sentiment_label
    priority_diff: int        # abs(primary.priority_score - companion.recommended_priority)
    relevance_diff: float     # abs(primary.relevance_score - companion.relevance_score)
    impact_diff: float        # abs(primary.impact_score - companion.impact_score)
    actionable_match: bool    # primary.actionable == companion.actionable
    tag_overlap: float        # Jaccard(set(primary.tags), set(companion.tags)) — 0.0 if both empty
```

---

## Storage Format

**JSONL** — one JSON object per line, UTF-8, no trailing newline required.

```jsonl
{"document_id": "...", "run_at": "2026-03-19T12:00:00Z", "primary_provider": "openai", ...}
{"document_id": "...", "run_at": "2026-03-19T12:00:01Z", "primary_provider": "openai", ...}
```

Rules:
- Each line is a self-contained `ShadowRunRecord` serialized as JSON.
- File is append-only — multiple runs may append to the same file.
- No schema version field required in Sprint 10 (file is for operator review, not machine parsing).
- Default output path: `shadow_run_<YYYYMMDD_HHMMSS>.jsonl` in the current working directory.

---

## CLI Specification

### `research shadow-run`

```
kai research shadow-run [--count N] [--output PATH]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--count` | 20 | Number of most-recent analyzed documents to process (max 100) |
| `--output` | `shadow_run_<timestamp>.jsonl` | Output JSONL file path |

**Behavior:**
1. Check `companion_model_endpoint` from settings — if None, print info message and exit 0.
2. Load `--count` most recent documents with `status=ANALYZED` from DB.
3. For each document:
   a. Call `InternalCompanionProvider.analyze(doc.title, doc.raw_text or "")`.
   b. On companion error: log warning, write record with `companion_result=null` and
      `divergence=null`, continue.
   c. On success: compute divergence, write `ShadowRunRecord`.
4. Print summary: N processed, M errors, output path.
5. Exit 0 always (shadow run failure is non-fatal).

**DB access:**
- Requires async DB session — uses existing `DocumentRepository`.
- Needs `get_recent_analyzed(limit)` method (new on `DocumentRepository`, Sprint-10 task 10.1).

**Does NOT:**
- Call `apply_to_document()`.
- Call `repo.update_analysis()`.
- Modify any document score or status.
- Use `APP_LLM_PROVIDER` for routing — always calls companion explicitly.

### `research shadow-report`

```
kai research shadow-report <path>
```

**Behavior:**
1. Read JSONL file at `<path>`.
2. Print per-document table:
   - Columns: `document_id`, `primary_provider`, `sentiment_match`, `priority_diff`,
     `relevance_diff`, `impact_diff`, `actionable_match`, `tag_overlap`
3. Print aggregate summary:
   - `total_records`, `error_records` (companion_result=null), `sentiment_agreement_rate`,
     `actionable_agreement_rate`, `avg_priority_diff`, `avg_relevance_diff`, `avg_impact_diff`
4. Exit 0 always.

---

## Repository Extension

**`DocumentRepository.get_recent_analyzed(limit: int) -> list[CanonicalDocument]`**

Returns the `limit` most recent documents with `status=ANALYZED`, ordered by `fetched_at` DESC.

```python
async def get_recent_analyzed(self, limit: int = 20) -> list[CanonicalDocument]:
    """Return the most recently analyzed documents for shadow run input."""
    ...
```

This is the only new public method on `DocumentRepository`. No schema change.

---

## Invariants (I-51 through I-57)

| ID | Rule |
|----|------|
| I-51 | Offline shadow run (`shadow-run` CLI) MUST NEVER call `apply_to_document()` or `repo.update_analysis()`. Zero DB writes to `canonical_documents` from the offline path. |
| I-52 | Shadow run calls `InternalCompanionProvider.analyze()` directly and explicitly — independent of `APP_LLM_PROVIDER`. Even if `APP_LLM_PROVIDER=companion`, shadow run is a separate, explicit audit call. |
| I-53 | Shadow JSONL is a standalone audit artifact. It MUST NOT be used as evaluation report input, training teacher data, or promotion gate input. |
| I-54 | Shadow run requires `companion_model_endpoint` to be configured. If the setting is absent, the command exits 0 with an informational message — not an error. |
| I-55 | Divergence summary is informational only. It MUST NOT be used for routing decisions, promotion gating, alert filtering, or research output modification. |
| I-56 | Live shadow (inline `--shadow` flag): Shadow provider runs **concurrent** to Primary inside `AnalysisPipeline.run()`. Primary await is never delayed — both tasks launched as `asyncio.create_task()`. Shadow exception is caught non-blocking; `shadow_error` is set on `PipelineResult`, primary proceeds. |
| I-57 | Live shadow persistence fix: `update_analysis()` receives `metadata_updates=res.document.metadata` (after `apply_to_document()`) — NOT `res.trace_metadata`. This guarantees that `shadow_analysis` and `shadow_provider` written by `apply_to_document()` reach the DB. Both `run_rss_pipeline()` and `analyze-pending` enforce this. |

---

## Five Explicit Separations

| Layer | What it is | What it is NOT |
|-------|-----------|----------------|
| **Primary analysis** | `AnalysisPipeline` → `apply_to_document()` → DB | Not shadow |
| **Shadow companion result** | `InternalCompanionProvider.analyze()` → JSONL only | Not pipeline result |
| **Divergence summary** | Computed diff, informational | Not a gate, not a signal |
| **Shadow JSONL** | Standalone audit file | Not EvaluationReport, not training corpus |
| **Shadow report CLI** | Offline reader for operator review | Not a promotion gate |

---

## Security Notes

- Companion endpoint is already localhost-validated by `companion_model_endpoint` settings
  validator (I-15). Shadow run inherits this constraint.
- Shadow JSONL contains document titles and analysis scores — no raw article content stored.
- Shadow run exits 0 on companion errors — no crash, no secret leakage in error output.
- No new authentication surface. No new external network calls.

---

## Sprint-10 Tasks

| # | Task | Agent | Status |
|---|---|---|---|
| 10.1 | `app/research/shadow.py`: `ShadowRunRecord`, `DivergenceSummary`, `compute_divergence()`, `write_shadow_record()`, `run_shadow_batch()` + `DocumentRepository.get_recent_analyzed()` + `tests/unit/test_shadow.py` | Claude Code | ✅ |
| 10.2 | CLI: `research shadow-run` + `research shadow-report-file` + `tests/unit/test_cli.py` shadow tests | Claude Code | ✅ |
| 10.3 | `docs/sprint10_shadow_run_contract.md` + `contracts.md §21` + I-51–I-55 | Claude Code | ✅ |
| 10.4 | `docs/intelligence_architecture.md` Sprint-10 update + `AGENTS.md` + `TASKLIST.md` | Claude Code | ✅ |

---

## Acceptance Criteria

```
Sprint 10 gilt als abgeschlossen wenn:
  - [x] 10.1: app/research/shadow.py vollständig + tests/unit/test_shadow.py grün (8 Tests)
  - [x] 10.2: shadow-run + shadow-report-file CLI vollständig + CLI-Tests grün (5 Tests)
  - [x] 10.3: sprint10_shadow_run_contract.md + contracts.md §21 + I-51–I-55 vollständig
  - [x] 10.4: intelligence_architecture.md + AGENTS.md + TASKLIST.md aktualisiert
  - [x] ruff check . sauber
  - [x] pytest passing (625 Tests, kein Rückschritt)
  - [x] shadow-run schreibt JSONL ohne DB-Writes zu canonical_documents
  - [x] shadow-report-file liest JSONL und zeigt Divergenztabelle + Aggregat
  - [x] Kein Einfluss auf primary analysis pipeline, research outputs, oder alert-Pfade
```

---

## Codex-Spec für 10.1 — app/research/shadow.py + Repository + Tests

```
Modul: app/research/shadow.py (NEU), app/storage/repositories/document_repo.py (ERWEITERN)
Testmodul: tests/unit/test_shadow.py (NEU)

Änderungen:

1. app/research/shadow.py:
   - @dataclass DivergenceSummary (fields: sentiment_match, priority_diff, relevance_diff,
     impact_diff, actionable_match, tag_overlap)
   - @dataclass ShadowRunRecord (fields: document_id, run_at, primary_provider,
     primary_analysis_source, companion_endpoint, companion_model, primary_result,
     companion_result, divergence)
   - compute_divergence(doc: CanonicalDocument, companion_output: LLMAnalysisOutput)
     -> DivergenceSummary
   - write_shadow_record(record: ShadowRunRecord, path: Path) -> None  (append-only JSONL)
   - run_shadow_batch(documents, companion, output_path) -> list[ShadowRunRecord]

2. app/storage/repositories/document_repo.py:
   + async def get_recent_analyzed(self, limit: int = 20) -> list[CanonicalDocument]
   Query: SELECT ... WHERE status='analyzed' ORDER BY fetched_at DESC LIMIT :limit

Tests (tests/unit/test_shadow.py):
  - test_compute_divergence_identical_results        (all match, all diff=0)
  - test_compute_divergence_full_mismatch            (nothing matches)
  - test_compute_divergence_tag_overlap_partial      (Jaccard = 0.5)
  - test_compute_divergence_both_tags_empty          (tag_overlap = 0.0, no ZeroDivision)
  - test_write_shadow_record_creates_valid_jsonl     (valid JSON per line)
  - test_write_shadow_record_appends_multiple        (two writes = two lines)
  - test_run_shadow_batch_calls_companion_per_doc    (mock companion, 3 docs)
  - test_run_shadow_batch_handles_companion_error    (companion raises, record has null companion)

Constraints:
  - NICHT: pipeline.py, apply_to_document(), update_analysis() ändern
  - NICHT: neue DB-Spalten oder Migrationen
  - NICHT: APP_LLM_PROVIDER auslesen oder verändern
  - companion_result=None bei Fehler (nicht abbrechen)

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_shadow.py grün (8 neue Tests)
  - [ ] pytest tests/unit/ grün (>= 598 Tests)
```

---

## Codex-Spec für 10.2 — CLI shadow-run + shadow-report

```
Modul: app/cli/main.py (ERWEITERN)
Testmodul: tests/unit/test_cli.py (ERWEITERN)

Neue CLI-Commands (research subgroup):

1. research shadow-run:
   Argumente/Optionen:
     --count INT   (default=20, max=100)
     --output PATH (default="shadow_run_<YYYYMMDD_HHMMSS>.jsonl")

   Verhalten:
     a. Lade settings — prüfe companion_model_endpoint
        → falls None: console.print("[dim]Shadow run skipped: companion_model_endpoint not set.[/dim]"); Exit 0
     b. Async DB-Session: repo.get_recent_analyzed(limit=count)
     c. Erstelle InternalCompanionProvider(endpoint, model, timeout)
     d. Für jedes doc: await companion.analyze(doc.title, doc.raw_text or "")
        → Erfolg: compute_divergence + write_shadow_record
        → Fehler: write_shadow_record mit companion_result=None, divergence=None
     e. console.print(f"[green]Shadow run complete:[/green] {n} processed, {m} errors → {output}")
     f. Exit 0

2. research shadow-report:
   Argumente: path: str (Pfad zur JSONL-Datei)

   Verhalten:
     a. Lese JSONL — parse jede Zeile als ShadowRunRecord-Dict
     b. Rich Table: document_id, primary_provider, sentiment_match, priority_diff,
        relevance_diff, impact_diff, actionable_match, tag_overlap
     c. Aggregat-Block: total, errors, sentiment_agreement_rate, actionable_agreement_rate,
        avg_priority_diff, avg_relevance_diff, avg_impact_diff
     d. Exit 0

CLI-Tests (tests/unit/test_cli.py — neu hinzufügen):
  - test_research_shadow_run_skips_when_no_endpoint  (no endpoint → exit 0 + info message)
  - test_research_shadow_run_writes_jsonl             (mock companion + mock repo → JSONL created)
  - test_research_shadow_run_handles_companion_error  (companion raises → record with null, exit 0)
  - test_research_shadow_report_prints_table          (read valid JSONL → table in output)
  - test_research_shadow_report_missing_file_exits    (missing path → exit 1)

Constraints:
  - NICHT: apply_to_document() oder update_analysis() aufrufen
  - NICHT: APP_LLM_PROVIDER für Routing verwenden
  - Shadow run: Exit 0 auch bei Companion-Fehlern

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_cli.py grün
  - [ ] pytest tests/unit/ grün (>= 598 + neue Tests)
  - [ ] shadow-run schreibt korrekte JSONL (verifiziert im Test)
  - [ ] shadow-report zeigt Tabelle + Aggregat
```
