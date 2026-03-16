# Analysis Pipeline

## Overview

The analysis pipeline runs after ingestion. It takes `PENDING` documents and produces
structured analysis results stored in `document_analysis`.

```
[DB: pending documents]
        ↓
  KeywordMatcher.match()          ← rule-based, always runs, zero API cost
        ↓
  NoveltyScorer.score()           ← in-session duplicate/near-duplicate detection
  CredibilityScorer.score()       ← source quality + spam signals
        ↓
  PriorityComposer.classify()     ← pre-LLM priority estimate
        ↓
  keyword_score >= min_llm_score?
  ├─ YES → OpenAIProvider.analyze_document()   [REQUIRES: OPENAI_API_KEY]
  └─ NO  → Rule-based AnalysisResult (low confidence)
        ↓
  DocumentRepository.save_analysis()
  DocumentRepository.mark_analysis_status(COMPLETED)
```

## Running the Pipeline

### CLI (recommended for development)
```bash
# Analyze all pending documents (rule-based only)
trading-bot analyze pending

# With LLM (requires OPENAI_API_KEY in .env)
trading-bot analyze pending --provider openai
```

### Via AnalysisRunner (programmatic)
```python
from app.orchestration.analysis_runner import AnalysisRunner
from app.analysis.keywords.matcher import KeywordMatcher
from app.core.utils.file_loaders import load_keywords, load_entity_aliases

# Load from monitor files
keywords = load_keywords(Path("monitor/keywords.txt"))
alias_groups = load_entity_aliases(Path("monitor/entity_aliases.yml"))
matcher = KeywordMatcher(keywords=keywords, alias_groups=alias_groups)

# Optional LLM provider
from app.integrations.openai.provider import OpenAIProvider
provider = OpenAIProvider(api_key=settings.openai.api_key)

runner = AnalysisRunner(
    session=session,
    matcher=matcher,
    llm_provider=provider,  # or None for rule-based only
    min_llm_score=0.10,
    batch_size=50,
)
stats = await runner.run()
```

## Scoring Modules

### KeywordMatcher (`app/analysis/keywords/matcher.py`)
- Loads from `monitor/keywords.txt`
- Supports alias groups from `monitor/entity_aliases.yml`
- Title hits score 2× higher than body hits (configurable `title_boost`)
- Returns `MatchResult.score` in [0.0, 1.0]

### NoveltyScorer (`app/analysis/scoring/novelty.py`)
- In-session deduplication via content hash and title Jaccard similarity
- `score=1.0`: fully novel; `score=0.0`: exact duplicate
- Threshold configurable (default: 0.70 Jaccard)

### CredibilityScorer (`app/analysis/scoring/credibility.py`)
- Base: source credibility from `SourceRegistry` (loaded from `monitor/news_domains.txt`)
- Spam penalty: clickbait patterns, ALL_CAPS, excessive punctuation
- Title quality: length, casing
- Returns score in [0.0, 1.0]

### PriorityComposer (`app/analysis/scoring/priority.py`)
- Combines all scores with configurable weights
- Maps composite score → `DocumentPriority`

| Score range | Priority  |
|-------------|-----------|
| ≥ 0.80      | CRITICAL  |
| ≥ 0.60      | HIGH      |
| ≥ 0.40      | MEDIUM    |
| ≥ 0.20      | LOW       |
| < 0.20      | NOISE     |

Default weights:
```
keyword:     30%
relevance:   20%
impact:      20%
recency:     15%
credibility: 10%
novelty:      5%
```

## Historical Events

Historical event records (`app/storage/models/historical.py`) are the foundation for:
- "Current event resembles X" comparisons in LLM prompts
- Outcome tracking (price impact, recovery time)
- Similarity scoring for alert enrichment

Seed data is added manually or via future data import scripts.
The `compare_to_historical()` method in `BaseAnalysisProvider` uses these as input.

## API Endpoints

| Endpoint                        | Description                          |
|---------------------------------|--------------------------------------|
| `GET /analysis/pending`         | List documents awaiting analysis     |
| `GET /analysis/stats`           | Coverage and cost statistics         |
| `POST /documents/search`        | Search with QuerySpec + Boolean DSL  |
| `GET /documents/{id}/analysis`  | Fetch analysis result for a document |
