"""
YouTube Channel Registry
========================
Builds SourceEntry objects for all YouTube channels defined in
monitor/youtube_channels.txt via the existing youtube_resolver.py.

NOTE: YouTube channels require the YouTube Data API v3 for live fetching.
      These sources are registered with status=REQUIRES_API until
      YOUTUBE_API_KEY is configured.
      [REQUIRES: YOUTUBE_API_KEY in .env]
"""

from __future__ import annotations

from pathlib import Path

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.core.logging import get_logger
from app.ingestion.resolvers.youtube_resolver import (
    YouTubeChannel,
    load_and_deduplicate_channels,
)
from app.ingestion.source_registry import SourceEntry, SourceRegistry

logger = get_logger(__name__)


def _channel_to_source_id(channel: YouTubeChannel) -> str:
    """Derive a stable source_id from a YouTubeChannel."""
    if channel.channel_id:
        return f"youtube_{channel.channel_id}"
    if channel.handle:
        # "@CoinBureau" → "youtube_CoinBureau"
        return f"youtube_{channel.handle.lstrip('@')}"
    # Fallback: derive from normalized URL
    slug = (
        channel.normalized_url
        .replace("https://www.youtube.com/", "")
        .replace("/", "_")
        .replace("@", "")
        .strip("_")
    )
    return f"youtube_{slug}" if slug else "youtube_unknown"


def _channel_to_entry(channel: YouTubeChannel) -> SourceEntry:
    """Convert a YouTubeChannel to a SourceEntry."""
    source_id = _channel_to_source_id(channel)

    # Derive display name from handle or URL slug
    if channel.handle:
        display_name = channel.handle.lstrip("@")
    else:
        display_name = source_id.replace("youtube_", "").replace("_", " ").title()

    return SourceEntry(
        source_id=source_id,
        source_name=display_name,
        source_type=SourceType.YOUTUBE_CHANNEL,
        # [REQUIRES: YOUTUBE_API_KEY] — without it, channels cannot be fetched
        status=SourceStatus.REQUIRES_API,
        url=channel.normalized_url,
        language="en",
        categories=["video", "crypto"],
        auth_mode=AuthMode.API_KEY,
        requires_action="Configure YOUTUBE_API_KEY in .env to enable fetching",
        config={
            "url_type": channel.url_type,
            "channel_id": channel.channel_id,
            "handle": channel.handle,
        },
    )


def build_youtube_registry(channels_path: Path) -> list[SourceEntry]:
    """
    Load and deduplicate YouTube channels from file.
    Returns list of SourceEntry objects (status=REQUIRES_API).

    Args:
        channels_path: Path to monitor/youtube_channels.txt
    """
    if not channels_path.exists():
        logger.warning("youtube_channels_file_not_found", path=str(channels_path))
        return []

    channels = load_and_deduplicate_channels(str(channels_path))
    entries = [_channel_to_entry(ch) for ch in channels]

    logger.info(
        "youtube_registry_built",
        total=len(entries),
        note="All channels require YOUTUBE_API_KEY",
    )
    return entries


def register_youtube_channels(
    registry: SourceRegistry,
    channels_path: Path,
) -> int:
    """
    Build YouTube channel entries and register into the given registry.
    Returns number of entries registered.

    Note: All entries will have status=REQUIRES_API.
    """
    entries = build_youtube_registry(channels_path)
    if entries:
        registry.register_many(entries)
    return len(entries)
