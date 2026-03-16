"""
YouTube Video Metadata
=======================
Fetches video and channel metadata via YouTube Data API v3.
[REQUIRES: YOUTUBE_API_KEY in .env]

Without an API key all methods return placeholder metadata
with status=REQUIRES_API.

API quotas (default free tier):
  - 10,000 units/day
  - search.list: 100 units/call
  - videos.list: 1 unit/call
  - channels.list: 1 unit/call
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.enums import SourceStatus
from app.core.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://www.googleapis.com/youtube/v3"


@dataclass
class VideoMetadata:
    video_id: str
    title: str = ""
    description: str = ""
    channel_id: str = ""
    channel_title: str = ""
    published_at: datetime | None = None
    duration_iso: str = ""        # e.g. "PT12M34S"
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    tags: list[str] = field(default_factory=list)
    thumbnail_url: str = ""
    status: SourceStatus = SourceStatus.REQUIRES_API

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "channel_id": self.channel_id,
            "channel_title": self.channel_title,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "tags": self.tags,
            "status": self.status.value,
        }


@dataclass
class ChannelMetadata:
    channel_id: str
    title: str = ""
    description: str = ""
    custom_url: str = ""
    subscriber_count: int = 0
    video_count: int = 0
    view_count: int = 0
    country: str = ""
    published_at: datetime | None = None
    uploads_playlist_id: str = ""
    status: SourceStatus = SourceStatus.REQUIRES_API

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "title": self.title,
            "custom_url": self.custom_url,
            "subscriber_count": self.subscriber_count,
            "video_count": self.video_count,
            "uploads_playlist_id": self.uploads_playlist_id,
            "status": self.status.value,
        }


class YouTubeMetadataClient:
    """
    Fetches video/channel metadata from YouTube Data API v3.
    [REQUIRES: YOUTUBE_API_KEY in .env]

    Without a key, all methods return stub metadata with status=REQUIRES_API.

    Usage:
        client = YouTubeMetadataClient(api_key="YOUR_KEY")
        video = await client.get_video_metadata("dQw4w9WgXcQ")
        channel = await client.get_channel_metadata("UCxxx")
    """

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def get_video_metadata(self, video_id: str) -> VideoMetadata:
        """Fetch video metadata. Returns REQUIRES_API stub if not configured."""
        if not self.is_configured:
            logger.debug(
                "youtube_api_not_configured",
                note="Set YOUTUBE_API_KEY in .env",
                video_id=video_id,
            )
            return VideoMetadata(
                video_id=video_id,
                status=SourceStatus.REQUIRES_API,
                title="[REQUIRES: YOUTUBE_API_KEY]",
            )

        import httpx  # noqa: PLC0415

        url = f"{_API_BASE}/videos"
        params = {
            "id": video_id,
            "part": "snippet,statistics,contentDetails",
            "key": self._api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    return VideoMetadata(video_id=video_id, status=SourceStatus.ERROR)
                item = items[0]
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                content = item.get("contentDetails", {})
                published = snippet.get("publishedAt")
                return VideoMetadata(
                    video_id=video_id,
                    title=snippet.get("title", ""),
                    description=snippet.get("description", "")[:500],
                    channel_id=snippet.get("channelId", ""),
                    channel_title=snippet.get("channelTitle", ""),
                    published_at=datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None,
                    duration_iso=content.get("duration", ""),
                    view_count=int(stats.get("viewCount", 0)),
                    like_count=int(stats.get("likeCount", 0)),
                    comment_count=int(stats.get("commentCount", 0)),
                    tags=snippet.get("tags", [])[:20],
                    thumbnail_url=snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                    status=SourceStatus.ACTIVE,
                )
        except Exception as e:
            logger.error("youtube_metadata_error", video_id=video_id, error=str(e))
            return VideoMetadata(video_id=video_id, status=SourceStatus.ERROR)

    async def get_channel_metadata(self, channel_id: str) -> ChannelMetadata:
        """Fetch channel metadata. Returns REQUIRES_API stub if not configured."""
        if not self.is_configured:
            return ChannelMetadata(
                channel_id=channel_id,
                status=SourceStatus.REQUIRES_API,
                title="[REQUIRES: YOUTUBE_API_KEY]",
            )

        import httpx  # noqa: PLC0415

        url = f"{_API_BASE}/channels"
        params = {
            "id": channel_id,
            "part": "snippet,statistics,contentDetails",
            "key": self._api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    return ChannelMetadata(channel_id=channel_id, status=SourceStatus.ERROR)
                item = items[0]
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                content = item.get("contentDetails", {})
                published = snippet.get("publishedAt")
                return ChannelMetadata(
                    channel_id=channel_id,
                    title=snippet.get("title", ""),
                    description=snippet.get("description", "")[:300],
                    custom_url=snippet.get("customUrl", ""),
                    subscriber_count=int(stats.get("subscriberCount", 0)),
                    video_count=int(stats.get("videoCount", 0)),
                    view_count=int(stats.get("viewCount", 0)),
                    country=snippet.get("country", ""),
                    published_at=datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None,
                    uploads_playlist_id=content.get("relatedPlaylists", {}).get("uploads", ""),
                    status=SourceStatus.ACTIVE,
                )
        except Exception as e:
            logger.error("youtube_channel_metadata_error", channel_id=channel_id, error=str(e))
            return ChannelMetadata(channel_id=channel_id, status=SourceStatus.ERROR)

    async def list_channel_videos(
        self,
        uploads_playlist_id: str,
        max_results: int = 10,
    ) -> list[str]:
        """
        Return list of video_ids from a channel's uploads playlist.
        [REQUIRES: YOUTUBE_API_KEY]
        """
        if not self.is_configured:
            return []

        import httpx  # noqa: PLC0415

        url = f"{_API_BASE}/playlistItems"
        params = {
            "playlistId": uploads_playlist_id,
            "part": "contentDetails",
            "maxResults": min(max_results, 50),
            "key": self._api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                return [
                    item["contentDetails"]["videoId"]
                    for item in data.get("items", [])
                    if "contentDetails" in item
                ]
        except Exception as e:
            logger.error("youtube_playlist_error", playlist_id=uploads_playlist_id, error=str(e))
            return []
