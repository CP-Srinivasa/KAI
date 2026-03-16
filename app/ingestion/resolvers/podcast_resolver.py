"""
Podcast Source Resolver
=======================
Classifies and resolves podcast/media source URLs.

Classification rules:
- Apple Podcasts URLs  → requires_api (iTunes Search API)
- Spotify URLs         → requires_api (Spotify API)
- Podigee URLs         → rss_resolution: {handle}.podigee.io/feed/mp3
- Website landing pages → manual_resolution
- Reference pages       → disabled_not_podcast

IMPORTANT: Never fake RSS URLs. Mark unresolvable sources correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from app.core.enums import SourceStatus, SourceType
from app.core.logging import get_logger

logger = get_logger(__name__)


class URLCategory(str, Enum):
    DIRECT_RSS = "direct_rss"
    PODCAST_LANDING = "podcast_landing"
    APPLE_PODCAST = "apple_podcast"
    SPOTIFY_SHOW = "spotify_show"
    SPOTIFY_ANCHOR = "spotify_anchor"
    YOUTUBE_CHANNEL = "youtube_channel"
    SOCIAL_ACCOUNT = "social_account"
    REFERENCE_RESOURCE = "reference_resource"
    WEBSITE_SOURCE = "website_source"
    PODIGEE_FEED = "podigee_feed"
    UNRESOLVED = "unresolved"


@dataclass
class ClassifiedSource:
    """Result of URL classification."""
    url: str
    category: URLCategory
    source_type: SourceType
    status: SourceStatus
    resolved_rss_url: str | None = None
    source_id: str | None = None
    notes: str = ""
    requires_action: str = ""


_REFERENCE_DOMAINS = {
    "a16zcrypto.com",
    "coinledger.io",
    "coinbase.com",
    "tradingview.com",
}

_APPLE_PODCAST_PATTERN = re.compile(
    r"podcasts\.apple\.com/.+/podcast/.+/id(\d+)"
)
_SPOTIFY_SHOW_PATTERN = re.compile(
    r"open\.spotify\.com/show/([A-Za-z0-9]+)"
)
_SPOTIFY_ANCHOR_PATTERN = re.compile(
    r"podcasters?\.spotify\.com/pod/show/([A-Za-z0-9_-]+)"
)
_YOUTUBE_PATTERN = re.compile(
    r"(?:youtube\.com/@|youtube\.com/c/|youtube\.com/channel/)([A-Za-z0-9_@-]+)"
)
_PODIGEE_PATTERN = re.compile(
    r"([A-Za-z0-9_-]+)\.podigee\.io"
)
_RSS_INDICATORS = ("/feed", "/rss", "/atom", ".rss", ".xml", "feed.xml", "podcast.xml")


def classify_url(url: str) -> ClassifiedSource:
    """
    Classify a single URL and determine its source type and status.
    """
    url = url.strip()
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.lower()

    # --- Reference pages ---
    for ref_domain in _REFERENCE_DOMAINS:
        if domain == ref_domain or domain.endswith(f".{ref_domain}"):
            return ClassifiedSource(
                url=url,
                category=URLCategory.REFERENCE_RESOURCE,
                source_type=SourceType.REFERENCE_PAGE,
                status=SourceStatus.DISABLED,
                notes="Educational/reference page, not a news or podcast feed",
                requires_action="",
            )

    # --- Podigee (must be before generic RSS check, as /feed is also a valid podigee path) ---
    podigee_match = _PODIGEE_PATTERN.match(domain)
    if podigee_match and "podigee.io" in domain:
        handle = podigee_match.group(1)
        rss_url = f"https://{handle}.podigee.io/feed/mp3"
        return ClassifiedSource(
            url=url,
            category=URLCategory.PODIGEE_FEED,
            source_type=SourceType.PODCAST_FEED,
            status=SourceStatus.ACTIVE,
            resolved_rss_url=rss_url,
            source_id=f"podigee_{handle}",
            notes=f"Podigee pattern resolved to RSS: {rss_url}",
            requires_action="Verify feed is accessible before activating",
        )

    # --- Direct RSS/Atom feed indicators ---
    if any(indicator in path for indicator in _RSS_INDICATORS):
        return ClassifiedSource(
            url=url,
            category=URLCategory.DIRECT_RSS,
            source_type=SourceType.RSS_FEED,
            status=SourceStatus.ACTIVE,
            resolved_rss_url=url,
            notes="Appears to be a direct RSS/Atom feed",
        )

    # --- Apple Podcasts ---
    apple_match = _APPLE_PODCAST_PATTERN.search(url)
    if apple_match:
        podcast_id = apple_match.group(1)
        return ClassifiedSource(
            url=url,
            category=URLCategory.APPLE_PODCAST,
            source_type=SourceType.PODCAST_PAGE,
            status=SourceStatus.REQUIRES_API,
            source_id=f"apple_podcast_{podcast_id}",
            notes=f"Apple Podcasts ID: {podcast_id}",
            requires_action=(
                f"Use iTunes Search API: "
                f"https://itunes.apple.com/lookup?id={podcast_id} "
                f"to find RSS feedUrl"
            ),
        )

    # --- Spotify Open ---
    spotify_match = _SPOTIFY_SHOW_PATTERN.search(url)
    if spotify_match:
        show_id = spotify_match.group(1)
        return ClassifiedSource(
            url=url,
            category=URLCategory.SPOTIFY_SHOW,
            source_type=SourceType.PODCAST_PAGE,
            status=SourceStatus.REQUIRES_API,
            source_id=f"spotify_show_{show_id}",
            notes="Spotify does not expose public RSS feeds",
            requires_action=(
                "Use Spotify Podcast API (requires Spotify developer credentials) "
                "or manually find alternative RSS source"
            ),
        )

    # --- Spotify for Podcasters / Anchor ---
    anchor_match = _SPOTIFY_ANCHOR_PATTERN.search(url)
    if anchor_match:
        show_slug = anchor_match.group(1)
        return ClassifiedSource(
            url=url,
            category=URLCategory.SPOTIFY_ANCHOR,
            source_type=SourceType.PODCAST_PAGE,
            status=SourceStatus.RSS_RESOLUTION_NEEDED,
            source_id=f"anchor_{show_slug}",
            notes="Anchor/Spotify-for-Podcasters hosted show",
            requires_action=(
                f"Try: https://anchor.fm/s/{show_slug}/podcast/rss "
                "or check the show's website for direct RSS"
            ),
        )

    # --- YouTube ---
    youtube_match = _YOUTUBE_PATTERN.search(url)
    if youtube_match or "youtube.com" in domain:
        return ClassifiedSource(
            url=url,
            category=URLCategory.YOUTUBE_CHANNEL,
            source_type=SourceType.YOUTUBE_CHANNEL,
            status=SourceStatus.REQUIRES_API,
            notes="YouTube channel — needs YouTube Data API v3 for metadata/videos",
            requires_action="Configure YOUTUBE_API_KEY in .env",
        )

    # --- Known news/website domains ---
    # (Could be extended with a domain registry)

    # --- Default: unresolved ---
    return ClassifiedSource(
        url=url,
        category=URLCategory.UNRESOLVED,
        source_type=SourceType.UNRESOLVED_SOURCE,
        status=SourceStatus.MANUAL_RESOLUTION,
        notes="Could not auto-classify. Manual review required.",
        requires_action="Manually identify source type and RSS availability",
    )


def classify_batch(urls: list[str]) -> list[ClassifiedSource]:
    """Classify a list of URLs."""
    results = []
    for url in urls:
        if not url.strip() or url.strip().startswith("#"):
            continue
        classified = classify_url(url)
        logger.debug(
            "url_classified",
            url=url[:80],
            category=classified.category.value,
            status=classified.status.value,
        )
        results.append(classified)
    return results
