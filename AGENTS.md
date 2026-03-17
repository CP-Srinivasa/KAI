# AGENTS.md — KAI Platform

> Universal entry point for all coding agents: Claude Code, OpenAI Codex, Google Antigravity.
> Read this before touching any code.

---

## 1. Platform Identity

**Name**: KAI (AI Analyst Trading Bot)
**Repo**: https://github.com/CP-Srinivasa/KAI
**Mission**: Agent-capable development & operations platform for AI-driven market intelligence.
**Motto**: Simple but Powerful

This is **not just an app** — it is a spec-driven, modular, machine-readable platform designed
to be developed and operated by both humans and AI agents.

---

## 2. Provider Decisions (fixed)

| # | Concern | Decision |
|---|---|---|
| A | Repo | GitHub |
| B | Language | Python 3.12+ |
| C | API Framework | FastAPI |
| D | Database | PostgreSQL (TimescaleDB optional later) |
| E | AI Providers | OpenAI/ChatGPT (primary), Anthropic (optional), Google Antigravity (optional) |

---

## 3. Module Map

```
app/
  core/          → settings, logging, domain types, enums, errors
  ingestion/     → source adapters, resolvers, classifiers, RSS
  normalization/ → content cleaning, canonical alignment
  enrichment/    → deduplication, entity helpers
  analysis/      → base interfaces for LLM + rule-based providers
  api/           → FastAPI routers (health, sources, query)
  cli/           → Typer commands
  storage/       → DB models, session (PostgreSQL/SQLAlchemy)
monitor/         → user-editable source lists, keywords, watchlists
tests/unit/      → pytest unit tests
docs/            → architecture and module documentation
```

Each module has its own `AGENTS.md` with: purpose, public interface, constraints, test commands.

---

## 4. Architecture Rules (non-negotiable)

- **Pydantic everywhere** — all inputs/outputs typed via Pydantic models, no bare `dict` or `Any`
- **Provider abstraction** — LLM providers extend `BaseAnalysisProvider`, never called directly from business logic
- **Adapter pattern** — source adapters extend `BaseSourceAdapter`
- **No secrets in code** — all config via `pydantic-settings` + `.env`
- **Classify before ingest** — source type must be resolved before fetching
- **Structured LLM output** — all LLM responses validated against schema
- **No silent drift** — if you change an interface, update its AGENTS.md

---

## 5. Current Phase

**Phase 1 — Foundation** ✅ complete
**Phase 2 — Ingestion Core** 🔄 next

Phase 2 scope:
- RSS scheduling (APScheduler)
- News API adapter (newsdata.io)
- Source registry (DB-backed)
- Dedup pipeline (DB-aware)
- DB session + migrations (Alembic)

---

## 6. Agent Role Model

**→ See [AGENT_ROLES.md](./AGENT_ROLES.md) for the full binding operations model.**

| Agent | Role |
|---|---|
| **OpenAI Codex** | Implementer — code, tests, refactoring, CI fixes |
| **Claude Code** | Architect — modules, interfaces, specs, multi-file changes |
| **Google Antigravity** | Orchestrator — workflows, MCP/Skills, build/deploy |

---

## 7. Agent Collaboration Protocol

When working in this repo, any agent MUST:

1. **Read the module's `AGENTS.md`** before writing code in that module
2. **Reuse existing domain models** — `CanonicalDocument`, `SourceMetadata`, etc.
3. **Follow existing naming** — do not rename, reorganize, or restructure without explicit instruction
4. **Write tests** for all non-trivial logic (pytest, place in `tests/unit/`)
5. **Report after changes**:
   - Files created
   - Files modified
   - Assumptions made
   - TODOs left
   - Test command to verify

---

## 8. Key Domain Models

| Model | Location | Purpose |
|---|---|---|
| `CanonicalDocument` | `app/core/domain/document.py` | Normalized content unit |
| `AnalysisResult` | `app/core/domain/document.py` | LLM output container |
| `SourceMetadata` | `app/ingestion/base/interfaces.py` | Source descriptor |
| `FetchResult` | `app/ingestion/base/interfaces.py` | Adapter fetch output |
| `AppSettings` | `app/core/settings.py` | All config aggregated |

---

## 9. Test & Quality Commands

```bash
# Run all tests
pytest

# Lint
ruff check .

# Type check (optional)
mypy app/

# Run API (dev)
uvicorn app.api.main:app --reload

# Run CLI
python -m app.cli.main --help
```

---

## 10. What "Done" Means

A task is complete when:
- [ ] Code exists and is typed
- [ ] Tests exist and pass
- [ ] `ruff check` passes
- [ ] Module `AGENTS.md` updated if interface changed
- [ ] No hardcoded secrets
- [ ] Failure paths handled explicitly
