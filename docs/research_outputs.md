# Research Outputs

Phase 5 introduces structured research outputs — a bridge between raw news monitoring and informed decision-making.
**No orders are placed automatically.** All outputs are for research and analysis purposes only.

---

## Overview

```
Documents (ingested)
      │
      ▼
DocumentScores (Phase 3 analysis pipeline)
      │
      ▼
SignalCandidateGenerator
      │
      ▼
SignalCandidate (per asset, per document)
      │
      ├──→ AssetResearchPack     (all evidence for one asset)
      ├──→ NarrativePack         (all evidence for one narrative/theme)
      ├──→ BreakingNewsPack      (cluster of high-urgency signals)
      └──→ DailyResearchBrief    (full daily summary)
```

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

Edit `monitor/watchlists.yml`:

```yaml
crypto:
  - symbol: MY_TOKEN
    name: My Token
    aliases: ["mytoken", "mt"]
    tags: [defi, layer2]
```

Then run:
```bash
python -m app.cli watchlists sync
```

Or reload via API:
```bash
POST /watchlists/sync
```

### Text Matching

The `WatchlistRegistry.find_by_text()` method uses **word-boundary regex** to prevent false positives:
- `"BTC"` matches `"BTC is rising"` ✓
- `"BTC"` does NOT match `"BTCUSDT"` ✗
- `"bitcoin"` matches `"Bitcoin rally continues"` ✓

---

## Event-to-Asset Mapping

Maps news documents to specific tradeable assets using three layers:

### Layer 1: LLM-Provided Assets (confidence: 0.90)
If the LLM analysis returns `affected_assets`, those are used directly with highest confidence.

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

## Asset Research Pack

A structured summary of all available signals for a single asset.

### Fields

| Field | Description |
|-------|-------------|
| `asset` | Symbol (BTC, ETH, etc.) |
| `direction_consensus` | bullish / bearish / neutral / mixed |
| `overall_confidence` | Average confidence across all signals |
| `urgency` | Maximum urgency across signals |
| `signals` | List of SignalSummary objects |
| `top_supporting_evidence` | Bull case points (deduplicated) |
| `top_contradicting_evidence` | Bear case points (deduplicated) |
| `key_risk_notes` | Risk flags (low credibility, recycled news, etc.) |
| `narrative_labels` | Active narrative themes |
| `total_documents` | Number of source documents |
| `sources` | Source IDs that contributed |

### API

```bash
GET /research/asset/BTC
GET /research/asset/ETH
GET /research/asset/NVDA
```

### CLI

```bash
python -m app.cli research asset BTC
```

---

## Narrative Pack

Groups signals by thematic narrative.

### Available Narratives

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

## Daily Research Brief

Full daily overview combining all signal candidates.

### Contents
- **total_signals**: Total signal candidates generated today
- **market_sentiment**: positive / neutral / negative (derived from direction distribution)
- **overall_urgency**: Maximum urgency across all signals
- **top_assets**: Up to 6 asset packs (sorted by confidence)
- **active_narratives**: Up to 4 narrative packs (min 2 signals each)
- **breaking_clusters**: Urgent signals cluster (IMMEDIATE + SHORT_TERM)
- **key_themes**: Active narrative labels
- **risk_summary**: Top risk notes
- **watchlist_hits**: All assets with at least one signal

### API

```bash
GET /research/brief
```

### CLI

```bash
python -m app.cli research build-brief
python -m app.cli research build-brief --date 2024-01-15
```

### Custom generation from document scores

```bash
POST /research/generate
```

```json
[
  {
    "title": "Bitcoin ETF Surpasses $10B AUM",
    "sentiment_label": "positive",
    "sentiment_score": 0.82,
    "impact_score": 0.85,
    "affected_assets": ["BTC", "IBIT"],
    "matched_entities": ["BlackRock"]
  }
]
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/watchlists/` | Summary by category |
| `GET` | `/watchlists/{category}` | Items in a category |
| `GET` | `/watchlists/search?q=bitcoin` | Text search |
| `POST` | `/watchlists/sync` | Reload from disk |
| `GET` | `/research/brief` | Daily brief (sample) |
| `GET` | `/research/asset/{symbol}` | Asset pack |
| `POST` | `/research/generate` | Brief from inputs |
| `GET` | `/signals/candidates` | Signal list (sample) |
| `GET` | `/signals/candidates/{asset}` | Signals for asset |
| `POST` | `/signals/evaluate` | Evaluate single doc |
| `GET` | `/signals/historical/{asset}` | Historical analogues |
