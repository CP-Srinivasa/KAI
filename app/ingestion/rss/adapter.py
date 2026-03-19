"""RSS/Atom feed adapter.

Fetches a feed via httpx, parses it with feedparser,
and returns a list of CanonicalDocuments.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.domain.document import CanonicalDocument
from app.ingestion.base.interfaces import (
    BaseSourceAdapter,
    FetchItem,
    FetchResult,
    SourceMetadata,
    normalize_fetch_item,
)
from app.normalization.cleaner import clean_text
from app.security.ssrf import validate_url

_DEFAULT_HEADERS = {
    "User-Agent": "ai-analyst-bot/0.1 (feed reader)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
}


class RSSFeedAdapter(BaseSourceAdapter):
    def __init__(
        self,
        metadata: SourceMetadata,
        timeout: int = 15,
        max_retries: int = 3,
    ) -> None:
        super().__init__(metadata)
        self._timeout = timeout
        self._max_retries = max_retries

    async def fetch(self) -> FetchResult:
        fetched_at = datetime.now(UTC)
        try:
            validate_url(self.metadata.url)  # SSRF guard before any network call
            raw = await self._fetch_raw()
            feed = feedparser.parse(raw)
            documents = [self._entry_to_doc(e, fetched_at) for e in feed.entries]
            return FetchResult(
                source_id=self.source_id,
                documents=documents,
                fetched_at=fetched_at,
                success=True,
                metadata={"entry_count": len(documents), "feed_version": feed.version},
            )
        except Exception as exc:
            return FetchResult(
                source_id=self.source_id,
                documents=[],
                fetched_at=fetched_at,
                success=False,
                error=str(exc),
            )

    async def validate(self) -> bool:
        try:
            raw = await self._fetch_raw()
            feed = feedparser.parse(raw)
            return bool(feed.version or feed.entries)
        except Exception:
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _fetch_raw(self) -> bytes:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers=_DEFAULT_HEADERS,
            follow_redirects=True,
        ) as client:
            response = await client.get(self.metadata.url)
            response.raise_for_status()
            return response.content

    def _entry_to_doc(self, entry: Any, fetched_at: datetime) -> CanonicalDocument:
        # Extract text content — prefer full content over summary
        text: str | None = None
        if entry.get("content"):
            text = entry["content"][0].get("value")
        if not text:
            text = entry.get("summary")

        # Parse publication date
        published: datetime | None = None
        if entry.get("published_parsed"):
            try:
                published = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=UTC)
            except (ValueError, OverflowError, OSError):
                published = None

        item = FetchItem(
            url=entry.get("link", ""),
            external_id=entry.get("id") or entry.get("link"),
            title=entry.get("title"),
            content=text,
            published_at=published,
        )
        document = normalize_fetch_item(
            item,
            source_id=self.source_id,
            source_name=self.metadata.source_name,
            source_type=self.metadata.source_type,  # honour actual type (e.g. PODCAST_FEED)
        )
        return document.model_copy(
            update={
                "author": entry.get("author"),
                "fetched_at": fetched_at,
                "summary": clean_text(entry.get("summary")),
            }
        )
