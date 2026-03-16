# Signal Candidates

A **SignalCandidate** is a structured, research-grade output that summarises the evidence for a
potential trading opportunity.

> ⚠ **Signal Candidates are NOT trade orders.**
> They are research artifacts for informed human decision-making.
> No positions are sized or executed automatically.

---

## What is a Signal Candidate?

A SignalCandidate is generated when:
1. A document passes the analysis pipeline with sufficient impact score (≥ 0.30 by default)
2. At least one tradeable asset can be mapped to the document
3. The asset mapping confidence exceeds the minimum threshold (≥ 0.55 by default)

Each candidate is tied to **one asset** and **one source document**.
A single document with high impact can generate multiple candidates (one per mapped asset).

---

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique candidate identifier |
| `document_id` | str | Source document ID |
| `asset` | str | Symbol (BTC, ETH, NVDA, etc.) |
| `direction_hint` | enum | bullish / bearish / neutral / mixed |
| `confidence` | float (0–1) | Signal confidence score |
| `supporting_evidence` | list[str] | Bull case, asset link reason |
| `contradicting_evidence` | list[str] | Bear case, counter-arguments |
| `risk_notes` | list[str] | Data quality flags |
| `source_quality` | float (0–1) | Source credibility score |
| `historical_context` | str | Similar past event reference |
| `narrative_label` | enum | Thematic classification |
| `urgency` | enum | Time horizon of the signal |
| `severity` | enum | Document priority level |
| `recommended_next_step` | str | Plain-language research action |
| `title` | str | Source document title |
| `url` | str | Source URL |

---

## Direction Hints

| Value | Meaning |
|-------|---------|
| `bullish` | Positive sentiment ≥ 0.30, predominantly positive |
| `bearish` | Negative sentiment ≤ -0.30, predominantly negative |
| `neutral` | Sentiment close to zero (|score| < 0.15) |
| `mixed` | Conflicting signals — no clear majority |

---

## Urgency Levels

| Level | Horizon | When assigned |
|-------|---------|---------------|
| `immediate` | Hours | CRITICAL priority + BREAKING alert type |
| `short_term` | 1–7 days | HIGH priority or BREAKING/WATCHLIST_HIT |
| `medium_term` | 1–4 weeks | MEDIUM priority |
| `long_term` | Months | LOW priority |
| `monitor` | Ongoing | No clear timeframe |

---

## Narrative Labels

Narratives classify the thematic context of the signal:

| Label | Example triggers |
|-------|----------------|
| `regulatory_risk` | SEC investigation, ban, compliance deadline |
| `institutional_adoption` | ETF AUM milestone, hedge fund allocation |
| `market_crash` | Exchange collapse, depeg, liquidation cascade |
| `recovery` | Rebound news, technical breakout |
| `macro_shift` | Fed pivot, rate hike, inflation data |
| `liquidity_crisis` | Stablecoin instability, credit crunch |
| `tech_upgrade` | Protocol upgrade, L2 launch, fork |
| `ecosystem_growth` | Partnership, integration, grant |
| `sentiment_shift` | Social media trend, fear index |
| `hack_exploit` | Smart contract exploit, exchange hack |

---

## Risk Notes

Risk notes are automatically added when:
- Source credibility < 60%: `"Low source credibility (55%)"`
- Spam probability > 30%: `"Elevated spam probability (35%)"`
- Novelty score < 40%: `"Low novelty — may be recycled news"`
- Thematic mapping (indirect link): `"Indirect asset link via thematic mapping (confidence 68%)"`

---

## Confidence Score

Confidence is a composite of:
- **Asset mapping confidence** (how certain the asset link is: 0.55–0.90)
- **Document impact score** (how impactful the underlying document is: 0–1)

Formula:
```
confidence = asset_mapping_confidence * impact_score * 0.5 + asset_mapping_confidence * 0.5
```

This ensures that even a high-confidence asset mapping is tempered by a low-impact document.

### Confidence thresholds

| Range | Interpretation |
|-------|---------------|
| ≥ 0.70 | High confidence — strong asset link and impact |
| 0.55–0.70 | Moderate confidence — actionable for research |
| 0.40–0.55 | Low confidence — background context only |
| < 0.40 | Filtered out by default |

---

## Trading Relevance Score

The `TradingRelevanceRanker` assigns a composite score for ranking:

| Factor | Weight |
|--------|--------|
| Signal confidence | 30% |
| Urgency | 25% |
| Document impact | 25% |
| Source quality | 12% |
| Novelty | 8% |

Higher score = more relevant for near-term research focus.

---

## Recommended Next Steps

The `recommended_next_step` field provides plain-language guidance:

- **IMMEDIATE**: `"Review latest BTC order book depth and on-chain flows immediately."`
- **SHORT_TERM bullish**: `"Monitor BTC for follow-through confirmation over next 1–3 days before any position sizing."`
- **BEARISH**: `"Assess BTC downside exposure; review stop levels and sector correlation."`
- **MONITOR**: `"Continue monitoring BTC — insufficient confidence for near-term action."`

---

## API Usage

### Evaluate a document

```bash
POST /signals/evaluate
```

```json
{
  "title": "SEC Opens Investigation Into Major DeFi Protocol",
  "sentiment_label": "negative",
  "sentiment_score": -0.70,
  "impact_score": 0.75,
  "credibility_score": 0.83,
  "priority": "high",
  "matched_entities": ["SEC"],
  "affected_assets": ["ETH"],
  "bear_case": "Enforcement risk could suppress DeFi activity."
}
```

Response includes `candidates` sorted by trading relevance score.

### List candidates

```bash
GET /signals/candidates?min_confidence=0.60&limit=20
```

### Historical analogues

```bash
GET /signals/historical/BTC?event_type=regulatory&sentiment=negative
```

---

## CLI Usage

```bash
# Generate and rank signal candidates from sample data
python -m app.cli signals generate

# Filter by confidence
python -m app.cli signals generate --min-confidence 0.60 --top 5

# Find historical analogues for BTC
python -m app.cli signals historical BTC

# Filter analogues by event type
python -m app.cli signals historical ETH --event-type hack_exploit --sentiment negative
```

---

## Example Output

```
┏━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Asset  ┃ Direction  ┃ Confidence ┃ Urgency    ┃ Next Step                                            ┃
┡━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ BTC    │ bullish    │ 85%        │ short_term │ Monitor BTC for follow-through confirmation.          │
│ IBIT   │ bullish    │ 76%        │ short_term │ Monitor IBIT for follow-through confirmation.         │
│ ETH    │ neutral    │ 62%        │ medium_term│ Add ETH to watch list for medium-term thesis.         │
└────────┴────────────┴────────────┴────────────┴──────────────────────────────────────────────────────┘

Note: Signal candidates are for research purposes only. No orders are placed automatically.
```

---

## Important Limitations

1. **Not predictive** — Signal candidates reflect current information, not future price movements
2. **No position sizing** — Candidates carry no recommended trade size
3. **No execution** — The system cannot place orders (by design)
4. **Historical context is illustrative** — Past analogues are background context, not forecasts
5. **Source quality varies** — Always check `source_quality` and `risk_notes` before acting on a signal
