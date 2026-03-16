"""
Bing News Connector
====================
Fetches news via Bing News Search API v7.
[REQUIRES: BING_SEARCH_API_KEY in .env]

Free tier: 1,000 transactions/month (F0).
Paid: S1 = 3 transactions/second.

API docs: https://docs.microsoft.com/en-us/bing/search-apis/bing-news-search/
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.ingestion.social.connectors.base import (
    BaseSocialConnector,
    ConnectorStatus,
    FetchParams,
    SocialPost,
)

logger = get_logger(__name__)

_BING_API = "https://api.bing.microsoft.com/v7.0/news/search"


class BingNewsConnector(BaseSocialConnector):
    """
    Bing News Search API connector.
    [REQUIRES: BING_SEARCH_API_KEY in .env]
    """

    def __init__(self, api_key: str = "", enabled: bool = True) -> None:
        self._api_key = api_key
        self._enabled = enabled

    @property
    def connector_id(self) -> str:
        return "bing_news"

    @property
    def status(self) -> ConnectorStatus:
        if not self._enabled:
            return ConnectorStatus.DISABLED
        if not self._api_key:
            return ConnectorStatus.REQUIRES_API
        return ConnectorStatus.ACTIVE

    @property
    def requires_action(self) -> str:
        if not self._enabled:
            return "Set BING_NEWS_ENABLED=true in .env"
        if not self._api_key:
            return (
                "Set BING_SEARCH_API_KEY in .env. "
                "Get a free key at https://azure.microsoft.com/en-us/services/cognitive-services/bing-news-search-api/"
            )
        return ""

    async def fetch(self, params: FetchParams) -> list[SocialPost]:
        if self.status != ConnectorStatus.ACTIVE or not params.query:
            return []

        try:
            import httpx  # noqa: PLC0415
            request_params: dict[str, Any] = {
                "q": params.query,
                "count": min(params.max_results, 100),
                "freshness": self._freshness(params.time_filter),
                "mkt": "en-US",
                "sortBy": "Date" if params.sort == "date" else "Relevance",
            }
            headers = {
                "Ocp-Apim-Subscription-Key": self._api_key,
                "User-Agent": "AI-Analyst-Bot/0.1",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(_BING_API, params=request_params, headers=headers)
                if resp.status_code == 429:
                    logger.warning("bing_news_rate_limited")
                    return []
                resp.raise_for_status()
                data = resp.json()

            posts = []
            for article in data.get("value", []):
                pub = None
                raw_date = article.get("datePublished", "")
                try:
                    pub = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

                provider = ""
                if article.get("provider"):
                    provider = article["provider"][0].get("name", "")

                posts.append(SocialPost(
                    post_id=article.get("url", ""),
                    source_connector="bing_news",
                    title=article.get("name", ""),
                    body=article.get("description", "")[:800],
                    url=article.get("url", ""),
                    author=provider,
                    published_at=pub,
                    score=0,
                    metadata={"provider": provider},
                ))

            logger.info("bing_news_fetched", count=len(posts), query=params.query)
            return posts

        except Exception as e:
            logger.error("bing_news_fetch_error", error=str(e))
            return []

    @staticmethod
    def _freshness(time_filter: str) -> str:
        return {
            "hour": "Hour",
            "day": "Day",
            "week": "Week",
            "month": "Month",
        }.get(time_filter, "Day")
