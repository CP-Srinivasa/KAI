# AGENTS.md — app/analysis/

## Purpose
All analysis logic: rule-based and LLM-based.
Input: `CanonicalDocument`. Output: `AnalysisResult`.
No ingestion, no storage, no alerting.

## Public Interface

| File | Exports | Notes |
|---|---|---|
| `base/interfaces.py` | `BaseAnalysisProvider`, `LLMAnalysisOutput` | Extend for every LLM provider |

## Provider Pattern

Every AI provider (OpenAI, Anthropic, Antigravity) must:
1. Extend `BaseAnalysisProvider`
2. Accept `CanonicalDocument` as input
3. Return `LLMAnalysisOutput` (validated Pydantic model)
4. Live in `app/integrations/<provider>/provider.py`
5. Never be called directly from business logic — only via the base interface

## Planned modules (Phase 3)

| Module | Purpose |
|---|---|
| `keywords/matcher.py` | Rule-based keyword matching against `monitor/keywords.txt` |
| `scoring/ranker.py` | Recency + keyword + engagement scoring |
| `llm/pipeline.py` | Orchestrates provider calls with retry + validation |
| `sentiment/` | Sentiment post-processing |

## Constraints

- No HTTP in analysis logic — providers handle transport
- No DB calls — analysis is stateless
- All LLM output validated against `LLMAnalysisOutput` schema
- Prompts versioned in `config/prompts/`
- Log provider name + model + latency for every LLM call

## Tests

```bash
pytest tests/unit/test_models.py   # covers LLMAnalysisOutput
```
