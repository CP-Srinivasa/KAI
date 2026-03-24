# Advanced Roadmap

This document outlines planned extensions and future phases for the AI Analyst Trading Bot beyond the current Phase 6 implementation.

> **Principle**: Each phase builds on the previous. Stability before completeness. No breaking changes to existing modules.

---

## Current State (Phase 6 Complete)

| Component | Status |
|-----------|--------|
| Watchlists (6 categories, 56+ entries) | ✅ Phase 5 |
| Event-to-Asset Mapper | ✅ Phase 5 |
| Signal Candidates & Generator | ✅ Phase 5 |
| Research Packs & Daily Brief | ✅ Phase 5 |
| Historical Analogues (8 seed events) | ✅ Phase 5 |
| Trading Relevance Ranker | ✅ Phase 5 |
| REST API (watchlists, research, signals) | ✅ Phase 5 |
| CLI (watchlists, research, signals) | ✅ Phase 5 |
| YouTube Transcript Pipeline | ✅ Phase 6 |
| Podcast Transcript Parser | ✅ Phase 6 |
| Social Connectors (6 platforms) | ✅ Phase 6 |
| Narrative Clustering Engine | ✅ Phase 6 |
| Historical Pattern Enrichment | ✅ Phase 6 |
| MCP / Agent Tool Registry | ✅ Phase 6 |
| Agent Context Builder | ✅ Phase 6 |
| MCP Adapter (stub) | ✅ Phase 6 |

---

## Phase 7: Live Data Integration

### 7A — Price Feed Connector
- Connect to market price APIs (CoinGecko, Binance, Yahoo Finance)
- Store OHLCV snapshots in the database
- Update `SignalCandidate.price_context` with live price data
- **[REQUIRES: CoinGecko API key (optional) or Binance public endpoint]**

### 7B — Live Signal Generation
- Replace `get_sample_candidates()` with a real ingestion → NLP → signal pipeline
- Ingest from active social connectors on a schedule (cron / APScheduler)
- Store generated `SignalCandidate` rows in PostgreSQL
- Add `SignalCandidateRepository` with CRUD operations

### 7C — Whisper Transcript Integration
- Implement audio-to-text transcription for podcasts marked `ai_required`
- Use `openai-whisper` or `faster-whisper` (local) or OpenAI Whisper API
- Integrate transcribed text back into the signal pipeline
- **[REQUIRES: `pip install faster-whisper` or `OPENAI_API_KEY`]**

### 7D — Scheduled Ingestion
- Add APScheduler or Celery beat for periodic ingestion
- Schedule: social fetch every 15min, brief generation every 1h
- Add `/admin/scheduler` API endpoint for status

---

## Phase 8: NLP & Entity Extraction

### 8A — Structured NLP Pipeline
- Extract named entities (persons, organizations, tickers) from text
- Use spaCy with custom crypto/finance entity rules
- Feed extracted entities into `EventToAssetMapper` directly
- Replace hardcoded `ENTITY_TO_ASSET` dict with a trained model

### 8B — Sentiment Analysis
- Replace rule-based sentiment with a fine-tuned model
- Candidate models: FinBERT, CryptoBERT, or custom
- Output: `DirectionHint` + confidence score
- **[REQUIRES: `pip install transformers torch`]**

### 8C — Keyword / Topic Extraction
- Auto-generate `NarrativeLabel` assignments from text
- Use TF-IDF or LDA for topic modeling on transcript/post batches
- Reduce reliance on hardcoded signal keyword rules

---

## Phase 9: Database & Persistence Layer

### 9A — Full Schema Migration
- Define and apply Alembic migrations for all models:
  - `signal_candidates`, `narrative_clusters`, `research_packs`
  - `social_posts`, `podcast_episodes`, `youtube_transcripts`
  - `watchlist_hits`, `historical_events`
- Enable persistent storage of all pipeline outputs

### 9B — Signal History & Backtesting Support
- Store signals with timestamps → enable lookback queries
- Add `GET /signals/history?asset=BTC&days=30` endpoint
- Support backtesting against stored signal history

### 9C — Alert Rules Engine
- Define threshold-based alert rules (velocity spike, confidence cross, urgency change)
- Persist rules in database with per-user configuration
- Trigger alerts → webhook / email / Slack

---

## Phase 10: Advanced Agent Capabilities

### 10A — Full MCP Server
- Implement production MCP server using `mcp` Python SDK
- Support stdio transport (Claude Desktop) and SSE transport
- Register all tools from `ToolRegistry.default()`
- Add MCP server launch script: `python -m app.agents.mcp_server`
- **[REQUIRES: `pip install mcp`]**

### 10B — Claude API Integration
- Build an `AnthropicAnalystAgent` using the Anthropic Python SDK
- Implement tool-use loop: send context → receive tool calls → execute → continue
- Use `AgentContextBuilder` output as the initial context
- Support streaming for real-time analysis output
- **[REQUIRES: `ANTHROPIC_API_KEY`, `pip install anthropic`]**

### 10C — Agent Memory & Continuity
- Add conversation history persistence for multi-turn analysis
- Store agent sessions in the database with TTL
- Support follow-up questions on prior research sessions

### 10D — Multi-Agent Orchestration
- Define specialized sub-agents: ResearchAgent, RiskAgent, SummaryAgent
- Orchestrate with a supervisor agent pattern
- Route queries to appropriate sub-agents based on intent

---

## Phase 11: Production Hardening

### 11A — Observability
- Add Prometheus metrics for all pipeline stages
- Structured log shipping to ELK or Loki
- Distributed tracing (OpenTelemetry)
- Dashboard: signal volume, latency, connector uptime

### 11B — Caching Layer
- Redis cache for: research packs, daily briefs, historical analogues
- Cache invalidation strategy based on signal freshness
- `GET /research/brief` served from cache (TTL: 5min)

### 11C — Authentication & Multi-tenancy
- JWT authentication for all API endpoints
- Per-user watchlists, alert rules, and research preferences
- API key management for external integrations

### 11D — Containerization & Deployment
- Finalize Dockerfile + docker-compose for local dev
- Kubernetes Helm chart for production deployment
- CI/CD: GitHub Actions → build → test → deploy
- Secrets management via Vault or AWS Secrets Manager

---

## Phase 12: Advanced Analytics

### 12A — Signal Correlation Engine
- Detect correlated signals across assets (e.g. BTC/ETH co-movement)
- Build asset correlation matrix from historical signal history
- Surface in research packs as "correlated assets"

### 12B — Predictive Confidence Calibration
- Calibrate confidence scores against historical outcomes
- Track "signal → price movement" accuracy over time
- Adjust `TradingRelevanceRanker` weights based on calibration data

### 12C — Portfolio-Level Analysis
- Given a user-defined portfolio, surface most relevant signals
- Aggregate risk across all held assets
- "Macro view" for multi-asset exposure

---

## Dependency Summary

| Phase | Key New Dependencies |
|-------|---------------------|
| 7A | `httpx` (already present), CoinGecko/Binance API |
| 7B | `apscheduler` or `celery` |
| 7C | `faster-whisper` or OpenAI Whisper API |
| 8A | `spacy`, `en_core_web_sm` model |
| 8B | `transformers`, `torch` |
| 9A | Alembic migrations (already scaffolded) |
| 10A | `pip install mcp` |
| 10B | `pip install anthropic` |
| 11A | `prometheus-client`, `opentelemetry-sdk` |
| 11B | `redis`, `redis-py` or `aioredis` |
| 11C | `python-jose`, `passlib` |

---

## Guiding Principles for Future Phases

1. **Incremental by default** — each PR should work independently without depending on the next
2. **Stub over skip** — if a feature needs external credentials, build the stub first with `[REQUIRES:]` annotations
3. **Tests first** — no phase is complete without unit tests for its core logic
4. **No trade execution** — this system is a research/analysis tool; it never places orders
5. **Docs alongside code** — each phase adds or updates a docs/ file
6. **Observable** — every pipeline stage logs structured events for debugging
