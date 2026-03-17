"""URL classifier — determines SourceType from a raw URL.

Rules (in order):
1. YouTube domains       → youtube_channel
2. Spotify show pages    → podcast_page / requires_api
3. Apple Podcast pages   → podcast_page / requires_api
4. Podigee subdomains    → podcast_feed (pattern-resolvable)
5. URL path matches RSS  → rss_feed
6. Everything else       → website

Reference pages (a16z, coinbase/learn, etc.) are classified manually
in monitor/website_sources.txt, not auto-detected here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.enums import SourceStatus, SourceType

# Ordered list of path patterns that indicate an RSS/Atom feed
_RSS_PATH_RE = re.compile(
    r"(/feed/?$"
    r"|/feed\.xml$"
    r"|/feed\.atom$"
    r"|/rss/?$"
    r"|/rss\.xml$"
    r"|/atom\.xml$"
    r"|/feed/rss/?$"
    r"|/feed/podcast/?$"
    r"|/feed/mp3$"
    r"|\.rss$"
    r"|\.atom$"
    r")"
)


@dataclass(frozen=True)
class ClassificationResult:
    source_type: SourceType
    status: SourceStatus
    notes: str | None = None


def classify_url(url: str) -> ClassificationResult:
    """Classify a raw URL into a SourceType + SourceStatus."""
    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return ClassificationResult(
            SourceType.UNRESOLVED_SOURCE, SourceStatus.UNRESOLVED, "Invalid URL"
        )

    host = parsed.netloc.lower()
    path = parsed.path.lower()

    # YouTube
    if "youtube.com" in host or host == "youtu.be":
        return ClassificationResult(SourceType.YOUTUBE_CHANNEL, SourceStatus.ACTIVE)

    # Spotify
    if "open.spotify.com" in host and "/show/" in path:
        return ClassificationResult(
            SourceType.PODCAST_PAGE, SourceStatus.REQUIRES_API, "Spotify requires API"
        )

    # Apple Podcasts
    if "podcasts.apple.com" in host:
        return ClassificationResult(
            SourceType.PODCAST_PAGE, SourceStatus.REQUIRES_API, "Apple Podcasts requires API"
        )

    # Podigee — feeds follow the pattern {handle}.podigee.io/feed/mp3
    if host.endswith(".podigee.io"):
        return ClassificationResult(
            SourceType.PODCAST_FEED,
            SourceStatus.ACTIVE,
            "Podigee feed — resolved via pattern",
        )

    # RSS/Atom feed path patterns
    if _RSS_PATH_RE.search(path):
        return ClassificationResult(SourceType.RSS_FEED, SourceStatus.ACTIVE)

    # Default: treat as website
    return ClassificationResult(SourceType.WEBSITE, SourceStatus.ACTIVE)
