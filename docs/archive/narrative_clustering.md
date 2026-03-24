# Narrative Clustering Reference

Narrative clustering groups `SignalCandidate` objects into thematic clusters based on shared assets, entities, and narrative labels. No ML required — purely rule-based using Jaccard similarity.

---

## Architecture

```
[SignalCandidates]
        │
        ▼
NarrativeClusterEngine.cluster()
        │
        ├── Group by NarrativeLabel
        │
        ├── Sub-cluster by asset/entity overlap (Jaccard ≥ 0.30)
        │
        ├── Compute temporal metrics (velocity, acceleration)
        │
        └── [NarrativeCluster list, sorted by doc_count desc]
```

Optional post-processing:
```
NarrativeClusterEngine.merge_clusters()
    → Merges clusters with ≥60% asset overlap across different labels
    → Used for cross-source narrative deduplication
```

---

## NarrativeCluster Fields

```python
@dataclass
class NarrativeCluster:
    cluster_id: str           # e.g. "nc-0001"
    label: NarrativeLabel     # Primary narrative theme
    title: str                # Title from highest-impact candidate
    assets: list[str]         # Unique assets in cluster (max 10)
    entities: list[str]       # Matched entities (persons, orgs) (max 10)
    sources: list[str]        # Unique source IDs (max 10)
    candidate_ids: list[str]  # IDs of contributing SignalCandidates
    doc_count: int            # Number of documents in cluster

    # Temporal
    first_seen: datetime | None
    last_seen: datetime | None
    velocity: float           # Documents per hour in last 24h

    # Signal aggregates
    dominant_direction: DirectionHint  # BULLISH | BEARISH | NEUTRAL | MIXED
    avg_confidence: float
    max_impact: float
    is_accelerating: bool     # True if last 6h > prior 6h AND last_6h >= 2

    # Cross-source
    is_cross_source: bool     # True if multiple distinct source_ids
```

---

## Narrative Labels

Labels are defined in `app/core/enums.py` as `NarrativeLabel`:

| Value | Description |
|-------|-------------|
| `institutional_adoption` | Institutional buying, ETF approvals, corporate treasury |
| `regulatory_risk` | SEC actions, bans, legal proceedings |
| `tech_upgrade` | Protocol upgrades, forks, network improvements |
| `macro_shift` | Fed policy, inflation, macro-economic changes |
| `market_crash` | Exchange failures, contagion, sharp selloffs |
| `liquidity_crisis` | Stablecoin depegs, credit crises, liquidity events |
| `hack_exploit` | Security breaches, DeFi exploits |
| `ecosystem_growth` | Adoption growth, developer activity, TVL increase |
| `whale_activity` | Large wallet movements, OTC deals |
| `unknown` | Unclassified signals |

---

## Clustering Algorithm

### Step 1: Group by Label
All candidates sharing the same `NarrativeLabel` are put in the same label group.

### Step 2: Sub-cluster by Asset/Entity Overlap (Jaccard)
Within each label group, candidates are merged if either:
- Asset Jaccard similarity ≥ `merge_threshold` (default: 0.30)
- Entity Jaccard similarity ≥ `merge_threshold`

```
Jaccard(A, B) = |A ∩ B| / |A ∪ B|
```

### Step 3: Filter by Size
Clusters with fewer than `min_cluster_size` (default: 2) documents are dropped, **unless** they contain a candidate with urgency `immediate` or `short_term`.

### Step 4: Compute Metrics
- **velocity**: `count of docs in last 24h / 24.0` (docs per hour)
- **is_accelerating**: `last_6h_count > prev_6h_count AND last_6h_count >= 2`
- **dominant_direction**: Most common `DirectionHint` value (Counter.most_common)
- **is_cross_source**: `len(distinct source_ids) > 1`

### Step 5: Sort
Returned sorted by `(doc_count, avg_confidence)` descending.

---

## ClusterConfig

```python
@dataclass
class ClusterConfig:
    min_cluster_size: int = 2           # Min docs to form a cluster
    merge_threshold: float = 0.30       # Jaccard threshold for sub-clustering
    acceleration_window_hours: int = 6  # Window for acceleration check
    max_clusters: int = 20              # Hard cap on returned clusters
```

---

## Historical Pattern Enrichment

Clusters can be enriched with historical context via `PatternEnricher`:

```python
from app.analysis.historical.pattern import PatternEnricher

enricher = PatternEnricher()
enrichment = enricher.enrich_cluster(
    label=cluster.label,
    assets=cluster.assets,
    tags=cluster.entities,
)

print(enrichment.matching_family.family_name)   # e.g. "exchange_collapse"
print(enrichment.typical_reaction.direction)    # "bearish"
print(enrichment.confidence)                    # 0.0 – 1.0
```

### PatternEnrichment Structure

```python
@dataclass
class PatternEnrichment:
    narrative_label: NarrativeLabel
    matching_family: EventFamily | None
    typical_reaction: TypicalReaction | None
    analogues: list[HistoricalAnalogue]
    confidence: float           # Additive: family=0.35, reaction=0.35, analogues=up to 0.30
    enrichment_note: str
```

### EventFamily Examples

| Family | Narrative | Example Events |
|--------|-----------|----------------|
| `exchange_collapse` | MARKET_CRASH | FTX (2022), Mt.Gox (2014) |
| `btc_institutional_adoption` | INSTITUTIONAL_ADOPTION | BTC ETF approval (2024) |
| `stablecoin_crisis` | LIQUIDITY_CRISIS | Terra/Luna (2022) |
| `bitcoin_halving_cycle` | ECOSYSTEM_GROWTH | BTC Halving (2024) |
| `crypto_regulatory_action` | REGULATORY_RISK | SEC vs Ripple |
| `macro_rate_cycle` | MACRO_SHIFT | Fed rate hikes (2022) |
| `protocol_upgrade` | TECH_UPGRADE | ETH Merge (2022) |

### Typical Reactions (REACTION_ARCHIVE)

| Event Type | Narrative | Direction | Typical Move | Duration |
|------------|-----------|-----------|-------------|----------|
| regulatory | regulatory_risk | bearish | -15% | 30 days |
| regulatory | institutional_adoption | bullish | +15% | 14 days |
| legal | market_crash | bearish | -25% | 90 days |
| market_manipulation | liquidity_crisis | bearish | -50% | 180 days |
| fork_upgrade | tech_upgrade | mixed | — | 30 days |
| macro_economic | macro_shift | bearish | -20% | 365 days |
| hack_exploit | hack_exploit | bearish | -10% | 14 days |

---

## Usage Examples

### Basic Clustering

```python
from app.analysis.narratives.cluster import NarrativeClusterEngine
from app.research.router_helpers import get_sample_candidates

engine = NarrativeClusterEngine()
candidates = get_sample_candidates()
clusters = engine.cluster(candidates)

for cluster in clusters:
    print(f"{cluster.label.value}: {cluster.doc_count} docs, "
          f"velocity={cluster.velocity:.2f}/h, "
          f"accelerating={cluster.is_accelerating}")
```

### With Cross-source Merging

```python
clusters = engine.cluster(candidates)
merged = engine.merge_clusters(clusters)
```

### Custom Config

```python
from app.analysis.narratives.cluster import ClusterConfig

config = ClusterConfig(
    min_cluster_size=1,
    merge_threshold=0.50,
    max_clusters=10,
)
engine = NarrativeClusterEngine(config=config)
```

### With Pattern Enrichment

```python
from app.analysis.historical.pattern import PatternEnricher

enricher = PatternEnricher()
clusters = engine.cluster(candidates)

for cluster in clusters:
    enrichment = enricher.enrich_cluster(
        label=cluster.label,
        assets=cluster.assets,
    )
    print(f"  Family: {enrichment.matching_family.family_name if enrichment.matching_family else 'unknown'}")
    print(f"  Reaction: {enrichment.typical_reaction.direction if enrichment.typical_reaction else 'unknown'}")
    print(f"  Confidence: {enrichment.confidence:.2f}")
```

---

## Interpretation Notes

- **Velocity** is a rough indicator of narrative momentum, not a prediction.
- **is_accelerating** is a simple heuristic — it can trigger on small samples.
- **PatternEnrichment confidence** reflects historical data coverage, not predictive accuracy.
- All analogues and typical reactions include explicit caveats and must not be treated as predictions.
- Historical patterns are contextual background, not trading signals.
