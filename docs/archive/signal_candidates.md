# Signal Candidates

A **SignalCandidate** is a structured, research-grade output that summarises the evidence for a
potential trading opportunity.

> ⚠ **Signal Candidates are NOT trade orders.**
> They are research artifacts for informed human decision-making.
> No positions are sized or executed automatically.
> The system has no broker integration and cannot place orders.

---

## Implementation Status

| Section | Status |
|---------|--------|
| Core model (`SignalCandidate`) | ✅ Sprint 4A — implemented |
| `extract_signal_candidates()` | ✅ Sprint 4A — implemented |
| Watchlist boost priority | ✅ Sprint 4A — implemented |
| CLI `research signals` | ✅ Sprint 4A — implemented |
| REST API endpoint `GET /research/signals` | ✅ Sprint 4B — implemented |
| Provider-independent signal path | ✅ Sprint 4C — operational |
| Extended model fields (urgency, narrative) | ⏳ Sprint 4B — planned |

---

## Current Model (Sprint 4A)

Defined in `app/research/signals.py`. Produced by `extract_signal_candidates()`.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `signal_id` | str | Unique identifier (`sig_<document_uuid>`) |
| `document_id` | str | Source `CanonicalDocument.id` — traceability |
| `target_asset` | str | Primary asset symbol (e.g. `"BTC"`, `"ETH"`) |
| `direction_hint` | str | `"bullish"` / `"bearish"` / `"neutral"` |
| `confidence` | float (0–1) | Proxied from `relevance_score` (LLM `confidence_score` is not persisted to DB) |
| `supporting_evidence` | str | Document summary or title — bull-case context |
| `contradicting_evidence` | str | Static placeholder in Sprint 4A |
| `risk_notes` | str | `spam_prob=<x> scope=<y>` from document metadata |
| `source_quality` | float (0–1) | Proxied from `credibility_score` |
| `recommended_next_step` | str | Plain-language research action (no execution language) |
| `priority` | int (8–10) | Effective priority after watchlist boost; enforced by `Field(ge=8, le=10)` |
| `sentiment` | SentimentLabel | `BULLISH` / `BEARISH` / `NEUTRAL` |
| `affected_assets` | list[str] | All tickers + crypto_assets from the source document |
| `market_scope` | MarketScope | Market scope from source document |
| `published_at` | datetime \| None | Source document publication timestamp |
| `extracted_at` | datetime | Extraction timestamp (UTC, auto-set) |

### Direction Hints

| Value | Meaning |
|-------|---------|
| `bullish` | `SentimentLabel.BULLISH` on source document |
| `bearish` | `SentimentLabel.BEARISH` on source document |
| `neutral` | All other cases (including `NEUTRAL`, `MIXED`, or unset) |

`"buy"`, `"sell"`, `"hold"`, `"mixed"` are **never valid** — see R-3 in `app/research/AGENTS.md`.

### Confidence (Sprint 4A proxy)

In Sprint 4A, `confidence` is a proxy for `relevance_score`:

```
confidence = document.relevance_score or 0.5
```

`AnalysisResult.confidence_score` is NOT persisted to DB (Invariant I-11 in `docs/contracts.md`).
The DB stores `credibility_score = 1.0 - spam_probability`, which is used as `source_quality`.

### Watchlist Boost

`extract_signal_candidates()` accepts `watchlist_boosts: dict[str, int] | None`:

```python
# Boost assets on watchlist by +2 priority points
boosts = {"BTC": 2, "ETH": 1}
candidates = extract_signal_candidates(docs, min_priority=8, watchlist_boosts=boosts)
```

The boost is applied to effective priority only — it never modifies the underlying document score.
Documents with `effective_priority < min_priority` are silently dropped.

### CLI Usage (Sprint 4A)

```bash
# Extract signals from the last 100 analyzed documents
python -m app.cli research signals

# Custom threshold and limit
python -m app.cli research signals --min-priority 9 --limit 200

# Boost a watchlist
python -m app.cli research signals --watchlist defi
```

---

## Extraction Contract

```python
from app.research.signals import extract_signal_candidates

candidates = extract_signal_candidates(
    documents,          # list[CanonicalDocument] — only is_analyzed=True are used
    min_priority=8,     # default — documents below this are dropped
    watchlist_boosts=None,  # optional dict[str, int]
)
# Returns: list[SignalCandidate] sorted by priority (highest first)
```

**Invariants enforced by `extract_signal_candidates()`:**
- Only `is_analyzed=True` documents enter the pipeline
- `effective_priority = min(10, base_priority + max_boost)`
- `direction_hint` is always one of `"bullish"`, `"bearish"`, `"neutral"`
- `document_id = str(doc.id)` — never null
- No DB reads or writes

---

## Provider-Independent Signal Path

Signal extraction stays provider-agnostic at the document boundary.

All supported analysis sources feed the same downstream path:
- deterministic fallback analysis
- future internal companion analysis
- external provider analysis

Shared rule:
- `extract_signal_candidates()` only consumes analyzed `CanonicalDocument` objects and does not
  branch on provider family

This keeps one signal path:

```
analysis layer -> AnalysisResult -> apply_to_document() -> CanonicalDocument
               -> extract_signal_candidates() -> SignalCandidate
```

The future companion layer may add `signal preclassification`, but only as an upstream hint.
It must not replace or bypass the existing threshold gate inside `extract_signal_candidates()`.

---

## Example Output

```
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━┳━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Signal       ┃ Direction    ┃ Pri ┃ Asset    ┃ Conf ┃ Evidence                                                   ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━╇━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ sig_abc123   │ BULLISH      │ 9   │ BTC      │ 0.87 │ Bitcoin ETF sees record inflows as institutional...        │
│ sig_def456   │ BEARISH      │ 8   │ ETH      │ 0.71 │ SEC opens investigation into major DeFi protocol...        │
│ sig_ghi789   │ NEUTRAL      │ 8   │ SOL      │ 0.62 │ Solana foundation announces new developer grants...        │
└──────────────┴──────────────┴─────┴──────────┴──────┴────────────────────────────────────────────────────────────┘

Note: Signal candidates are for research purposes only. No orders are placed automatically.
```

---

## Sprint 4B Extensions (Planned)

The following fields and features are **not yet implemented**. They are planned for Sprint 4B.

### Extended Fields (planned)

| Field | Type | Description |
|-------|------|-------------|
| `narrative_label` | enum | Thematic classification (regulatory_risk, institutional_adoption, …) |
| `urgency` | enum | Time horizon: immediate / short_term / medium_term / long_term / monitor |
| `severity` | enum | Based on priority level |
| `historical_context` | str \| None | Similar past event reference (from HistoricalEvent matching) |
| `title` | str | Source document title (denormalized for convenience) |
| `url` | str | Source document URL (denormalized for convenience) |

### Narrative Labels (planned)

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

### Confidence Formula (Sprint 4B)

Sprint 4B will compute a proper composite confidence score:

```
confidence = asset_mapping_confidence × impact_score × 0.5
           + asset_mapping_confidence × 0.5
```

Until then, `relevance_score` is used as a proxy.

### REST API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/research/signals` | List signal candidates, optionally filtered or watchlist-boosted |
| `GET` | `/research/signals/{asset}` | Planned convenience route for one asset |

---

## Fallback Compatibility

### Context

When no external provider is configured, the provider is disabled, or the provider call fails,
the system degrades to deterministic fallback analysis and still writes a valid analyzed document.

### Signal Eligibility

Fallback-analyzed documents use the same threshold gate as every other analyzed document.

Because fallback analysis is conservative, those documents are typically less likely to cross the
signal threshold. That is expected. The important contract is:
- no special-case bypass for fallback results
- no second signal engine
- no silent disappearance from the analyzed-document pipeline

### Watchlist Boost and Fallback

Watchlist boosts are applied to effective priority, not to the underlying document score.
If a fallback-analyzed document crosses threshold after normal scoring and an explicit boost,
it is still processed through the same canonical signal path.

### Fallback `direction_hint`

Fallback analysis is expected to produce more conservative sentiment outputs than richer provider
paths. If a fallback-analyzed document crosses threshold, `direction_hint` is still derived from
the document's normalized sentiment fields in the same way as every other candidate.

### Research Briefs with Fallback Docs

Fallback-analyzed documents can appear in Research Briefs and may feed the signal extractor.
In practice they are expected to populate lower-confidence research more often than high-priority
signals, but that is an outcome of shared scoring rather than a separate fallback-only rule.

### Identifying a Fallback Document

A fallback-origin result should remain identifiable through conservative explanations and
pipeline provenance, without requiring a separate signal schema. See
[docs/intelligence_architecture.md](./intelligence_architecture.md) for the longer-term
fallback / companion / external layering model.

---

## Important Limitations

1. **Not predictive** — Signal candidates reflect current information, not future price movements
2. **No position sizing** — Candidates carry no recommended trade size
3. **No execution** — The system cannot place orders, has no broker integration, and will not gain it before Phase 11
4. **Historical context is illustrative** — Past analogues are background context, not forecasts (Sprint 4B)
5. **Source quality varies** — Always check `source_quality` and `risk_notes` before acting on a signal
6. **Confidence is a proxy** — In Sprint 4A, confidence equals `relevance_score`, not a composite score
