# AGENTS.md — app/storage/

## Purpose
Persistence layer: DB session, ORM models, Pydantic schemas, repositories.
No business logic. No HTTP. No LLM calls.

## Public Interface

| File | Exports | Notes |
|---|---|---|
| `db/session.py` | `Base`, `build_engine()`, `build_session_factory()`, `get_session()` | Async SQLAlchemy |
| `models/source.py` | `SourceModel` | ORM model — `sources` table |
| `schemas/source.py` | `SourceCreate`, `SourceUpdate`, `SourceRead` | Pydantic v2 |
| `repositories/source_repo.py` | `SourceRepository` | CRUD: create, get_by_id, get_by_url, list, update, delete |
| `migrations/env.py` | Alembic env | Async migration runner |
| `migrations/versions/0001_*.py` | First migration | Creates `sources` table |

## sources Table Schema

| Column | Type | Notes |
|---|---|---|
| `source_id` | String(36) UUID | Primary key |
| `source_type` | String(50) | `SourceType` enum value |
| `provider` | String(100) | e.g. "newsdata", "rss", "youtube" |
| `status` | String(50) | `SourceStatus` enum value |
| `auth_mode` | String(50) | `AuthMode` enum value |
| `original_url` | Text UNIQUE | Raw URL as provided |
| `normalized_url` | Text nullable | Resolved/cleaned URL |
| `notes` | Text nullable | Free text |
| `created_at` | DateTime(tz) | Auto-set on insert |
| `updated_at` | DateTime(tz) | Auto-set on insert + update |

## Constraints

- No business logic in repositories — only DB operations
- All repository methods take/return Pydantic schemas (not raw ORM models)
- Enum values stored as strings in DB — cast on read via `SourceRead.model_validate()`
- Always use `flush()` not `commit()` — commit happens at session boundary (FastAPI lifespan)
- `original_url` is unique — duplicate URL raises `StorageError`

## Adding new models

1. Create `app/storage/models/<name>.py` extending `Base`
2. Create `app/storage/schemas/<name>.py` with Create/Update/Read
3. Create `app/storage/repositories/<name>_repo.py`
4. Add Alembic migration in `migrations/versions/`
5. Import model in `migrations/env.py`
6. Update this AGENTS.md

## Migrations

```bash
# Apply migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# Rollback one step
alembic downgrade -1
```

## Tests

```bash
pytest tests/unit/test_source_registry.py -v
```
