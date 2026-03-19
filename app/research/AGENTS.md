# AGENTS.md - app/research/

> Module-level agent contract for the Research and Signal Generation layer.
> All agents must read this before modifying any file in `app/research/`.

---

## Purpose

`app/research/` is the research output layer of KAI.

It consumes analyzed `CanonicalDocument` objects from storage and produces:
- `ResearchBrief` - aggregated snapshot for a named cluster (watchlist, asset, topic)
- `SignalCandidate` - filtered high-priority research signal for human review
- `WatchlistRegistry` - access layer for `monitor/watchlists.yml`
- JSONL dataset exports for teacher, benchmark, and rule-baseline corpora
- Offline evaluation and benchmark artifacts for companion readiness

This layer is read-only relative to the DB. No document writes. No score mutations.
No direct DB access. All input arrives as `list[CanonicalDocument]`.

---

## Module Files

| File | Responsibility |
|---|---|
| `__init__.py` | Public API - re-exports key classes |
| `watchlists.py` | `WatchlistRegistry` - tag-based watchlist access from `monitor/watchlists.yml` |
| `briefs.py` | `ResearchBrief`, `ResearchBriefBuilder` - cluster-level research snapshot |
| `signals.py` | `SignalCandidate`, `extract_signal_candidates()` - high-priority signals extraction |
| `datasets.py` | `export_training_data()` - JSONL export for teacher, benchmark, and baseline corpora |
| `evaluation.py` | `compare_outputs()`, `compare_datasets()`, `load_jsonl()`, `save_evaluation_report()`, `save_benchmark_artifact()` - offline comparison and benchmark helpers |

---

## Key Contracts

### WatchlistRegistry

- Loaded from `monitor/watchlists.yml` via `WatchlistRegistry.from_monitor_dir(path)`
- Supports four watchlist types: `"assets"`, `"persons"`, `"topics"`, `"sources"`
- `get_watchlist(tag, item_type="assets")` -> `list[str]`
- `get_all_watchlists(item_type="assets")` -> `Mapping[str, list[str]]`
- `filter_documents(documents, tag, item_type="assets")` -> `list[CanonicalDocument]`
- `get_symbols_for_category(category)` -> `list[str]` (assets only)

### ResearchBrief

- Built via `ResearchBriefBuilder(cluster_name).build(documents)`
- Input: `list[CanonicalDocument]`; only `is_analyzed=True` docs are used
- Actionable threshold: `priority_score >= 8`
- Output fields:
  - `cluster_name`, `title`, `summary`
  - `document_count`, `average_priority`, `overall_sentiment`
  - `top_documents`
  - `top_assets`
  - `top_entities`
  - `top_actionable_signals`
  - `key_documents`
- Serialization: `.to_markdown()`, `.to_json_dict()`

### SignalCandidate

- Produced via `extract_signal_candidates(documents, min_priority=8, watchlist_boosts=None)`
- Only documents with `is_analyzed=True` and `effective_priority >= min_priority` are included
- `direction_hint` values are always `"bullish"`, `"bearish"`, or `"neutral"`
- `document_id` is required for traceability

### Dataset and Evaluation Helpers

- `export_training_data(documents, output_path, teacher_only=False)` reuses persisted document fields only
- Teacher eligibility is determined only by `analysis_source=EXTERNAL_LLM`
- `compare_datasets()` matches rows only by `metadata.document_id`
- `save_evaluation_report()` writes a structured JSON report for offline review
- `save_benchmark_artifact()` writes a small manifest for future companion tuning artifacts

---

## Immutable Rules

| Rule | Detail |
|---|---|
| R-1 | No DB writes in this layer |
| R-2 | `apply_to_document()` is the only score mutation point and must not be called here |
| R-3 | `direction_hint` is always `"bullish"`, `"bearish"`, or `"neutral"` |
| R-4 | `SignalCandidate` is a research artifact, never an execution order |
| R-5 | Watchlist boosts are research hints only and never override stored scores |
| R-6 | `WatchlistRegistry` reads `monitor/watchlists.yml`, never a DB table |
| R-7 | `extract_signal_candidates()` filters on `is_analyzed=True` before priority checks |
| R-8 | Teacher-only dataset export uses `analysis_source` only, never `provider` or metadata traces |
| R-9 | Offline evaluation and benchmark helpers must stay network-free and DB-free |

---

## What Agents May Do Here

- Extend serialization formats for research outputs
- Add small lookup helpers in `WatchlistRegistry`
- Extend offline evaluation and benchmark reporting without changing the dataset schema
- Write or extend tests in `tests/unit/test_research_*.py`, `tests/unit/test_datasets.py`, and `tests/unit/test_evaluation.py`

## What Agents Must NOT Do Here

- Write to the DB or call repository write methods
- Mutate document scores or call `apply_to_document()`
- Add provider-specific network logic to this layer
- Use `provider` or `ensemble_chain` as teacher-eligibility criteria
- Introduce a second dataset schema parallel to the existing JSONL contract

---

## Tests

```bash
pytest tests/unit/test_research_signals.py -v
pytest tests/unit/test_research_briefs.py -v
pytest tests/unit/test_research_watchlists.py -v
pytest tests/unit/test_datasets.py -v
pytest tests/unit/test_evaluation.py -v
```
