"""
YouTube Channel Resolver
========================
Normalizes YouTube channel URLs and optionally resolves channel IDs
via the YouTube Data API v3.

URL formats handled:
- https://www.youtube.com/@Handle        (current standard)
- https://www.youtube.com/c/ChannelName  (legacy)
- https://www.youtube.com/channel/UCxxx  (by Channel ID)

TODO: Implement live channel ID resolution via YouTube Data API v3.
      Requires YOUTUBE_API_KEY in settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)

_HANDLE_PATTERN = re.compile(r"youtube\.com/@([A-Za-z0-9_.-]+)")
_CUSTOM_PATTERN = re.compile(r"youtube\.com/c/([A-Za-z0-9_.-]+)")
_CHANNEL_ID_PATTERN = re.compile(r"youtube\.com/channel/(UC[A-Za-z0-9_-]+)")
_USER_PATTERN = re.compile(r"youtube\.com/user/([A-Za-z0-9_.-]+)")


@dataclass
class YouTubeChannel:
    """Normalized YouTube channel entry."""
    original_url: str
    normalized_url: str
    handle: str | None
    channel_id: str | None
    url_type: str  # "handle", "custom", "channel_id", "user", "unknown"


def normalize_channel_url(url: str) -> YouTubeChannel:
    """
    Normalize a YouTube channel URL to a canonical form.
    Returns YouTubeChannel with parsed components.
    """
    url = url.strip().split("?")[0].split("#")[0]

    # @Handle format (current standard)
    m = _HANDLE_PATTERN.search(url)
    if m:
        handle = m.group(1)
        return YouTubeChannel(
            original_url=url,
            normalized_url=f"https://www.youtube.com/@{handle}",
            handle=f"@{handle}",
            channel_id=None,
            url_type="handle",
        )

    # /c/ChannelName format (legacy)
    m = _CUSTOM_PATTERN.search(url)
    if m:
        name = m.group(1)
        return YouTubeChannel(
            original_url=url,
            normalized_url=f"https://www.youtube.com/c/{name}",
            handle=None,
            channel_id=None,
            url_type="custom",
        )

    # /channel/UCxxxxxxx format (by Channel ID)
    m = _CHANNEL_ID_PATTERN.search(url)
    if m:
        channel_id = m.group(1)
        return YouTubeChannel(
            original_url=url,
            normalized_url=f"https://www.youtube.com/channel/{channel_id}",
            handle=None,
            channel_id=channel_id,
            url_type="channel_id",
        )

    # /user/Username format (very legacy)
    m = _USER_PATTERN.search(url)
    if m:
        username = m.group(1)
        return YouTubeChannel(
            original_url=url,
            normalized_url=f"https://www.youtube.com/user/{username}",
            handle=None,
            channel_id=None,
            url_type="user",
        )

    logger.warning("youtube_url_unrecognized", url=url)
    return YouTubeChannel(
        original_url=url,
        normalized_url=url,
        handle=None,
        channel_id=None,
        url_type="unknown",
    )


def load_and_deduplicate_channels(file_path: str) -> list[YouTubeChannel]:
    """
    Load YouTube channel URLs from file, normalize and deduplicate.
    Lines starting with # are treated as comments.
    """
    seen_normalized: set[str] = set()
    channels: list[YouTubeChannel] = []

    with open(file_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            channel = normalize_channel_url(line)
            key = channel.normalized_url.lower()
            if key not in seen_normalized:
                seen_normalized.add(key)
                channels.append(channel)
            else:
                logger.debug("youtube_channel_duplicate_skipped", url=line)

    logger.info(
        "youtube_channels_loaded",
        total=len(channels),
        file=file_path,
    )
    return channels
