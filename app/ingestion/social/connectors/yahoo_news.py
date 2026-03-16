"""
Yahoo News Connector
=====================
Fetches news from Yahoo Finance/News RSS feeds — no API key required.

RSS endpoints:
  Finance news: https://finance.yahoo.com/rss/
  Search:       https://news.search.yahoo.com/rss?p={query}

Status: ACTIVE (RSS-based, no credentials needed)
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote_plus

from app.core.logging import get_logger
from app.ingestion.social.connectors.base import (
    BaseSocialConnector,
    ConnectorStatus,
    FetchParams,
    SocialPost,
)

logger = get_logger(__name__)

_YAHOO_SEARCH_RSS = "https://news.search.yahoo.com/rss"
_YAHOO_FINANCE_RSS = "https://finance.yahoo.com/rss/"


class YahooNewsConnector(BaseSocialConnector):
    """
    Yahoo News / Yahoo Finance RSS connector — no API key needed.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    @property
    def connector_id(self) -> str:
        return "yahoo_news"

    @property
    def status(self) -> ConnectorStatus:
        return ConnectorStatus.ACTIVE if self._enabled else ConnectorStatus.DISABLED

    @property
    def requires_action(self) -> str:
        return "" if self._enabled else "Set YAHOO_NEWS_ENABLED=true in .env"

    async def fetch(self, params: FetchParams) -> list[SocialPost]:
        if self.status != ConnectorStatus.ACTIVE or not params.query:
            return []

        try:
            import feedparser  # noqa: PLC0415
            import httpx  # noqa: PLC0415

            url = f"{_YAHOO_SEARCH_RSS}?p={quote_plus(params.query)}"
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "AI-Analyst-Bot/0.1"})
                resp.raise_for_status()
                content = resp.text

            feed = feedparser.parse(content)
            posts = []
            for entry in feed.entries[: params.max_results]:
                pub = None
                try:
                    if entry.get("published_parsed"):
                        pub = datetime(*entry.published_parsed[:6])
                except Exception:
                    pass

                posts.append(SocialPost(
                    post_id=entry.get("id", entry.get("link", "")),
                    source_connector="yahoo_news",
                    title=entry.get("title", ""),
                    body=re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:800],
                    url=entry.get("link", ""),
                    author=entry.get("author", ""),
                    published_at=pub,
                    score=0,
                ))

            logger.info("yahoo_news_fetched", count=len(posts), query=params.query)
            return posts

        except Exception as e:
            logger.error("yahoo_news_fetch_error", error=str(e))
            return []
