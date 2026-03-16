# PROJECT_SPEC.md

## 1. Project Overview

### Project Name
`ai_analyst_trading_bot`

### Purpose
Build a modular, production-oriented AI-powered market intelligence platform that monitors relevant information across news, websites, social sources, YouTube, podcasts, and reference resources; normalizes and enriches the data; analyzes it using both rule-based and LLM-based methods; scores and prioritizes it; alerts the user; and prepares research and trading-oriented signal candidates.

### Guiding Motto
**Simple but Powerful**

The project should support both:
- **Crypto markets**
- **Traditional markets** such as equities, ETFs, and macro-driven contexts

The initial focus is **monitoring, analysis, research, and signal preparation**, not direct autonomous live trading.

---

## 2. Strategic Objectives

### Primary Objectives
1. Monitor many relevant content sources
2. Normalize heterogeneous content into one canonical format
3. Run structured AI and non-AI analysis
4. Rank and prioritize based on market relevance
5. Alert the user when important events occur
6. Prepare structured research outputs
7. Prepare trading-relevant signal candidates
8. Maintain extensibility for future broker/exchange execution integration

### Secondary Objectives
1. Preserve clean architecture for multi-agent development
2. Remain easy to maintain and iterate
3. Avoid vendor lock-in in core logic
4. Enable future integration with ChatGPT/OpenAI, Anthropic, and other model providers
5. Support future historical-event learning and narrative clustering

---

## 3. Scope

### In Scope
- RSS ingestion
- Website source registry
- News-domain registry
- Podcast source classification and resolution
- YouTube channel registry and normalization
- Query DSL and filter engine
- Keyword and entity-based monitoring
- LLM-assisted document analysis
- Sentiment, relevance, impact, novelty, confidence scoring
- Alerts via Telegram and Email
- Watchlists
- Research briefs
- Signal candidate generation
- Historical analog data structures
- Multi-source deduplication and canonicalization

### Out of Scope in Early Phases
- Direct autonomous live order placement
- HFT / ultra-low-latency execution systems
- Full unmanaged scraping across arbitrary websites
- Full social connector completeness without API/config support
- Production self-modifying agents without review gates

---

## 4. Users / Use Cases

### Primary User
An advanced investor / operator who wants:
- near real-time monitoring,
- relevant filtered signals,
- summarized high-value insights,
- AI-assisted interpretation,
- alerting,
- research support,
- trading preparation.

### Example Use Cases
1. Detect major Bitcoin or Ethereum narrative changes across news and social channels
2. Track mentions of key crypto personalities and assess likely market significance
3. Monitor YouTube and podcast ecosystems for sentiment shifts or recurring themes
4. Detect market-impacting content clusters
5. Prioritize breaking events and send alerts via Telegram
6. Build daily research briefs for crypto and equities
7. Generate signal candidates tied to assets, sectors, or themes

---

## 5. Design Principles

### Principle 1: Separation of Concerns
Separate:
- ingestion
- normalization
- enrichment
- analysis
- scoring
- alerting
- research
- signal preparation

### Principle 2: Source Correctness
A source must be accurately classified before it is ingested.

### Principle 3: Structured Outputs
LLM-based analysis must be schema-driven and validated.

### Principle 4: Extensibility
The core domain must not depend tightly on specific providers.

### Principle 5: Stability over Cleverness
Do not optimize for novelty at the expense of maintainability.

---

## 6. High-Level Architecture

### Recommended Monorepo Structure

```text
repo/
  app/
    api/
    cli/
    core/
    ingestion/
    normalization/
    enrichment/
    analysis/
    alerts/
    research/
    trading/
    storage/
    integrations/
    orchestration/
    jobs/
    config/
  monitor/
  tests/
  docs/
  scripts/
  docker/
  .github/workflows/