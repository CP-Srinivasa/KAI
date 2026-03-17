"""YouTube channel URL normalizer and registry loader."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("handle", re.compile(r"youtube\.com/@([\w.-]+)", re.IGNORECASE)),
    ("channel_id", re.compile(r"youtube\.com/channel/([\w-]+)", re.IGNORECASE)),
    ("user", re.compile(r"youtube\.com/user/([\w-]+)", re.IGNORECASE)),
    ("custom", re.compile(r"youtube\.com/c/([\w-]+)", re.IGNORECASE)),
]

# youtu.be short links are video links, not channel links
_YOUTU_BE_RE = re.compile(r"youtu\.be/([\w-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class YouTubeChannel:
    raw_url: str
    normalized_url: str
    handle: str
    channel_type: str  # 'handle' | 'channel_id' | 'user' | 'custom' | 'unknown'
    notes: str | None = None


def normalize_youtube_url(raw_url: str) -> YouTubeChannel:
    """Normalize a YouTube URL to its canonical form.

    Handles: @handle, /channel/, /user/, /c/, and youtu.be short links.
    youtu.be links are video URLs (not channels) — returned with type 'video_link'.
    """
    url = raw_url.strip()

    # youtu.be short links point to individual videos, not channels
    m = _YOUTU_BE_RE.search(url)
    if m:
        video_id = m.group(1)
        return YouTubeChannel(
            raw_url=url,
            normalized_url=f"https://www.youtube.com/watch?v={video_id}",
            handle=video_id,
            channel_type="video_link",
            notes="youtu.be short link — video, not a channel",
        )

    for channel_type, pattern in _PATTERNS:
        m = pattern.search(url)
        if m:
            handle = m.group(1)
            if channel_type == "handle":
                normalized = f"https://www.youtube.com/@{handle}"
            elif channel_type == "channel_id":
                normalized = f"https://www.youtube.com/channel/{handle}"
            elif channel_type == "user":
                normalized = f"https://www.youtube.com/user/{handle}"
            else:
                normalized = f"https://www.youtube.com/c/{handle}"
            return YouTubeChannel(
                raw_url=url,
                normalized_url=normalized,
                handle=handle,
                channel_type=channel_type,
            )

    return YouTubeChannel(
        raw_url=url,
        normalized_url=url,
        handle="",
        channel_type="unknown",
        notes="Could not normalize URL",
    )


def load_youtube_channels(monitor_dir: Path) -> list[YouTubeChannel]:
    """Load, normalize, and deduplicate YouTube channels from monitor file."""
    path = monitor_dir / "youtube_channels.txt"
    if not path.exists():
        return []

    seen: set[str] = set()
    channels: list[YouTubeChannel] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ch = normalize_youtube_url(line)
        if ch.normalized_url not in seen:
            seen.add(ch.normalized_url)
            channels.append(ch)

    return channels
