"""RSS/Atom feed adapter.

Fetches a feed via httpx, parses it with feedparser,
and returns a list of CanonicalDocuments.
"""

from __future__ import annotations

import asyncio
import calendar
import os
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.domain.document import CanonicalDocument
from app.core.logging import get_logger
from app.ingestion.base.interfaces import (
    BaseSourceAdapter,
    FetchItem,
    FetchResult,
    SourceMetadata,
    normalize_fetch_item,
)
from app.normalization.cleaner import clean_text
from app.security.ssrf import ssrf_redirect_hook, validate_url

_log = get_logger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": "ai-analyst-bot/0.1 (feed reader)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Wedge hardening (2026-05-29): the D-122 full-text fallback calls trafilatura
# synchronously. Two guards bound it so a bad feed batch (many 403/slow article
# URLs) can no longer starve the kai-server event loop for minutes:
#   - per-URL download timeout (trafilatura DOWNLOAD_TIMEOUT)
#   - per-fetch budget (max fallback fetches per feed tick)
# In addition fetch() runs the whole sync doc-conversion off the loop via
# asyncio.to_thread, so even the bounded work never blocks request handling.
# Quick-win follow-up to #104 (2026-05-29): the off-loop fix made the wedge
# non-terminal, but a multi-feed tick still slowed /health for ~2min because the
# shared thread-pool extraction contends for the GIL. Tighter defaults bound a
# single feed's extraction to ~3×4s, and the semaphore serialises extraction
# across feeds (default 1) so concurrent feeds can no longer pile up GIL
# contention. All env-tunable.
_FULL_TEXT_TIMEOUT_S = _int_env("RSS_FULLTEXT_TIMEOUT_S", 4)
_FULL_TEXT_MAX_PER_FETCH = _int_env("RSS_FULLTEXT_MAX_PER_FETCH", 3)
_FULL_TEXT_CONCURRENCY = _int_env("RSS_FULLTEXT_CONCURRENCY", 1)
# Bounds concurrent off-loop extraction across feeds. Created at import; binds
# to the running loop on first use (Python 3.10+).
_EXTRACTION_SEM = asyncio.Semaphore(_FULL_TEXT_CONCURRENCY)


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
        self._full_text_used = 0

    async def fetch(self) -> FetchResult:
        fetched_at = datetime.now(UTC)
        try:
            validate_url(self.metadata.url)  # SSRF guard before any network call
            raw = await self._fetch_raw()
            feed = feedparser.parse(raw)
            # Wedge hardening: the doc-conversion below runs trafilatura's
            # synchronous fetch_url/extract for empty-body entries. Offload the
            # whole batch to a worker thread so it cannot block the event loop
            # (2026-05-29 incident: a 403/slow-article batch starved kai-server).
            async with _EXTRACTION_SEM:
                documents = await asyncio.to_thread(self._build_documents, feed.entries, fetched_at)
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
            event_hooks={"response": [ssrf_redirect_hook]},
        ) as client:
            response = await client.get(self.metadata.url)
            response.raise_for_status()
            return response.content

    def _build_documents(self, entries: list[Any], fetched_at: datetime) -> list[CanonicalDocument]:
        """Convert feed entries to documents (runs in a worker thread).

        Resets the per-fetch full-text budget so a single feed tick can never
        trigger an unbounded number of trafilatura downloads.
        """
        self._full_text_used = 0
        return [self._entry_to_doc(e, fetched_at) for e in entries]

    @staticmethod
    def _fetch_full_text(url: str) -> str | None:
        """Fetch full article text from URL via trafilatura.

        Returns extracted text or None on any failure.  Fail-open: never
        raises, so a broken article page does not block the feed. A hard
        per-URL download timeout (``RSS_FULLTEXT_TIMEOUT_S``) bounds the
        blocking time so a slow/403 host cannot stall the batch.
        """
        try:
            import trafilatura  # lazy import — only needed for stub feeds

            config = None
            try:
                from trafilatura.settings import use_config

                config = use_config()
                config.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(_FULL_TEXT_TIMEOUT_S))
            except Exception:  # noqa: BLE001 — older trafilatura / no config support
                config = None

            downloaded = (
                trafilatura.fetch_url(url, config=config)
                if config is not None
                else trafilatura.fetch_url(url)
            )
            if not downloaded:
                return None
            text = trafilatura.extract(downloaded)
            return text if text and len(text) > 50 else None
        except Exception:  # noqa: BLE001
            return None

    def _entry_to_doc(self, entry: Any, fetched_at: datetime) -> CanonicalDocument:
        # Extract text content — prefer full content over summary
        text: str | None = None
        if entry.get("content"):
            text = entry["content"][0].get("value")
        if not text:
            text = entry.get("summary")

        # D-122: Full-text fallback for feeds that deliver empty bodies.
        # Fetches the article page and extracts main text via trafilatura.
        if not text or len(text.strip()) <= 50:
            link = entry.get("link")
            if link and self._full_text_used < _FULL_TEXT_MAX_PER_FETCH:
                self._full_text_used += 1
                full_text = self._fetch_full_text(link)
                if full_text:
                    _log.info(
                        "rss.full_text_fallback",
                        url=link,
                        extracted_len=len(full_text),
                    )
                    text = full_text

        # Parse publication date.
        # feedparser's ``published_parsed`` is a struct_time already normalised to
        # UTC. ``calendar.timegm`` interprets it as UTC; ``time.mktime`` would
        # (wrongly) treat it as host-local time and shift the timestamp by the
        # host's UTC offset — on a CET/CEST host that silently moved every RSS
        # ``published_at`` ~1h earlier, inflating all measured ingestion latency.
        published: datetime | None = None
        if entry.get("published_parsed"):
            try:
                published = datetime.fromtimestamp(
                    calendar.timegm(entry.published_parsed), tz=UTC
                )
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
