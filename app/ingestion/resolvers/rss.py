"""RSS/Atom feed resolver.

Validates that a URL actually serves a parseable RSS or Atom feed via HTTP.

Principles:
- No fake URL construction. If the URL doesn't return a valid feed → is_valid=False.
- Follows redirects so the resolved_url reflects where the feed actually lives.
- Uses feedparser to determine validity (needs version or at least one entry).
"""

from __future__ import annotations

from dataclasses import dataclass

import feedparser
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_DEFAULT_HEADERS = {
    "User-Agent": "ai-analyst-bot/0.1 (feed validator)",
    "Accept": ("application/rss+xml, application/atom+xml, application/xml, text/xml, */*"),
}


@dataclass(frozen=True)
class RSSResolveResult:
    url: str
    is_valid: bool
    resolved_url: str | None  # final URL after redirects
    feed_title: str | None
    entry_count: int
    error: str | None = None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _fetch(url: str, timeout: int) -> tuple[bytes, str]:
    """Fetch feed bytes and return (content, final_url)."""
    async with httpx.AsyncClient(
        timeout=timeout,
        headers=_DEFAULT_HEADERS,
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content, str(response.url)


async def resolve_rss_feed(url: str, timeout: int = 10) -> RSSResolveResult:
    """Validate that a URL is a real RSS/Atom feed via HTTP.

    Returns RSSResolveResult with is_valid=True only if feedparser
    can parse the content and finds a recognisable feed structure.
    Never constructs or guesses alternative URLs.
    """
    try:
        content, resolved_url = await _fetch(url, timeout)
    except httpx.HTTPError as exc:
        return RSSResolveResult(
            url=url,
            is_valid=False,
            resolved_url=None,
            feed_title=None,
            entry_count=0,
            error=f"HTTP error: {exc}",
        )
    except Exception as exc:
        return RSSResolveResult(
            url=url,
            is_valid=False,
            resolved_url=None,
            feed_title=None,
            entry_count=0,
            error=f"Unexpected error: {exc}",
        )

    feed = feedparser.parse(content)
    is_valid = bool(feed.version or feed.entries)

    if not is_valid:
        return RSSResolveResult(
            url=url,
            is_valid=False,
            resolved_url=resolved_url,
            feed_title=None,
            entry_count=0,
            error="Response is not a valid RSS or Atom feed",
        )

    feed_title: str | None = None
    if hasattr(feed, "feed") and feed.feed:
        feed_title = feed.feed.get("title")

    return RSSResolveResult(
        url=url,
        is_valid=True,
        resolved_url=resolved_url,
        feed_title=feed_title,
        entry_count=len(feed.entries),
    )
