"""
NewsAPI.org Adapter
===================
Fetches news articles from NewsAPI.org.

[REQUIRES: NEWSAPI_KEY in .env]

NewsAPI offers:
- /v2/top-headlines  — Breaking news by country/category
- /v2/everything    — Full-text search with date range

Documentation: https://newsapi.org/docs

NOTE: This adapter is configuration-dependent. Without NEWSAPI_KEY,
      it is registered with status=REQUIRES_API and will not be scheduled.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.domain.document import CanonicalDocument
from app.core.enums import AuthMode, Language, SourceStatus, SourceType
from app.core.errors import FetchError, RateLimitError
from app.core.logging import get_logger
from app.ingestion.base.interfaces import BaseSourceAdapter, FetchResult, SourceMetadata

logger = get_logger(__name__)

_NEWSAPI_BASE = "https://newsapi.org/v2"
_DEFAULT_PAGE_SIZE = 100  # NewsAPI max


class NewsAPIAdapter(BaseSourceAdapter):
    """
    Adapter for NewsAPI.org — search-based news ingestion.

    [REQUIRES: NEWSAPI_KEY in .env]

    Args:
        api_key:       NewsAPI.org API key
        query:         Search query string (e.g., "bitcoin OR ethereum")
        language:      ISO 639-1 language code (default: "en")
        from_days_ago: How many days back to search (default: 1)
        page_size:     Max results per request (max: 100)
        source_id:     Unique source identifier for this query
    """

    def __init__(
        self,
        api_key: str,
        query: str,
        language: str = "en",
        from_days_ago: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
        source_id: str = "newsapi_everything",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key
        self._query = query
        self._language = language
        self._from_days_ago = from_days_ago
        self._page_size = min(page_size, _DEFAULT_PAGE_SIZE)
        self._source_id = source_id

        self._metadata = SourceMetadata(
            source_id=source_id,
            source_name="NewsAPI.org",
            source_type=SourceType.NEWS_API,
            provider="newsapi",
            auth_mode=AuthMode.API_KEY,
            status=SourceStatus.ACTIVE,
            url=_NEWSAPI_BASE,
            language=language,
            rate_limit_per_minute=100,  # Paid plan; free plan is lower
        )

    @property
    def metadata(self) -> SourceMetadata:
        return self._metadata

    async def _fetch_raw(self) -> dict[str, Any]:
        from_dt = (
            datetime.now(tz=timezone.utc) - timedelta(days=self._from_days_ago)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "q": self._query,
            "language": self._language,
            "from": from_dt,
            "pageSize": self._page_size,
            "sortBy": "publishedAt",
        }
        headers = {"X-Api-Key": self._api_key}

        client = self._http_client or self._build_http_client()
        try:
            response = await client.get(
                f"{_NEWSAPI_BASE}/everything",
                params=params,
                headers=headers,
            )
        except httpx.TimeoutException as e:
            raise FetchError(f"NewsAPI timeout: {e}") from e
        except httpx.RequestError as e:
            raise FetchError(f"NewsAPI request error: {e}") from e

        if response.status_code == 429:
            raise RateLimitError("NewsAPI rate limit exceeded")
        if response.status_code == 401:
            raise FetchError("NewsAPI: Invalid API key (401)", status_code=401)
        if response.status_code >= 400:
            raise FetchError(
                f"NewsAPI error {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        data = response.json()
        if data.get("status") != "ok":
            raise FetchError(
                f"NewsAPI non-ok response: {data.get('message', 'unknown error')}"
            )

        logger.debug(
            "newsapi_fetch_success",
            query=self._query[:60],
            total_results=data.get("totalResults", 0),
            returned=len(data.get("articles", [])),
        )
        return data

    def _normalize(self, raw_data: dict[str, Any]) -> list[CanonicalDocument]:
        articles = raw_data.get("articles", [])
        documents: list[CanonicalDocument] = []

        for article in articles:
            try:
                doc = self._article_to_document(article)
                documents.append(doc)
            except Exception as e:
                logger.warning(
                    "newsapi_article_parse_error",
                    url=article.get("url", "")[:80],
                    error=str(e),
                )

        return documents

    def _article_to_document(self, article: dict[str, Any]) -> CanonicalDocument:
        import hashlib

        url = article.get("url", "") or ""
        title = article.get("title", "") or ""
        description = article.get("description", "") or ""
        content = article.get("content", "") or ""
        author = article.get("author", "") or ""
        source_name = (article.get("source") or {}).get("name", "NewsAPI")

        published_str = article.get("publishedAt", "")
        published_at: datetime | None = None
        if published_str:
            try:
                published_at = datetime.fromisoformat(
                    published_str.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                pass

        raw_text = f"{title}\n\n{description}\n\n{content}".strip()
        content_hash = hashlib.sha256(f"{url}|{title}".encode()).hexdigest()

        try:
            lang = Language(self._language)
        except ValueError:
            lang = Language.UNKNOWN

        return CanonicalDocument(
            external_id=url,
            source_id=self._source_id,
            source_name=source_name,
            source_type=SourceType.NEWS_API,
            provider="newsapi",
            url=url,
            title=title,
            author=author,
            published_at=published_at,
            language=lang,
            raw_text=raw_text,
            cleaned_text=raw_text,
            content_hash=content_hash,
            metadata={
                "newsapi_query": self._query,
                "image_url": article.get("urlToImage", ""),
            },
        )

    async def fetch(self) -> FetchResult:
        """Override to check for API key before attempting fetch."""
        if not self._api_key:
            logger.warning("newsapi_no_api_key", source_id=self._source_id)
            return FetchResult(
                source_id=self._source_id,
                documents=[],
                error="NEWSAPI_KEY not configured — set it in .env",
            )
        return await super().fetch()
