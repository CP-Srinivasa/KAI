# AGENTS.md — app/core/

## Purpose
Foundation layer. Contains everything that all other modules depend on.
No business logic. No provider-specific code. No I/O.

## Public Interface

| File | Exports | Notes |
|---|---|---|
| `settings.py` | `AppSettings`, `DBSettings`, `OpenAISettings`, `AnthropicSettings`, `TelegramSettings` | pydantic-settings, loaded from .env |
| `enums.py` | `SourceType`, `SourceStatus`, `SentimentLabel`, `AlertPriority` | Shared enums — do not duplicate elsewhere |
| `errors.py` | `KAIError`, `IngestionError`, `AnalysisError`, `StorageError` | Exception hierarchy |
| `logging.py` | `get_logger()` | structlog JSON logger |
| `domain/document.py` | `CanonicalDocument`, `AnalysisResult`, `EntityMention`, `YouTubeVideoMeta`, `PodcastEpisodeMeta`, `QuerySpec` | Core domain models |

## Constraints

- No imports from `app.ingestion`, `app.analysis`, `app.api`, etc.
- No database calls
- No HTTP calls
- No LLM calls
- All models must be Pydantic v2

## Adding to core/

Only add here if:
- it is needed by 2+ other modules, AND
- it has no external dependencies (no HTTP, no DB, no LLM)

## Domain Model Details

### CanonicalDocument
- `id: UUID` — auto-generated
- `content_hash: str` — SHA-256 of `url|title|raw_text`, auto-computed via `model_validator`; set manually to override
- `word_count: int` — `@computed_field`, not stored in DB; prefers `cleaned_text` over `raw_text`
- `is_duplicate: bool`, `is_analyzed: bool` — state flags set by pipeline stages
- `market_scope: MarketScope` — defaults to `UNKNOWN`
- `entity_mentions: list[EntityMention]` — structured extraction results

### EntityMention
- `confidence: float` — validated `[0.0, 1.0]`
- `source: str` — `"rule"` | `"llm"` | `"manual"`

### AnalysisResult
- Links to `CanonicalDocument` via `document_id: UUID`
- `recommended_priority: int` — validated `[1, 10]`, default `5`
- `raw_output: dict` — preserved LLM response for replay/debug

## Tests

```bash
pytest tests/unit/test_settings.py
pytest tests/unit/test_models.py
pytest tests/unit/test_canonical_document.py
```
