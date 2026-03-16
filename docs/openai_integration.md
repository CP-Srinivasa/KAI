# OpenAI Integration

## Architecture

The OpenAI integration follows a strict layered design:

```
app/analysis/llm/base.py          ← Interface + output schema (no OpenAI import)
app/integrations/openai/provider.py ← Transport layer (OpenAI SDK, retries, cost)
```

**The business logic is in `base.py`. The OpenAI-specific code is only in `provider.py`.**
Swapping to Anthropic or a local model only requires a new provider class.

## Configuration

Set in `.env`:
```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o           # or gpt-4o-mini for cost savings
OPENAI_COST_LIMIT_USD=10.0    # Daily spend limit (hard stop at this amount)
OPENAI_TIMEOUT_SECONDS=60
OPENAI_MAX_RETRIES=3
```

## Structured Output

All analysis calls use `response_format={"type": "json_object"}` — OpenAI returns a JSON
string which is then validated against `LLMAnalysisOutput` (Pydantic schema).

If the JSON is invalid or missing required fields, `LLMOutputValidationError` is raised
and the document is marked `analysis_status=failed`. It will not be retried automatically.

## Cost Control

Cost is tracked per call using a hardcoded price table (`_COST_TABLE` in `provider.py`):

| Model          | Input (per 1K tokens) | Output (per 1K tokens) |
|----------------|-----------------------|------------------------|
| gpt-4o         | $0.0025               | $0.010                 |
| gpt-4o-mini    | $0.00015              | $0.0006                |
| gpt-4-turbo    | $0.010                | $0.030                 |
| gpt-3.5-turbo  | $0.0005               | $0.0015                |

`_check_cost_limit()` is called before every API call. If the daily limit is exceeded,
`LLMCostLimitError` is raised — the document falls back to rule-based analysis.

## Analysis Pipeline Gate

The `AnalysisRunner` only sends documents to OpenAI if:
1. `OPENAI_API_KEY` is configured
2. The document's keyword match score ≥ `min_llm_score` (default: 0.10)
3. Daily cost limit not exceeded

Documents below the threshold get rule-based analysis only.

## Adding a New LLM Provider

1. Create `app/integrations/<provider>/provider.py`
2. Extend `BaseAnalysisProvider` from `app/analysis/llm/base.py`
3. Implement: `analyze_document()`, `summarize_document()`, `compare_to_historical()`, `healthcheck()`
4. Return `AnalysisResult` (from `app/core/domain/document.py`) — same schema for all providers
5. Inject into `AnalysisRunner(llm_provider=YourProvider(...))`

## Output Schema (`LLMAnalysisOutput`)

All providers must return data that validates against this schema:

```python
class LLMAnalysisOutput(BaseModel):
    sentiment_label: SentimentLabel         # positive / neutral / negative
    sentiment_score: float                  # [-1.0, 1.0]
    relevance_score: float                  # [0.0, 1.0]
    impact_score: float                     # [0.0, 1.0]
    confidence_score: float                 # [0.0, 1.0]
    novelty_score: float                    # [0.0, 1.0]
    spam_probability: float                 # [0.0, 1.0]
    market_scope: MarketScope               # crypto / equities / macro / mixed
    affected_assets: list[str]              # ["BTC", "ETH", ...]
    affected_sectors: list[str]             # ["DeFi", "Banking", ...]
    event_type: EventType                   # regulatory / earnings / hack / ...
    bull_case: str
    bear_case: str
    neutral_case: str
    historical_analogs: list[str]
    recommended_priority: DocumentPriority  # critical / high / medium / low / noise
    actionable: bool
    tags: list[str]
    explanation_short: str                  # min 10 chars, non-empty
    explanation_long: str
```
