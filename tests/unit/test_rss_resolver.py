"""Tests for the HTTP-based RSS feed resolver.

All network calls are mocked — no real HTTP in unit tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ingestion.resolvers.rss import resolve_rss_feed

SAMPLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Crypto Feed</title>
    <link>https://example.com</link>
    <item><title>Bitcoin hits ATH</title><link>https://example.com/1</link></item>
    <item><title>ETH update</title><link>https://example.com/2</link></item>
  </channel>
</rss>"""

SAMPLE_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry><title>Entry 1</title><id>urn:uuid:1</id></entry>
</feed>"""

NOT_A_FEED = b"<html><body>This is just a website</body></html>"


def _mock_response(content: bytes, url: str = "https://example.com/feed") -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.url = url
    resp.raise_for_status = MagicMock()
    return resp


def _mock_client(response: MagicMock) -> MagicMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_resolve_valid_rss_feed() -> None:
    resp = _mock_response(SAMPLE_RSS)
    with patch("app.ingestion.resolvers.rss.httpx.AsyncClient", return_value=_mock_client(resp)):
        result = await resolve_rss_feed("https://example.com/feed")
    assert result.is_valid is True
    assert result.entry_count == 2
    assert result.feed_title == "Test Crypto Feed"
    assert result.error is None
    assert result.resolved_url == "https://example.com/feed"


@pytest.mark.asyncio
async def test_resolve_valid_atom_feed() -> None:
    resp = _mock_response(SAMPLE_ATOM)
    with patch("app.ingestion.resolvers.rss.httpx.AsyncClient", return_value=_mock_client(resp)):
        result = await resolve_rss_feed("https://example.com/atom")
    assert result.is_valid is True
    assert result.entry_count == 1
    assert result.feed_title == "Atom Feed"


@pytest.mark.asyncio
async def test_resolve_not_a_feed_returns_invalid() -> None:
    resp = _mock_response(NOT_A_FEED)
    with patch("app.ingestion.resolvers.rss.httpx.AsyncClient", return_value=_mock_client(resp)):
        result = await resolve_rss_feed("https://example.com/page")
    assert result.is_valid is False
    assert result.entry_count == 0
    assert result.error is not None


@pytest.mark.asyncio
async def test_resolve_http_error_returns_invalid() -> None:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    # Bypass SSRF validation so we can test the HTTP-layer error path
    with (
        patch("app.ingestion.resolvers.rss.validate_url", return_value=None),
        patch("app.ingestion.resolvers.rss.httpx.AsyncClient", return_value=client),
    ):
        result = await resolve_rss_feed("https://offline.example.com/feed")
    assert result.is_valid is False
    assert result.resolved_url is None
    assert "HTTP error" in (result.error or "")


@pytest.mark.asyncio
async def test_resolve_follows_redirects() -> None:
    resp = _mock_response(SAMPLE_RSS, url="https://example.com/feed-v2")
    with patch("app.ingestion.resolvers.rss.httpx.AsyncClient", return_value=_mock_client(resp)):
        result = await resolve_rss_feed("https://example.com/old-feed")
    assert result.is_valid is True
    assert result.resolved_url == "https://example.com/feed-v2"


@pytest.mark.asyncio
async def test_resolve_no_fake_url_constructed() -> None:
    """Resolver must NEVER guess or construct alternative URLs."""
    resp = _mock_response(NOT_A_FEED, url="https://example.com/notafeed")
    with patch("app.ingestion.resolvers.rss.httpx.AsyncClient", return_value=_mock_client(resp)):
        result = await resolve_rss_feed("https://example.com/notafeed")
    # Only the input URL (or its redirect) should appear — no constructed /feed or /rss URLs
    assert result.is_valid is False
    assert result.url == "https://example.com/notafeed"
