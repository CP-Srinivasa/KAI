"""
Google News Connector
======================
Fetches news from Google News RSS feeds — no API key required.

Google News provides topic/keyword RSS feeds at:
  https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en

Rate limit: be conservative; Google may block aggressive scraping.
Recommended: max 1 request / 5 seconds.

Status: ACTIVE (no API key needed — RSS-based)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from app.core.logging import get_logger
from app.ingestion.social.connectors.base import (
    BaseSocialConnector,
    ConnectorStatus,
    FetchParams,
    SocialPost,
)

logger = get_logger(__name__)

_GNEWS_RSS = "https://news.google.com/rss/search"


class GoogleNewsConnector(BaseSocialConnector):
    """
    Google News RSS connector — no API key needed.

    Fetches recent news articles matching a search query via RSS.
    Limited to ~10–20 results per request (Google limit).
    """

    def __init__(self, enabled: bool = True, language: str = "en", country: str = "US") -> None:
        self._enabled = enabled
        self._language = language
        self._country = country

    @property
    def connector_id(self) -> str:
        return "google_news"

    @property
    def status(self) -> ConnectorStatus:
        return ConnectorStatus.ACTIVE if self._enabled else ConnectorStatus.DISABLED

    @property
    def requires_action(self) -> str:
        return "" if self._enabled else "Set GOOGLE_NEWS_ENABLED=true in .env"

    async def fetch(self, params: FetchParams) -> list[SocialPost]:
        if self.status != ConnectorStatus.ACTIVE or not params.query:
            return []

        try:
            import feedparser  # noqa: PLC0415
            import httpx  # noqa: PLC0415

            url = (
                f"{_GNEWS_RSS}?q={quote_plus(params.query)}"
                f"&hl={self._language}&gl={self._country}"
                f"&ceid={self._country}:{self._language}"
            )
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

                # Google News wraps source in title: "Title - Source"
                title = entry.get("title", "")
                source_hint = ""
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    source_hint = parts[1].strip()

                posts.append(SocialPost(
                    post_id=entry.get("id", entry.get("link", "")),
                    source_connector="google_news",
                    title=title,
                    body=re.sub(r"<[^>]+>", "", entry.get("summary", "")),
                    url=entry.get("link", ""),
                    author=source_hint,
                    published_at=pub,
                    score=0,
                    metadata={"source_outlet": source_hint},
                ))

            logger.info("google_news_fetched", count=len(posts), query=params.query)
            return posts

        except Exception as e:
            logger.error("google_news_fetch_error", error=str(e))
            return []
