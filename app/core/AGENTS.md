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
| `domain/document.py` | `CanonicalDocument`, `AnalysisResult`, `EntityMention` | Core domain models |

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

## Tests

```bash
pytest tests/unit/test_settings.py
pytest tests/unit/test_models.py
```
