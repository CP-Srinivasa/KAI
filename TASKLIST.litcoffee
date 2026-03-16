
---

# 3) `TASKLIST.md`

```md
# TASKLIST.md

## Project Task Roadmap
This tasklist is the operational execution layer for `ai_analyst_trading_bot`.

It is intended for:
- Claude Code
- Codex / ChatGPT-based coding agents
- human review
- staged implementation planning

The list is organized by phase and priority.

---

# Global Rules for Every Task

Before implementing any task:
1. Preserve existing architecture
2. Avoid unnecessary refactors
3. Add tests for non-trivial logic
4. Update docs if behavior changes
5. Report:
   - created files
   - changed files
   - assumptions
   - TODOs
   - local test commands

Definition of completion for a task:
- implementation exists
- tests exist or are updated
- docs are updated where relevant
- config implications are clear
- failure modes are handled

---

# PHASE 1 — FOUNDATION

## P1.1 Repository Skeleton
- [ ] Create monorepo folder structure
- [ ] Create `app/`, `tests/`, `docs/`, `monitor/`, `docker/`, `.github/workflows/`
- [ ] Add placeholder `__init__.py` files where needed

## P1.2 Python Project Setup
- [ ] Create `pyproject.toml`
- [ ] Configure dependencies
- [ ] Configure dev dependencies
- [ ] Configure pytest
- [ ] Configure ruff
- [ ] Optionally prepare mypy config

## P1.3 Settings
- [ ] Create app settings model
- [ ] Create database settings model
- [ ] Create alert settings model
- [ ] Create provider settings model
- [ ] Create source settings model
- [ ] Add `.env.example`

## P1.4 Logging
- [ ] Implement structured logging setup
- [ ] Add configurable log level
- [ ] Ensure logging can be reused across API/CLI/jobs

## P1.5 Core Models and Enums
- [ ] Create `SourceType` enum
- [ ] Create `SourceStatus` enum
- [ ] Create base `QuerySpec`
- [ ] Create base `CanonicalDocument`
- [ ] Create base error classes

## P1.6 Base Interfaces
- [ ] Create base source adapter interface
- [ ] Create base analysis provider interface
- [ ] Create common service interface conventions if needed

## P1.7 API Foundation
- [ ] Create FastAPI app
- [ ] Implement `/health`
- [ ] Implement `/sources` placeholder
- [ ] Implement `/query/validate` placeholder

## P1.8 CLI Foundation
- [ ] Create Typer CLI
- [ ] Add `sources classify`
- [ ] Add `podcasts resolve`
- [ ] Add `youtube resolve`
- [ ] Add `query validate`

## P1.9 Monitor Files
- [ ] Create `monitor/keywords.txt`
- [ ] Create `monitor/hashtags.txt`
- [ ] Create `monitor/youtube_channels.txt`
- [ ] Create `monitor/podcast_feeds_raw.txt`
- [ ] Create `monitor/podcast_feeds_resolved.txt`
- [ ] Create `monitor/podcast_sources_unresolved.txt`
- [ ] Create `monitor/website_sources.txt`
- [ ] Create `monitor/news_domains.txt`
- [ ] Create `monitor/social_accounts.txt`
- [ ] Create `monitor/entity_aliases.yml`

## P1.10 Initial Data Population
- [ ] Populate YouTube channel list
- [ ] Populate podcast raw source list
- [ ] Populate website source list
- [ ] Populate news domain list
- [ ] Populate keywords list
- [ ] Populate social handles list
- [ ] Populate initial entity aliases

## P1.11 Docker and CI
- [ ] Create `Dockerfile`
- [ ] Create `docker-compose.yml`
- [ ] Add GitHub Actions workflow for tests and linting

## P1.12 Documentation
- [ ] Write `README.md`
- [ ] Write `docs/architecture.md`

## P1.13 Phase 1 Tests
- [ ] Test settings loading
- [ ] Test health endpoint
- [ ] Test CLI startup
- [ ] Test keyword loading
- [ ] Test core models import/validation

---

# PHASE 2 — INGESTION CORE

## P2.1 Source Classification
- [ ] Design source classification model
- [ ] Implement source classifier utilities
- [ ] Add classification tests

## P2.2 URL Typing
- [ ] Detect RSS / Atom feeds
- [ ] Detect YouTube handle URLs
- [ ] Detect YouTube `/c/` URLs
- [ ] Detect Spotify show URLs
- [ ] Detect Apple podcast URLs
- [ ] Detect Podigee URLs
- [ ] Detect general websites
- [ ] Detect reference pages
- [ ] Detect unresolved source cases

## P2.3 RSS Adapter
- [ ] Implement RSS/Atom fetcher
- [ ] Normalize feed entries
- [ ] Add retries and timeouts
- [ ] Add tests

## P2.4 Podcast Resolution
- [ ] Load `podcast_feeds_raw.txt`
- [ ] Classify each raw podcast-like URL
- [ ] Attempt valid feed resolution where possible
- [ ] Write resolved feeds to `podcast_feeds_resolved.txt`
- [ ] Write unresolved sources to `podcast_sources_unresolved.txt`
- [ ] Document resolution reasons

## P2.5 YouTube Registry / Resolution
- [ ] Load `youtube_channels.txt`
- [ ] Normalize channel URLs
- [ ] Deduplicate entries
- [ ] Store structured channel source records
- [ ] Prepare placeholders for future channel-id resolution

## P2.6 Website Source Registry
- [ ] Create structured source registry for websites/domains
- [ ] Store domain metadata, notes, status, source type

## P2.7 Canonicalization Foundations
- [ ] Expand canonical document model
- [ ] Add basic content cleaning helpers
- [ ] Add metadata normalization helpers

## P2.8 Deduplication
- [ ] Implement normalized URL comparison
- [ ] Implement content-hash generation
- [ ] Implement title normalization
- [ ] Implement basic duplicate detection helpers

## P2.9 API / CLI Expansion
- [ ] Improve `/sources`
- [ ] Add RSS ingest CLI command
- [ ] Add podcast resolve CLI behavior
- [ ] Add YouTube resolve CLI behavior

## P2.10 Documentation
- [ ] Write `docs/source_classification.md`

## P2.11 Phase 2 Tests
- [ ] URL classification tests
- [ ] RSS detection tests
- [ ] YouTube normalization tests
- [ ] Podcast resolution tests
- [ ] Dedup tests
- [ ] Canonical document construction tests

---

# PHASE 3 — ANALYSIS CORE

## P3.1 Query DSL
- [ ] Design query grammar
- [ ] Implement parser
- [ ] Implement AST
- [ ] Implement validator
- [ ] Implement executor against canonical docs

## P3.2 Keyword Engine
- [ ] Implement keyword loader
- [ ] Implement normalized matching
- [ ] Implement alias-aware matching
- [ ] Implement weighted hits
- [ ] Implement title-priority hits

## P3.3 Entity / Watchlist Matching
- [ ] Detect person/entity hits
- [ ] Detect social handle hits
- [ ] Detect ticker/asset hits

## P3.4 LLM Abstraction
- [ ] Implement analysis provider base interface
- [ ] Define structured analysis schema
- [ ] Validate responses against schema

## P3.5 OpenAI / ChatGPT Provider
- [ ] Implement OpenAI provider client
- [ ] Add structured response handling
- [ ] Add retries / timeouts
- [ ] Add configuration model
- [ ] Add mock/test support

## P3.6 Scoring Base
- [ ] Implement relevance scoring
- [ ] Implement impact scoring
- [ ] Implement novelty scoring
- [ ] Implement confidence helpers
- [ ] Add credibility placeholder scoring

## P3.7 Historical Foundations
- [ ] Define historical event schema
- [ ] Define event-outcome schema
- [ ] Define analog candidate structure

## P3.8 API / CLI Expansion
- [ ] Add `/documents/search`
- [ ] Add `/documents/{id}`
- [ ] Add `/analysis/{id}`
- [ ] Improve `/query/validate`
- [ ] Add `analyze pending`
- [ ] Add `documents search`

## P3.9 Documentation
- [ ] Write `docs/query_dsl.md`
- [ ] Write `docs/openai_integration.md`
- [ ] Write `docs/analysis_pipeline.md`

## P3.10 Phase 3 Tests
- [ ] Query parser tests
- [ ] AST tests
- [ ] keyword matching tests
- [ ] alias tests
- [ ] structured output validation tests
- [ ] provider mock tests
- [ ] scoring tests

---

# PHASE 4 — ALERTING

## P4.1 Alert Rule Models
- [ ] Define alert rule schema
- [ ] Define alert severity model
- [ ] Define alert delivery model

## P4.2 Telegram Integration
- [ ] Implement Telegram config
- [ ] Implement Telegram message formatter
- [ ] Implement send function
- [ ] Implement dry-run mode

## P4.3 Email Integration
- [ ] Implement email config
- [ ] Implement text/html formatter
- [ ] Implement send function
- [ ] Implement dry-run mode

## P4.4 Alert Evaluation Engine
- [ ] Implement threshold logic
- [ ] Implement rule evaluation against analyzed documents
- [ ] Implement dedup logic for repeated alerts
- [ ] Implement severity mapping

## P4.5 Alert Types
- [ ] Immediate breaking alerts
- [ ] Digest alerts
- [ ] Daily brief
- [ ] Watchlist alerts
- [ ] Anomaly alerts

## P4.6 API / CLI Expansion
- [ ] Add `/alerts/test`
- [ ] Add `/alerts/rules`
- [ ] Add `/alerts/preview`
- [ ] Add `alerts send-test`
- [ ] Add `alerts send-digest`
- [ ] Add `alerts evaluate-pending`

## P4.7 Documentation
- [ ] Write `docs/alerting.md`

## P4.8 Phase 4 Tests
- [ ] threshold tests
- [ ] severity tests
- [ ] Telegram formatter tests
- [ ] Email formatter tests
- [ ] dry-run tests
- [ ] dedup tests

---

# PHASE 5 — RESEARCH & SIGNAL PREPARATION

## P5.1 Watchlists
- [ ] Design watchlist schema
- [ ] Support asset watchlists
- [ ] Support person watchlists
- [ ] Support topic watchlists
- [ ] Support source watchlists

## P5.2 Event-to-Asset Mapping
- [ ] Implement direct ticker mapping
- [ ] Implement entity-to-asset mapping
- [ ] Implement thematic mapping rules
- [ ] Add confidence values

## P5.3 Signal Candidates
- [ ] Define signal candidate schema
- [ ] Generate signal candidates from analyzed documents
- [ ] Include supporting evidence
- [ ] Include contradicting evidence
- [ ] Include risk notes
- [ ] Include recommended next step

## P5.4 Research Packs
- [ ] Build asset brief generator
- [ ] Build topic brief generator
- [ ] Build daily market brief generator
- [ ] Build cluster summary generator

## P5.5 Historical Analog Extension
- [ ] Link events to analogs
- [ ] Summarize known outcomes
- [ ] Add caveat/confidence handling

## P5.6 API / CLI Expansion
- [ ] Add `/watchlists`
- [ ] Add `/research/brief`
- [ ] Add `/research/asset/{symbol}`
- [ ] Add `/signals/candidates`
- [ ] Add `research build-brief`
- [ ] Add `watchlists sync`
- [ ] Add `signals generate`

## P5.7 Documentation
- [ ] Write `docs/research_outputs.md`
- [ ] Write `docs/signal_candidates.md`

## P5.8 Phase 5 Tests
- [ ] watchlist tests
- [ ] mapping tests
- [ ] signal generation tests
- [ ] research formatting tests
- [ ] analog selection tests

---

# PHASE 6 — ADVANCED CONNECTORS & NARRATIVE INTELLIGENCE

## P6.1 YouTube Expansion
- [ ] Add metadata retrieval layer
- [ ] Prepare transcript pipeline
- [ ] Add transcript availability status handling

## P6.2 Podcast Expansion
- [ ] Ingest resolved podcast feeds
- [ ] Prepare transcript-ready episode pipeline
- [ ] Add unresolved transcript handling

## P6.3 Social Connectors
- [ ] Prepare or implement Reddit connector
- [ ] Prepare or implement X/Twitter connector
- [ ] Prepare or implement Facebook connector
- [ ] Prepare or implement Google News connector
- [ ] Prepare or implement Bing News connector
- [ ] Prepare or implement Yahoo News connector

## P6.4 Connector State Management
- [ ] Add connector status model
- [ ] Support `active`, `disabled`, `requires_api`, `planned`

## P6.5 Narrative Clustering
- [ ] Implement topic/entity/asset clustering
- [ ] Implement narrative label generation
- [ ] Implement narrative acceleration detection
- [ ] Implement cross-source cluster merge

## P6.6 Historical Pattern Enrichment
- [ ] Group related event families
- [ ] Link clusters to historical patterns
- [ ] Expand reaction archive model

## P6.7 Agent / MCP Readiness
- [ ] Ensure provider/tool abstractions remain compatible
- [ ] Document future MCP/remote-tool options

## P6.8 Documentation
- [ ] Write `docs/connectors.md`
- [ ] Write `docs/narrative_clustering.md`
- [ ] Write `docs/transcripts.md`
- [ ] Write `docs/advanced_roadmap.md`

## P6.9 Phase 6 Tests
- [ ] connector config tests
- [ ] status handling tests
- [ ] clustering helper tests
- [ ] transcript pipeline component tests
- [ ] narrative label tests

---

# CROSS-CUTTING TASKS

## C1. Security / Safety
- [ ] Ensure no secrets in repo
- [ ] Ensure provider keys use env vars
- [ ] Ensure dangerous live-trading paths remain gated or absent
- [ ] Ensure dry-run modes where appropriate

## C2. Data Quality
- [ ] Add duplicate suppression checks
- [ ] Add malformed source handling
- [ ] Add schema validation at key boundaries

## C3. Observability
- [ ] Improve structured logging
- [ ] Add health visibility for source processing
- [ ] Add useful debug traces without noisy chaos

## C4. Developer Experience
- [ ] Add Makefile or task runner if useful
- [ ] Improve local startup flow
- [ ] Improve docs for setup and testing

## C5. Review Discipline
For every major phase, confirm:
- [ ] tests pass
- [ ] lint passes
- [ ] docs updated
- [ ] changed files listed
- [ ] assumptions documented
- [ ] TODOs captured

---

# BACKLOG / LATER

## B1. Execution Integration (Future, gated)
- [ ] broker adapter interfaces
- [ ] exchange adapter interfaces
- [ ] risk gate integration
- [ ] paper-trading mode
- [ ] reconciliation layer

## B2. Advanced Historical Analytics
- [ ] event family analytics
- [ ] historical impact heuristics
- [ ] analog ranking refinements

## B3. Advanced Model Routing
- [ ] multi-provider routing
- [ ] cost-aware model selection
- [ ] fallback strategies

## B4. Dashboard / UI
- [ ] admin dashboard
- [ ] source overview
- [ ] alert center
- [ ] research browser
- [ ] signal candidate board

---

# Immediate Recommended Execution Order

1. Complete all Phase 1 tasks
2. Complete all Phase 2 tasks
3. Complete all Phase 3 tasks
4. Complete all Phase 4 tasks
5. Complete all Phase 5 tasks
6. Complete selected stable Phase 6 tasks

---

# Required End-of-Run Report Format for Coding Agents

At the end of any implementation run, provide:

1. Files created
2. Files modified
3. Key architectural decisions
4. Assumptions
5. Open TODOs
6. Risks / limitations
7. Exact local test commands

This reporting format is mandatory for all coding-agent work in this repository.