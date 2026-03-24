# Research Outputs

Phase 5 introduces structured research outputs — a bridge between raw news monitoring and informed decision-making.
**No orders are placed automatically.** All outputs are for research and analysis purposes only.

---

## Implementation Status

| Component | Status | Module |
|-----------|--------|--------|
| `WatchlistRegistry` (multi-type: assets/persons/topics/sources) | ✅ Sprint 4A | `app/research/watchlists.py` |
| `ResearchBrief` + `ResearchBriefBuilder` | ✅ Sprint 4A | `app/research/briefs.py` |
| `SignalCandidate` + `extract_signal_candidates()` | ✅ Sprint 4A | `app/research/signals.py` |
| CLI: `research brief`, `research watchlists`, `research signals` | ✅ Sprint 4A | `app/cli/main.py` |
| REST API endpoints (`/research/brief`, `/research/signals`) | ✅ Sprint 4B | `app/api/routers/research.py` |
| Provider-independent fallback analysis for briefs | ✅ Sprint 4C | `app/analysis/pipeline.py` |
| Intelligence architecture for fallback / companion / external analysis | ✅ documented | `docs/intelligence_architecture.md` |
| `WatchlistRegistry.find_by_text()` | ⏳ Sprint 4B | planned |
| `AssetResearchPack`, `NarrativePack`, `DailyResearchBrief` | ⏳ Sprint 4B+ | planned |

---

## Overview

### Current (Sprint 4A)

```
CanonicalDocument (is_analyzed=True)
      │
      ▼
WatchlistRegistry.filter_documents(tag, item_type)
      │
      ▼
ResearchBriefBuilder.build(documents)    extract_signal_candidates(documents)
      │                                          │
      ▼                                          ▼
ResearchBrief                           list[SignalCandidate]
(cluster snapshot — markdown/JSON)      (priority >= 8, sorted desc)
```

### Planned (Sprint 4B+)

```
list[SignalCandidate]
      │
      ├──→ AssetResearchPack     (all evidence for one asset)
      ├──→ NarrativePack         (all evidence for one narrative/theme)
      ├──→ BreakingNewsPack      (cluster of high-urgency signals)
      └──→ DailyResearchBrief    (full daily summary)
```

---

## Behavior Without External Provider

KAI remains usable when no external provider is configured, the provider is disabled,
or the provider call fails.

In those cases the shared analysis pipeline degrades to conservative fallback analysis and still
produces a valid `AnalysisResult` that can flow into persisted analyzed documents, Research Briefs,
and the existing signal-threshold path.

Architecture reference: [docs/intelligence_architecture.md](./intelligence_architecture.md)

### Analysis behavior

| Stage | External provider available | Deterministic fallback |
|-------|--------------|----------------------|
| Keyword matching | ✅ runs | ✅ runs |
| Entity extraction | ✅ runs | ✅ runs |
| External provider reasoning | ✅ runs | ❌ skipped |
| Analysis result | external output normalized into `AnalysisResult` | valid conservative `AnalysisResult` |
| Sentiment / impact / summary richness | higher-quality when available | conservative baseline |
| Priority score | full shared scoring path | same shared scoring path, typically lower-confidence outcomes |
| `actionable` | may be true | defensive by default |

### Research output with fallback docs

| Output | External-provider path | Fallback path |
|--------|-------------|-------------------|
| `ResearchBrief` generation | ✅ | ✅ |
| `ResearchBrief.key_documents` | ✅ | ✅ |
| `ResearchBrief.top_actionable_signals` | ✅ when thresholds are met | conservative and often sparse |
| `SignalCandidate` via `extract_signal_candidates()` | ✅ when thresholds are met | possible only if standard thresholds are met; no bypass |
| Overall sentiment in brief | richer calibration | conservative baseline |

### Companion layer outlook

The next intelligence step is the internal companion layer:
- local, provider-independent enhancement
- same downstream `AnalysisResult` contract
- initial focus on sentiment, relevance, conservative impact, tags/topics, short summaries,
  and signal preclassification

It is an enhancer between fallback and external providers, not a second research stack and not
an immediate replacement for frontier models.

---

## Watchlists

Watchlists define what the system monitors. Configured in `monitor/watchlists.yml`.

### Categories

| Category | Description | Identifier type |
|----------|-------------|-----------------|
| `crypto` | Coins and tokens | Symbol (BTC, ETH, SOL) |
| `equities` | Stocks | Symbol (MSTR, COIN, NVDA) |
| `etfs` | ETF products | Symbol (IBIT, FBTC, GBTC) |
| `persons` | Influential individuals | Name (Michael Saylor) |
| `topics` | Themes and events | Name (DeFi, Regulation) |
| `domains` | Trusted news sources | Domain (coindesk.com) |

### Adding items

Edit `monitor/watchlists.yml` directly:

```yaml
crypto:
  - symbol: MY_TOKEN
    name: My Token
    aliases: ["mytoken", "mt"]
    tags: [defi, layer2]
```

The registry is loaded at runtime from the YAML file. No sync command is needed —
restart the CLI or API process to pick up changes.

> **Sprint 4B planned**: `POST /watchlists/sync` — hot-reload endpoint. Not yet implemented.

### Document Filtering

`WatchlistRegistry.filter_documents(documents, tag, item_type="assets")` matches documents against
a watchlist tag and returns only those containing matching assets, persons, topics, or domains.

Asset matching uses uppercase symbol comparison (`BTC` in `doc.tickers + doc.crypto_assets`).
Person matching uses word-boundary regex against `doc.people`, `doc.entities`, and `entity_mentions`.
Topic matching uses tag/topic/category sets. Source matching normalises domain from `doc.url`.

> **Sprint 4B planned**: `WatchlistRegistry.find_by_text(text)` — free-text search across
> all watchlist entries using word-boundary regex (e.g. `"BTC"` matches `"BTC is rising"` but
> not `"BTCUSDT"`). Not yet implemented.

---

## Event-to-Asset Mapping

Maps news documents to specific tradeable assets using three layers:

### Layer 1: Analysis-Provided Assets (confidence: 0.90)
If the analysis result returns `affected_assets`, those are used directly with highest confidence.

### Layer 2: Direct Ticker Detection (confidence: 0.88)
Uppercase ticker symbols (BTC, ETH, NVDA, COIN…) found in title/text.

### Layer 3: Entity-to-Asset (confidence: 0.75–0.78)
Named entities map to associated assets:
- `"Coinbase"` → COIN, BTC
- `"BlackRock"` → IBIT, BTC
- `"MicroStrategy"` → MSTR, BTC
- `"SEC"` → BTC, ETH, COIN

### Layer 4: Thematic Mapping (confidence: 0.55–0.90)
Topic tags trigger asset groups:
- `defi` → ETH, LINK
- `bitcoin_etf` → BTC, IBIT, FBTC, GBTC
- `regulation` → BTC, ETH, COIN
- `halving` → BTC
- `ai` → NVDA

When the same asset is mapped by multiple layers, the **highest confidence** wins.

---

## Research Brief (Current — Sprint 4A)

A `ResearchBrief` is the primary aggregated research output. Produced by `ResearchBriefBuilder`.

### Fields

| Field | Description |
|-------|-------------|
| `cluster_name` | Watchlist tag or cluster name |
| `title` | Auto-generated: `"Research Brief: <cluster>"` |
| `summary` | Auto-generated summary sentence |
| `document_count` | Number of analyzed documents |
| `average_priority` | Mean priority score across documents |
| `overall_sentiment` | Dominant sentiment (most frequent label) |
| `top_documents` | Top 10 documents by (priority, impact, date) |
| `top_assets` | Up to 5 most-mentioned assets (`BriefFacet`) |
| `top_entities` | Up to 5 most-mentioned entities (`BriefFacet`) |
| `top_actionable_signals` | Documents with `priority >= 8` (max 10) |
| `key_documents` | Non-actionable documents (max 20) |

### CLI (Sprint 4A)

```bash
# Generate brief for a watchlist
python -m app.cli research brief --watchlist defi

# JSON output
python -m app.cli research brief --watchlist major --format json

# Filter by watchlist type
python -m app.cli research brief --watchlist saylor --type persons
```

### API

```bash
GET /research/brief?watchlist=defi&watchlist_type=topics
```

---

## Asset Research Pack (Sprint 4B+ — planned)

> Not yet implemented. Planned after Sprint 4B API endpoints.

A structured summary of all available signals for a single asset.

### Fields (planned)

| Field | Description |
|-------|-------------|
| `asset` | Symbol (BTC, ETH, etc.) |
| `direction_consensus` | bullish / bearish / neutral |
| `overall_confidence` | Average confidence across all signals |
| `signals` | List of SignalCandidate objects |
| `top_supporting_evidence` | Bull case points |
| `top_contradicting_evidence` | Bear case points |
| `key_risk_notes` | Risk flags |
| `narrative_labels` | Active narrative themes |
| `total_documents` | Number of source documents |

---

## Narrative Pack (Sprint 4B+ — planned)

> Not yet implemented.

Groups signals by thematic narrative. Available narrative labels:

| Label | Typical triggers |
|-------|-----------------|
| `regulatory_risk` | SEC actions, bans, compliance news |
| `institutional_adoption` | ETF flows, hedge fund buys, BlackRock |
| `market_crash` | Collapse, liquidations, depeg |
| `recovery` | Rebound, recovery, bull run |
| `macro_shift` | Fed decisions, inflation, rate changes |
| `liquidity_crisis` | Stablecoin depeg, bank failures |
| `tech_upgrade` | Protocol upgrades, forks, L2 launches |
| `ecosystem_growth` | Partnerships, new protocols, adoption |
| `sentiment_shift` | Social media trends, fear/greed index |
| `hack_exploit` | Smart contract exploits, exchange hacks |

---

## Daily Research Brief (Sprint 4B+ — planned)

> Not yet implemented. Planned after `NarrativePack` and `AssetResearchPack` are available.

Full daily overview combining all signal candidates across all watchlists.

---

## API Endpoints

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `/research/brief` | ✅ Sprint 4B | Research brief for analyzed documents filtered by watchlist + type |
| `GET` | `/research/signals` | ✅ Sprint 4B | Signal candidates list, optionally watchlist-boosted |
| `GET` | `/research/asset/{symbol}` | ⏳ Sprint 4B+ | Asset research pack |
| `GET` | `/watchlists/` | ⏳ planned | Watchlist summary |
| `GET` | `/watchlists/{tag}` | ⏳ planned | Items for a watchlist tag |
| `GET` | `/watchlists/search?q=...` | ⏳ Sprint 4B | Text search (requires `find_by_text()`) |
| `POST` | `/watchlists/sync` | ⏳ planned | Hot-reload from disk |
| `GET` | `/signals/historical/{asset}` | ⏳ Sprint 4B+ | Historical analogues |
