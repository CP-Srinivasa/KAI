"""Newsdata.io API client.

API docs: https://newsdata.io/documentation
Endpoint: GET /api/1/latest  (latest news headlines)
Auth:     apikey query param (free tier: up to 10 results / request)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

_BASE_URL = "https://newsdata.io/api/1"
_DEFAULT_TIMEOUT = 20


@dataclass(frozen=True)
class NewsdataArticle:
    article_id: str
    title: str
    link: str
    published_at: datetime
    source_id: str
    source_url: str
    language: str
    description: str | None = None
    content: str | None = None
    creator: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    country: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    source_priority: int = 0


class NewsdataClient:
    """Minimal async client for the Newsdata.io /api/1/latest endpoint.

    Args:
        api_key: Newsdata.io API key.
        timeout: HTTP timeout in seconds.
    """

    def __init__(self, api_key: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def fetch_latest(
        self,
        *,
        q: str | None = None,
        language: str | None = "en",
        country: str | None = None,
        category: str | None = None,
        size: int = 10,
        page: str | None = None,
    ) -> list[NewsdataArticle]:
        """Fetch latest news articles from Newsdata.io.

        Returns an empty list on HTTP errors (non-2xx) to keep the caller resilient.

        Args:
            q:        Optional search query.
            language: Comma-separated language codes (e.g. "en,de").
            country:  Comma-separated country codes (e.g. "us,gb").
            category: Comma-separated categories ("business","technology","top", …).
            size:     Number of results (1–10 free tier, up to 50 paid tier).
            page:     Pagination token from the previous response (nextPage field).
        """
        params: dict[str, Any] = {
            "apikey": self._api_key,
            "size": size,
        }
        if q:
            params["q"] = q
        if language:
            params["language"] = language
        if country:
            params["country"] = country
        if category:
            params["category"] = category
        if page:
            params["page"] = page

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{_BASE_URL}/latest", params=params)
            response.raise_for_status()
            data = response.json()

        return [self._parse_article(a) for a in data.get("results", [])]

    def _parse_article(self, raw: dict[str, Any]) -> NewsdataArticle:
        pub_raw = raw.get("pubDate") or ""
        try:
            published_at = datetime.fromisoformat(pub_raw.replace(" ", "T"))
        except (ValueError, AttributeError):
            published_at = datetime.now(UTC)

        return NewsdataArticle(
            article_id=raw.get("article_id") or "",
            title=raw.get("title") or "",
            link=raw.get("link") or "",
            published_at=published_at,
            source_id=raw.get("source_id") or "",
            source_url=raw.get("source_url") or "",
            language=raw.get("language") or "en",
            description=raw.get("description") or None,
            content=raw.get("content") or None,
            creator=raw.get("creator") or [],
            category=raw.get("category") or [],
            country=raw.get("country") or [],
            keywords=raw.get("keywords") or [],
            source_priority=int(raw.get("source_priority") or 0),
        )
