"""
RSS Feed Adapter
================
Fetches and normalizes RSS/Atom feeds using feedparser.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx

from app.core.domain.document import CanonicalDocument
from app.core.enums import AuthMode, Language, SourceStatus, SourceType
from app.core.errors import FetchError, ParseError
from app.core.logging import get_logger
from app.ingestion.base.interfaces import BaseSourceAdapter, SourceMetadata

logger = get_logger(__name__)


def _parse_date(date_struct: Any) -> datetime | None:
    if not date_struct:
        return None
    try:
        import time
        return datetime.fromtimestamp(time.mktime(date_struct), tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _strip_html(text: str) -> str:
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self._parts.append(data)

        def get_text(self) -> str:
            import re
            return re.sub(r"\s+", " ", "".join(self._parts)).strip()

    s = _Stripper()
    s.feed(text)
    return s.get_text()


class RSSFeedAdapter(BaseSourceAdapter):
    """
    Adapter for RSS/Atom feeds.
    Does NOT support authenticated paywalled or JS-rendered feeds.
    """

    def __init__(
        self,
        source_id: str,
        feed_url: str,
        source_name: str,
        language: str = "en",
        country: str = "",
        categories: list[str] | None = None,
        credibility_score: float = 0.5,
        max_items: int = 50,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._source_id = source_id
        self._feed_url = feed_url
        self._source_name = source_name
        self._language = language
        self._country = country
        self._categories = categories or []
        self._credibility_score = credibility_score
        self._max_items = max_items
        self._metadata = SourceMetadata(
            source_id=source_id,
            source_name=source_name,
            source_type=SourceType.RSS_FEED,
            provider="rss",
            auth_mode=AuthMode.NONE,
            status=SourceStatus.ACTIVE,
            url=feed_url,
            language=language,
            country=country,
            categories=self._categories,
        )

    @property
    def metadata(self) -> SourceMetadata:
        return self._metadata

    async def _fetch_raw(self) -> str:
        client = self._http_client or self._build_http_client()
        try:
            response = await client.get(self._feed_url)
            if response.status_code == 429:
                from app.core.errors import RateLimitError
                raise RateLimitError(f"Rate limited: {self._feed_url}")
            if response.status_code >= 400:
                raise FetchError(
                    f"HTTP {response.status_code} for {self._feed_url}",
                    status_code=response.status_code,
                )
            return response.text
        except httpx.TimeoutException as e:
            raise FetchError(f"Timeout: {self._feed_url}") from e
        except httpx.RequestError as e:
            raise FetchError(f"Request error: {self._feed_url}: {e}") from e

    def _normalize(self, raw_data: str) -> list[CanonicalDocument]:
        try:
            feed = feedparser.parse(raw_data)
        except Exception as e:
            raise ParseError(f"Failed to parse RSS feed: {e}") from e

        documents: list[CanonicalDocument] = []
        for entry in feed.entries[: self._max_items]:
            try:
                documents.append(self._entry_to_document(entry))
            except Exception as e:
                logger.warning("rss_entry_parse_error", source_id=self._source_id, error=str(e))
        return documents

    def _entry_to_document(self, entry: Any) -> CanonicalDocument:
        raw_content = ""
        if hasattr(entry, "content") and entry.content:
            raw_content = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            raw_content = entry.summary or ""

        title = getattr(entry, "title", "") or ""
        url = getattr(entry, "link", "") or ""
        published_at = _parse_date(getattr(entry, "published_parsed", None))

        lang_val = self._language
        try:
            lang = Language(lang_val)
        except ValueError:
            lang = Language.UNKNOWN

        return CanonicalDocument(
            external_id=getattr(entry, "id", "") or url,
            source_id=self._source_id,
            source_name=self._source_name,
            source_type=SourceType.RSS_FEED,
            provider="rss",
            url=url,
            title=title,
            author=getattr(entry, "author", "") or "",
            published_at=published_at,
            language=lang,
            country=self._country,
            categories=self._categories.copy(),
            raw_text=raw_content,
            cleaned_text=_strip_html(raw_content),
            content_hash=hashlib.sha256(f"{url}|{title}|{published_at}".encode()).hexdigest(),
            metadata={"feed_url": self._feed_url},
        )
