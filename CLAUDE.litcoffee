# CLAUDE.md

## Project Identity

**Project Name:** `ai_analyst_trading_bot`  
**Mission:** Build a production-oriented, modular AI-powered monitoring, analysis, alerting, research, and signal-preparation platform for crypto and traditional financial markets.  
**Engineering Motto:** **Simple but Powerful**

This repository is designed to support:
- multi-source market intelligence ingestion,
- structured AI analysis,
- historical and contextual research,
- alerting and prioritization,
- trading-oriented signal preparation,
- future extensibility for execution systems and agent-driven workflows.

The system must remain understandable, testable, modular, and safe.

---

## Core Principles

1. **Keep it simple**
   - Prefer a small number of strong abstractions.
   - Avoid unnecessary framework complexity.
   - Avoid premature microservices.

2. **Keep it powerful**
   - Design for extension, not reinvention.
   - Build generic interfaces with domain-specific adapters.
   - Support crypto, equities, ETFs, and macro-relevant signals.

3. **Keep it reliable**
   - Use typed models.
   - Use deterministic pipelines where possible.
   - Validate all structured outputs.
   - Log clearly and consistently.
   - Fail gracefully.

4. **Keep it modular**
   - Separate ingestion, normalization, enrichment, analysis, storage, alerts, research, and trading preparation.
   - Keep provider-specific logic out of the core domain.

5. **Keep it safe**
   - No hardcoded secrets.
   - No uncontrolled live trading.
   - No unstable scraping-first architecture.
   - Respect source differences: feed, page, channel, API, unresolved source.

---

## Non-Negotiable Rules

### Architecture
- Use **Python 3.12+**
- Prefer a **monorepo** with clean module boundaries
- Use **FastAPI** for service endpoints
- Use **Typer** for CLI commands
- Use **Pydantic / pydantic-settings** for configuration
- Use **SQLAlchemy 2.x** and Alembic for DB foundation
- Prefer **PostgreSQL**
- Use **pytest** and **ruff**
- Use **mypy** where it improves stability without heavy friction

### Code Quality
- Write readable, production-oriented code
- Prefer explicitness over magic
- Keep functions focused
- Keep interfaces small and stable
- Add tests for all non-trivial logic
- Do not introduce large dependencies without strong reason

### Data & Source Handling
- Never assume a URL is an RSS feed unless validated
- Never treat a podcast landing page as a feed automatically
- Never treat a YouTube channel URL as a transcript source automatically
- Classify first, resolve second, ingest third
- Maintain explicit unresolved/disabled/requires_api states

### LLM Integration
- Use provider abstraction
- Use structured outputs with schema validation
- No direct business logic inside transport/provider clients
- Version prompts
- Log model/provider metadata when useful
- Keep OpenAI/ChatGPT integration replaceable

### Safety & Scope
- No direct live-trading execution in early phases
- No broad fragile scraping systems as foundation
- No hidden assumptions about API access
- No credentials committed to the repository
- No monolithic "god service"

---

## Working Style Expectations

When working in this repository:

1. Think like a production-oriented lead engineer.
2. Be conservative with assumptions.
3. Prefer robust foundations over flashy features.
4. Implement incrementally.
5. Preserve existing architecture when extending.
6. Add or update tests with meaningful changes.
7. Document assumptions when needed.
8. If a source cannot be resolved cleanly, classify it correctly and move on.

---

## Repository Goals

The repository should evolve into a platform with the following capability layers:

1. **Source Registry & Classification**
2. **Ingestion & Resolution**
3. **Canonicalization & Deduplication**
4. **Rule-Based Analysis**
5. **LLM-Augmented Analysis**
6. **Scoring & Ranking**
7. **Alerting**
8. **Research Outputs**
9. **Signal Preparation**
10. **Advanced Connectors / Narrative Intelligence**
11. **Optional Execution Integration (future, gated)**

---

## System Scope

### Included
- News monitoring
- Website monitoring
- RSS ingestion
- Podcast source classification and resolution
- YouTube channel registry and resolution
- Search/filter/query DSL
- Sentiment, relevance, novelty, impact scoring
- Historical analog structures
- Alerts via Telegram and Email
- Watchlists and signal candidate generation
- Research briefs and prioritized outputs

### Excluded in early phases
- Full live order execution
- High-frequency trading infrastructure
- Broad scraping of arbitrary sources without stability/compliance plan
- Hard vendor lock-in
- Unreviewed autonomous production deployment changes

---

## Preferred Architecture Shape

- **Core domain** remains provider-agnostic
- **Integrations** remain isolated
- **Adapters** remain thin
- **Analysis outputs** remain structured
- **Source classification** is explicit and persistent
- **Research and signal prep** sit on top of normalized and analyzed documents

---

## Expected Module Separation

- `app/core/` → settings, logging, domain types, enums, errors, utilities
- `app/ingestion/` → source adapters, resolvers, registries, schedulers
- `app/normalization/` → canonical schemas, content cleanup, metadata alignment
- `app/enrichment/` → entities, tags, language, dedup helpers
- `app/analysis/` → keyword logic, DSL, sentiment, scoring, historical comparison
- `app/integrations/` → provider-specific clients and adapters
- `app/alerts/` → Telegram, email, alert rules, formatters
- `app/research/` → briefs, summaries, event clusters, watchlists
- `app/trading/` → signal candidates, asset mapping, risk notes
- `app/api/` → FastAPI endpoints
- `app/cli/` → Typer commands
- `app/storage/` → DB models, repositories, migrations
- `monitor/` → user-editable source lists and watchlists

---

## Required Source Taxonomy

Every source must be classifiable into one of these or a compatible extension:

- `rss_feed`
- `website`
- `news_api`
- `youtube_channel`
- `podcast_feed`
- `podcast_page`
- `reference_page`
- `social_api`
- `manual_source`
- `unresolved_source`

Every source should also have a lifecycle/status field such as:
- `active`
- `planned`
- `disabled`
- `requires_api`
- `manual_resolution`
- `unresolved`

---

## Prompting / Agent Collaboration Rules

This repository is expected to be worked on by multiple coding agents and assistants (e.g. Claude Code, Codex, ChatGPT-based workflows).  
To keep outputs compatible:

1. Do not invent new architectural directions without necessity.
2. Reuse existing domain models where possible.
3. Keep naming stable and descriptive.
4. Do not duplicate provider logic across modules.
5. Prefer extension over replacement.
6. Report:
   - files created,
   - files changed,
   - assumptions,
   - TODOs,
   - local test commands.

---

## Quality Bar

Every meaningful implementation should aim to satisfy:

- `pytest` passes
- `ruff check` passes
- config is documented
- new source types are documented
- structured outputs are validated
- unresolved external dependencies are explicitly marked
- no silent architectural drift

---

## Implementation Priorities

1. Foundation
2. Source classification and ingestion
3. Analysis core
4. Alerting
5. Research and signal preparation
6. Advanced connectors and narratives
7. Future execution integration

---

## Explicit Warnings

Do not:
- fake RSS feeds,
- silently scrape unstable sources as if they were APIs,
- hardwire business logic into LLM provider clients,
- entangle analysis logic with transport code,
- skip tests for parser/resolver/scoring logic,
- break existing modules for cosmetic refactors.

---

## What “Done” Means

A feature is only considered meaningfully complete when:
- code exists,
- config exists,
- tests exist,
- docs are updated,
- assumptions are recorded,
- failure states are handled,
- the design still fits the project architecture.

---

## Local Development Expectations

Typical commands should work and remain documented:

- install dependencies
- run API
- run CLI
- run tests
- run lint
- classify sources
- resolve podcasts
- resolve YouTube channels
- validate query syntax
- analyze pending documents
- send test alerts

---

## Final Behavioral Instruction

When unsure:
- classify conservatively,
- extend minimally,
- document clearly,
- avoid fragile shortcuts,
- preserve the long-term integrity of the repository.