"""RSS adapter tests — httpx is mocked, no network calls."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import feedparser
import pytest

from app.core.enums import SourceStatus, SourceType
from app.ingestion.base.interfaces import SourceMetadata
from app.ingestion.rss.adapter import RSSFeedAdapter

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <description>A test RSS feed</description>
    <item>
      <title>Bitcoin hits new high</title>
      <link>https://example.com/article-1</link>
      <description>Bitcoin has reached a new all-time high today.</description>
      <guid>https://example.com/article-1</guid>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
      <author>Test Author</author>
    </item>
    <item>
      <title>Ethereum upgrade complete</title>
      <link>https://example.com/article-2</link>
      <description>The Ethereum network has completed its upgrade.</description>
      <guid>https://example.com/article-2</guid>
    </item>
  </channel>
</rss>"""


def _make_adapter() -> RSSFeedAdapter:
    metadata = SourceMetadata(
        source_id="test-feed",
        source_name="Test Feed",
        source_type=SourceType.RSS_FEED,
        url="https://example.com/feed",
        status=SourceStatus.ACTIVE,
    )
    return RSSFeedAdapter(metadata, timeout=5, max_retries=1)


@pytest.mark.asyncio
async def test_fetch_returns_documents() -> None:
    adapter = _make_adapter()
    with patch.object(adapter, "_fetch_raw", new=AsyncMock(return_value=SAMPLE_RSS)):
        result = await adapter.fetch()
    assert result.success is True
    assert result.source_id == "test-feed"
    assert len(result.documents) == 2


@pytest.mark.asyncio
async def test_fetch_document_fields() -> None:
    adapter = _make_adapter()
    with patch.object(adapter, "_fetch_raw", new=AsyncMock(return_value=SAMPLE_RSS)):
        result = await adapter.fetch()
    doc = result.documents[0]
    assert doc.title == "Bitcoin hits new high"
    assert doc.url == "https://example.com/article-1"
    assert doc.source_id == "test-feed"
    assert doc.source_type == SourceType.RSS_FEED
    assert doc.author == "Test Author"
    assert doc.published_at is not None


@pytest.mark.asyncio
async def test_fetch_on_http_error_returns_failure() -> None:
    import httpx

    adapter = _make_adapter()
    with patch.object(
        adapter,
        "_fetch_raw",
        new=AsyncMock(
            side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        ),
    ):
        result = await adapter.fetch()
    assert result.success is False
    assert result.error is not None
    assert result.documents == []


@pytest.mark.asyncio
async def test_validate_valid_feed() -> None:
    adapter = _make_adapter()
    with patch.object(adapter, "_fetch_raw", new=AsyncMock(return_value=SAMPLE_RSS)):
        valid = await adapter.validate()
    assert valid is True


@pytest.mark.asyncio
async def test_validate_invalid_feed() -> None:
    adapter = _make_adapter()
    with patch.object(adapter, "_fetch_raw", new=AsyncMock(return_value=b"not a feed")):
        # feedparser doesn't crash, but version will be empty and entries empty
        valid = await adapter.validate()
    assert valid is False


def test_feedparser_parses_sample() -> None:
    """Sanity check: feedparser can parse our sample XML."""
    feed = feedparser.parse(SAMPLE_RSS)
    assert len(feed.entries) == 2
    assert feed.entries[0].title == "Bitcoin hits new high"


@pytest.mark.asyncio
async def test_published_at_is_utc_not_local_shifted() -> None:
    """Regression (data-quality): ``published_parsed`` is a UTC ``struct_time``;
    it MUST be read via ``calendar.timegm`` (UTC), never ``time.mktime`` (local),
    or ``published_at`` is shifted by the host TZ offset.

    SAMPLE_RSS pubDate is ``2024-01-01 12:00:00 +0000`` (UTC). The test forces a
    non-UTC host TZ (Europe/Berlin = UTC+1 in January) so a regression back to
    ``time.mktime`` is caught even on a UTC CI runner. Also pins the
    None-handling for the second item, which carries no pubDate.
    """
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset unavailable (non-Unix); cannot force TZ shift")
    prev_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Europe/Berlin"
        time.tzset()
        adapter = _make_adapter()
        with patch.object(adapter, "_fetch_raw", new=AsyncMock(return_value=SAMPLE_RSS)):
            result = await adapter.fetch()
        # timegm → exact UTC; time.mktime under Berlin would yield 11:00:00Z.
        assert result.documents[0].published_at == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        # second item has no pubDate → published_at stays None (no fabrication)
        assert result.documents[1].published_at is None
    finally:
        if prev_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev_tz
        time.tzset()
