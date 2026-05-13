# AGENTS.md — app/ingestion/

## Purpose
Everything related to fetching content from external sources.
Classify → Resolve → Fetch → Return `FetchResult`.
No analysis, no storage, no alerting.

## Public Interface

| File | Exports | Notes |
|---|---|---|
| `base/interfaces.py` | `BaseSourceAdapter`, `SourceMetadata`, `FetchResult` | Extend for every new adapter |
| `classifier.py` | `SourceClassifier` | Classifies raw URLs into `SourceType` |
| `rss/adapter.py` | `RSSFeedAdapter` | Fetches RSS feeds via feedparser |
| `resolvers/podcast.py` | `PodcastResolver` | Classifies Apple/Spotify/Podigee URLs |
| `resolvers/youtube.py` | `YouTubeResolver` | Normalizes YouTube channel/video URLs |

## Pipeline Order

```
raw URL → SourceClassifier → resolver (if needed) → adapter.fetch() → FetchResult
```

## Constraints

- Never ingest without classification first
- Never fake an RSS feed — if unresolvable, return `SourceStatus.requires_api` or `unresolved`
- Adapters must not contain analysis logic
- All fetch results return `FetchResult` (never raw dicts)
- HTTP via `httpx` only (async preferred)

## Adding a new adapter

1. Create `app/ingestion/<type>/adapter.py`
2. Extend `BaseSourceAdapter`
3. Add `SourceType` entry in `app/core/enums.py` if new type
4. Add classifier rule in `classifier.py`
5. Write tests in `tests/unit/test_<type>_adapter.py`
6. Update this AGENTS.md

## Tests

```bash
pytest tests/unit/test_rss_adapter.py
pytest tests/unit/test_classifier.py
pytest tests/unit/test_podcast_resolver.py
pytest tests/unit/test_youtube_resolver.py
```
