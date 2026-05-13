# AGENTS.md — app/analysis/

## Purpose
Analysis core — keyword matching, entity extraction, Query DSL filtering, and LLM provider orchestration.
Input: `CanonicalDocument`. Output: `AnalysisResult` / `PipelineResult`.
No storage calls. No HTTP (transport isolated in `app/integrations/`). Stateless after construction.

## Public Interface

| File | Exports | Notes |
|---|---|---|
| `base/interfaces.py` | `LLMAnalysisOutput`, `BaseAnalysisProvider` | ABC + structured output schema |
| `keywords/engine.py` | `KeywordEngine`, `KeywordHit` | Multi-layer text matcher |
| `keywords/watchlist.py` | `WatchlistEntry`, `load_watchlist` | Loads monitor/watchlists.yml |
| `keywords/aliases.py` | `EntityAlias`, `load_entity_aliases` | Loads monitor/entity_aliases.yml |
| `query/executor.py` | `QueryExecutor` | In-memory QuerySpec filter |
| `pipeline.py` | `AnalysisPipeline`, `PipelineResult` | Full analysis orchestrator |

## Provider Pattern

Every AI provider (OpenAI, Anthropic, Antigravity) must:
1. Extend `BaseAnalysisProvider`
2. Implement `provider_name: str` and `model: str | None` properties
3. Implement `async analyze(title, text, context) -> LLMAnalysisOutput`
4. Live in `app/integrations/<provider>/provider.py`
5. Never be called directly from business logic — only through `AnalysisPipeline`

Current providers:
- `app/integrations/openai/provider.py` — `OpenAIAnalysisProvider` (gpt-4o, structured outputs)

## KeywordEngine

- Loaded from `monitor/keywords.txt`, `monitor/watchlists.yml`, `monitor/entity_aliases.yml`
- Match priority (highest → lowest): watchlist entry → entity alias → plain keyword
- `match(text)` → `list[KeywordHit]` (canonical, category, occurrences)
- `match_tickers(text)` → `list[str]` (crypto/equity/etf symbols only)
- Thread-safe after construction (read-only index)

## AnalysisPipeline Stages

1. `KeywordEngine.match(title + text)` → `list[KeywordHit]`
2. `hits_to_entity_mentions(hits)` → `list[EntityMention]`
3. `provider.analyze(title, text, context)` → `LLMAnalysisOutput` (optional, skip if no provider)
4. Assemble `AnalysisResult` (document_id linkage + provider metadata)

`run_batch()` uses `asyncio.Semaphore(5)` for bounded concurrency.

## RuleAnalyzer — Fallback Path (Sprint 4C)

`RuleAnalyzer` in `app/analysis/rules/rule_analyzer.py` produces a valid `AnalysisResult`
using keyword matching and heuristics — no LLM, no external calls.

**Reliable**: `relevance_score`, `market_scope`, `affected_assets`, `tags`, `confidence_score=1.0`
**Conservative**: `sentiment_label=NEUTRAL`, `impact_score=0.0`, `novelty_score=0.5`, `actionable=False`

**Sprint 4C gap**: `apply_to_document()` currently requires `llm_output` to be present.
Rule-only results are NOT written back to the document or DB until Sprint 4C fixes this.

**Priority ceiling**: Rule-only analysis produces `priority_score` ≤ ~5 by design.
Documents analyzed only by `RuleAnalyzer` will never produce `SignalCandidate` objects
(threshold is `priority >= 8`). See `docs/contracts.md §12`.

Sprint 4C tasks (for Codex):
1. Relax `apply_to_document()` guard — allow `analysis_result` without `llm_output`
2. Guard `analyze_pending` against `analysis_result=None` — mark FAILED, not silently pass None
3. Add tests: `test_pipeline_fallback.py` — rule-only path, no scores lost

## LLMAnalysisOutput — score ranges

| Field | Range | Default |
|---|---|---|
| `sentiment_score` | -1.0 .. 1.0 | — |
| `relevance_score` | 0.0 .. 1.0 | — |
| `impact_score` | 0.0 .. 1.0 | — |
| `confidence_score` | 0.0 .. 1.0 | — |
| `novelty_score` | 0.0 .. 1.0 | — |
| `spam_probability` | 0.0 .. 1.0 | — |
| `recommended_priority` | 1 .. 10 | 5 |

## Constraints

- No DB imports
- No HTTP in analysis logic (provider transport lives in `app/integrations/`)
- All output models are Pydantic v2, all scores validated
- Prompts versioned in `app/integrations/openai/prompts.py`

## Tests

```bash
pytest tests/unit/test_keyword_engine.py
pytest tests/unit/test_query_executor.py
pytest tests/unit/test_openai_provider.py
pytest tests/unit/test_analysis_pipeline.py
```
