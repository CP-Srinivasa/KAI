# AGENTS.md — app/api/

## Purpose
FastAPI HTTP layer. Thin routers only.
No business logic in routers — delegate to service/core modules.

## Public Interface

| File | Route prefix | Status |
|---|---|---|
| `routers/health.py` | `/health` | ✅ active |
| `routers/sources.py` | `/sources` | ✅ active |
| `routers/query.py` | `/query` | ✅ active |
| `main.py` | App factory + lifespan | ✅ active |

## Planned routers (later phases)

| Router | Phase |
|---|---|
| `/documents` | Phase 2 |
| `/alerts` | Phase 4 |
| `/watchlists` | Phase 5 |
| `/analysis` | Phase 3 |

## Constraints

- Routers must not contain business logic
- All request/response models must be Pydantic
- OpenAPI schema auto-generated — keep models clean and documented
- Use FastAPI lifespan for startup/shutdown (not deprecated `on_event`)
- No direct DB calls in routers — use repository pattern (Phase 2+)

## OpenAPI

Auto-generated at `/docs` (Swagger) and `/redoc`.
This is the machine-readable API contract for all agents.

## Tests

```bash
pytest tests/unit/test_health.py
```
