# Social Connectors Reference

Social connectors ingest posts, articles, and news from external platforms. They share a unified interface (`BaseSocialConnector`) and are managed via `SocialConnectorRegistry`.

---

## Architecture

```
SocialConnectorRegistry
  ├── RedditConnector         [REQUIRES: API credentials]
  ├── TwitterConnector        [REQUIRES: Bearer Token]
  ├── GoogleNewsConnector     [Active, no key needed]
  ├── YahooNewsConnector      [Active, no key needed]
  ├── BingNewsConnector       [REQUIRES: Bing Search API key]
  └── FacebookConnector       [Planned — requires Meta app approval]
```

All connectors produce `SocialPost` objects with a unified schema and are invoked in parallel via `asyncio.gather`.

---

## ConnectorStatus Values

| Status | Meaning |
|--------|---------|
| `active` | Connector is enabled and has required credentials |
| `requires_api` | Missing required API key or credentials |
| `disabled` | Explicitly disabled via config |
| `planned` | Not yet implemented or requires external approval |
| `rate_limited` | Temporarily paused due to rate limits |
| `error` | Runtime error on last attempt |

---

## Connector Details

### GoogleNewsConnector
- **Status**: Active (no API key needed)
- **Source**: RSS feed from `news.google.com/rss/search`
- **Config**: `GOOGLE_NEWS_ENABLED=true` (default: true)
- **Notes**: Strips ` - Source Name` suffix from titles. Free, read-only.

### YahooNewsConnector
- **Status**: Active (no API key needed)
- **Source**: RSS feed from `news.search.yahoo.com/rss`
- **Config**: `YAHOO_NEWS_ENABLED=true` (default: true)
- **Notes**: Good for financial news. Free, read-only.

### RedditConnector
- **Status**: `requires_api` without credentials
- **Auth**: OAuth2 client credentials flow
- **Config**:
  ```env
  REDDIT_CLIENT_ID=your_client_id
  REDDIT_CLIENT_SECRET=your_client_secret
  REDDIT_ENABLED=true
  ```
- **Notes**: Searches across subreddits (default: `r/CryptoCurrency`, `r/Bitcoin`, `r/investing`). Returns posts with upvote ratio in metadata.

### TwitterConnector
- **Status**: `requires_api` without credentials
- **API**: Twitter API v2 — `GET /2/tweets/search/recent`
- **Config**:
  ```env
  TWITTER_BEARER_TOKEN=your_bearer_token
  TWITTER_ENABLED=true
  ```
- **Notes**: Automatically appends `-is:retweet lang:en` to queries. Handles 429 (rate limit) and 403 (forbidden) explicitly.

### BingNewsConnector
- **Status**: `requires_api` without key
- **API**: Bing News Search API v7
- **Config**:
  ```env
  BING_SEARCH_API_KEY=your_key
  BING_NEWS_ENABLED=true
  ```
- **Notes**: Supports `time_filter` → Bing `freshness` mapping (`day`, `week`, `month`). High quality results.

### FacebookConnector
- **Status**: `planned` — always disabled
- **Reason**: Requires approved Meta app and Page Access Token
- **Config**: N/A — fetch() returns `[]` with a warning log
- **Notes**: Placeholder for future integration. [REQUIRES: Meta app review + `FACEBOOK_PAGE_ACCESS_TOKEN` + `FACEBOOK_PAGE_ID`]

---

## SocialPost Schema

Every connector returns `SocialPost` objects:

```python
@dataclass
class SocialPost:
    post_id: str
    connector_id: str
    title: str
    url: str = ""
    body: str = ""
    author: str = ""
    source_name: str = ""
    published_at: datetime | None = None
    score: int = 0              # upvotes, likes, engagement
    metadata: dict = field(default_factory=dict)
```

---

## FetchParams

```python
@dataclass
class FetchParams:
    query: str
    subreddit: str | None = None      # Reddit-specific
    max_results: int = 10
    sort: str = "relevance"           # relevance | hot | new | top
    time_filter: str = "day"          # day | week | month
```

---

## Usage

### Registry-level (parallel fetch from all active connectors)

```python
from app.ingestion.social.registry import SocialConnectorRegistry
from app.ingestion.social.connectors.base import FetchParams

registry = SocialConnectorRegistry.default()
params = FetchParams(query="Bitcoin ETF", max_results=20)
posts = await registry.fetch_all(params)

for post in posts:
    print(post.connector_id, post.title, post.score)
```

### Single connector

```python
posts = await registry.fetch_from("google_news", params)
```

### Status report

```python
report = registry.status_report()
# [{"connector_id": "reddit", "status": "requires_api", ...}, ...]
```

### With custom environment

```python
import os
os.environ["REDDIT_CLIENT_ID"] = "your_id"
os.environ["REDDIT_CLIENT_SECRET"] = "your_secret"
os.environ["REDDIT_ENABLED"] = "true"

registry = SocialConnectorRegistry.from_settings(None)
```

---

## Adding a New Connector

1. Create `app/ingestion/social/connectors/myplatform.py`
2. Subclass `BaseSocialConnector`
3. Implement `connector_id`, `status`, `requires_action`, `fetch()`
4. Register in `SocialConnectorRegistry.from_settings()`

```python
class MyPlatformConnector(BaseSocialConnector):
    def __init__(self, api_key: str, enabled: bool = False):
        self._api_key = api_key
        self._enabled = enabled

    @property
    def connector_id(self) -> str:
        return "myplatform"

    @property
    def status(self) -> ConnectorStatus:
        if not self._enabled:
            return ConnectorStatus.DISABLED
        if not self._api_key:
            return ConnectorStatus.REQUIRES_API
        return ConnectorStatus.ACTIVE

    @property
    def requires_action(self) -> str:
        return "Set MYPLATFORM_API_KEY" if not self._api_key else ""

    async def fetch(self, params: FetchParams) -> list[SocialPost]:
        if self.status != ConnectorStatus.ACTIVE:
            return []
        # ... implementation
```

---

## Rate Limits and Retry

Connectors handle rate limits internally:
- `TwitterConnector`: HTTP 429 → sets `status = RATE_LIMITED`, returns `[]`
- `RedditConnector`: exponential backoff via `tenacity` on 429
- RSS connectors: no rate limits (public RSS feeds)

For production use, wrap registry calls with your own retry/cache layer.
