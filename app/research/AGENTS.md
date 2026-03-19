# AGENTS.md — app/research/

> Module-level agent contract for the Research & Signal Generation layer.
> All agents must read this before modifying any file in `app/research/`.

---

## Purpose

`app/research/` is the **research output layer** of KAI.

It consumes analyzed `CanonicalDocument` objects from the storage layer and produces:
- `ResearchBrief` — aggregated snapshot for a named cluster (watchlist, asset, topic)
- `SignalCandidate` — strictly filtered, high-priority research signal for human review
- `WatchlistRegistry` — access layer for `monitor/watchlists.yml`

**This layer is read-only relative to the DB.** No document writes. No score mutations.
No direct DB access. All input arrives as `list[CanonicalDocument]`.

---

## Module Files

| File | Responsibility |
|---|---|
| `__init__.py` | Public API — re-exports all key classes |
| `watchlists.py` | `WatchlistRegistry` — tag-based watchlist access from `monitor/watchlists.yml` |
| `briefs.py` | `ResearchBrief`, `ResearchBriefBuilder` — cluster-level research snapshot |
| `signals.py` | `SignalCandidate`, `extract_signal_candidates()` — high-priority signals extraction |
| `datasets.py` | `export_training_data()` — JSONL export for companion model fine-tuning (see §14) |
| `evaluation.py` | `compare_outputs()`, `EvaluationResult` — companion vs teacher metric comparison |

---

## Key Contracts

### WatchlistRegistry

- Loaded from `monitor/watchlists.yml` via `WatchlistRegistry.from_monitor_dir(path)`
- Supports four watchlist types: `"assets"`, `"persons"`, `"topics"`, `"sources"`
- `get_watchlist(tag, item_type="assets")` → `list[str]` identifiers
- `get_all_watchlists(item_type="assets")` → `Mapping[str, list[str]]`
- `filter_documents(documents, tag, item_type="assets")` → `list[CanonicalDocument]`
- `get_symbols_for_category(category)` → `list[str]` (assets only)

### ResearchBrief

- Built via `ResearchBriefBuilder(cluster_name).build(documents)`
- Input: `list[CanonicalDocument]` — only `is_analyzed=True` docs are used
- Actionable threshold: `priority_score >= 8` (synced with `ThresholdEngine.min_priority`)
- Output fields:
  - `cluster_name`, `title`, `summary` — cluster identity and auto-generated summary
  - `document_count`, `average_priority`, `overall_sentiment` — aggregate metrics
  - `top_documents` — top 10 by (priority, impact, date)
  - `top_assets` — `list[BriefFacet]` — ranked by frequency across documents (max 5)
  - `top_entities` — `list[BriefFacet]` — ranked by frequency across documents (max 5)
  - `top_actionable_signals` — `list[BriefDocument]` — docs with `priority >= 8` (max 10)
  - `key_documents` — `list[BriefDocument]` — non-actionable docs (max 20)
- `BriefFacet`: `name: str`, `count: int` — simple ranked name/frequency pair
- Serialization: `.to_markdown()`, `.to_json_dict()`

### SignalCandidate

- Produced via `extract_signal_candidates(documents, min_priority=8, watchlist_boosts=None)`
- Only documents with `is_analyzed=True` and `effective_priority >= min_priority` are included
- `direction_hint` values: **`"bullish"` / `"bearish"` / `"neutral"`** — never `"buy"/"sell"/"hold"`
- `confidence` is proxied from `relevance_score` (LLM `confidence_score` is not persisted to DB)
- `document_id` is required — provides traceability back to the source `CanonicalDocument`
- `priority` field: `Field(ge=8, le=10)` — enforced at model level
- Watchlist boosts: `{"BTC": 1}` artificially raises effective priority to clear the threshold

---

## Immutable Rules (for all agents)

| Rule | Detail |
|---|---|
| R-1 | No DB writes in this layer — research output is in-memory only |
| R-2 | `apply_to_document()` is the only score mutation point — not callable here |
| R-3 | `direction_hint` is always `"bullish"`, `"bearish"`, or `"neutral"` |
| R-4 | `SignalCandidate` is a research artifact — never an execution order |
| R-5 | Watchlist boosts are research hints — they never override analysis scores |
| R-6 | `WatchlistRegistry` reads `monitor/watchlists.yml` — never a DB table |
| R-7 | `extract_signal_candidates()` filters on `is_analyzed=True` before priority check |

---

## What Agents May Do Here

- Add new serialization formats to `ResearchBrief` (e.g. `.to_html()`)
- Extend `WatchlistRegistry` with new lookup methods
- Add new fields to `SignalCandidate` for richer research context
- Add `WatchlistType` support for new categories
- Write or extend tests in `tests/unit/test_research_*.py`

## What Agents Must NOT Do Here

- Write to DB or call `repo.*` methods
- Call `apply_to_document()` or mutate `CanonicalDocument` scores
- Use execution language in `direction_hint` (`"buy"`, `"sell"`, `"hold"`)
- Bypass `is_analyzed=True` guard in `extract_signal_candidates()`
- Add provider-specific logic (LLM calls, API calls) into this layer

---

## Sprint 4 Scope

### Sprint 4A — delivered
- [x] `WatchlistRegistry` — full multi-type support (assets, persons, topics, sources), `filter_documents()`, `from_file()`, `save()`
- [x] `ResearchBrief` / `BriefFacet` / `ResearchBriefBuilder` — builder, markdown output, JSON serialization, auto-summary
- [x] `SignalCandidate` — strict model, watchlist boosts, `document_id` traceability
- [x] CLI `research brief`, `research watchlists`, `research signals` (in `app/cli/main.py`)
- [x] `app/research/AGENTS.md` (this file)

### Sprint 4B — pending (Codex / Antigravity)
- [ ] `GET /research/briefs/{cluster}` — API endpoint (`app/api/routers/research.py`)
- [ ] `GET /research/signals` — API endpoint
- [ ] `WatchlistRegistry.find_by_text()` — text-based lookup (planned, not yet implemented)
- [ ] Tests for CLI commands and API endpoints (`tests/unit/test_research_cli.py`, `test_research_api.py`)

---

## Test Commands

```bash
pytest tests/unit/test_research_signals.py -v
pytest tests/unit/test_research_briefs.py -v
pytest tests/unit/test_research_watchlists.py -v
```
