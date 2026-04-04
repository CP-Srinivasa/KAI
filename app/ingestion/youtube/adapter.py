"""YouTube channel ingestion adapter.

Uses YouTube Data API v3 to fetch recent videos from monitored channels
and youtube-transcript-api to extract transcripts as document text.

Produces CanonicalDocuments compatible with the standard pipeline
(persist_fetch_result → AnalysisPipeline → AlertService).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from app.core.domain.document import CanonicalDocument, YouTubeVideoMeta
from app.core.enums import DocumentType, SourceType
from app.ingestion.base.interfaces import FetchResult

logger = logging.getLogger(__name__)

_YT_API_BASE = "https://www.googleapis.com/youtube/v3"
_MAX_RESULTS_PER_CHANNEL = 10
_MAX_TRANSCRIPT_CHARS = 12_000
_PREFERRED_LANGUAGES = ["en", "de"]


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    title: str
    description: str
    channel_id: str
    channel_title: str
    published_at: str
    thumbnail_url: str | None = None


async def fetch_channel_videos(
    api_key: str,
    channel_handle: str,
    *,
    max_results: int = _MAX_RESULTS_PER_CHANNEL,
    timeout: int = 15,
) -> list[YouTubeVideo]:
    """Fetch recent videos from a YouTube channel via Data API v3.

    Accepts @handle, channel ID, or /c/ custom URL handle.
    Resolves to channel ID first, then fetches recent uploads.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Step 1: Resolve handle to channel ID
        channel_id = await _resolve_channel_id(client, api_key, channel_handle)
        if not channel_id:
            logger.warning("youtube.channel_not_found", extra={"handle": channel_handle})
            return []

        # Step 2: Search for recent videos
        resp = await client.get(
            f"{_YT_API_BASE}/search",
            params={
                "key": api_key,
                "channelId": channel_id,
                "part": "snippet",
                "order": "date",
                "type": "video",
                "maxResults": max_results,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    videos: list[YouTubeVideo] = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        vid_id = item.get("id", {}).get("videoId")
        if not vid_id:
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumb = (thumbnails.get("high") or thumbnails.get("default") or {}).get("url")
        videos.append(
            YouTubeVideo(
                video_id=vid_id,
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                channel_id=snippet.get("channelId", channel_id),
                channel_title=snippet.get("channelTitle", ""),
                published_at=snippet.get("publishedAt", ""),
                thumbnail_url=thumb,
            )
        )
    return videos


async def _resolve_channel_id(
    client: httpx.AsyncClient,
    api_key: str,
    handle: str,
) -> str | None:
    """Resolve a YouTube @handle or custom URL to a channel ID."""
    clean = handle.strip().lstrip("@")

    # Try forHandle (works for @handles)
    resp = await client.get(
        f"{_YT_API_BASE}/channels",
        params={"key": api_key, "forHandle": clean, "part": "id"},
    )
    if resp.status_code == 200:
        items = resp.json().get("items", [])
        if items:
            return items[0]["id"]

    # Try as direct channel ID (UC...)
    if clean.startswith("UC"):
        return clean

    # Try search as fallback
    resp = await client.get(
        f"{_YT_API_BASE}/search",
        params={
            "key": api_key,
            "q": clean,
            "type": "channel",
            "part": "snippet",
            "maxResults": 1,
        },
    )
    if resp.status_code == 200:
        items = resp.json().get("items", [])
        if items:
            return items[0]["snippet"]["channelId"]

    return None


def fetch_transcript(video_id: str) -> str | None:
    """Fetch transcript for a video. Returns None if unavailable."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Try preferred languages first
        transcript = None
        for lang in _PREFERRED_LANGUAGES:
            try:
                transcript = transcript_list.find_transcript([lang])
                break
            except NoTranscriptFound:
                continue

        # Fall back to auto-generated
        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(
                    _PREFERRED_LANGUAGES
                )
            except NoTranscriptFound:
                return None

        parts = transcript.fetch()
        text = " ".join(entry.get("text", "") for entry in parts)
        return text[:_MAX_TRANSCRIPT_CHARS] if text else None

    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except Exception as exc:
        logger.warning(
            "youtube.transcript_error",
            extra={"video_id": video_id, "error": str(exc)},
        )
        return None


def _video_to_document(
    video: YouTubeVideo,
    transcript: str | None,
    source_id: str,
    source_name: str,
) -> CanonicalDocument:
    """Convert a YouTube video + transcript into a CanonicalDocument."""
    url = f"https://www.youtube.com/watch?v={video.video_id}"
    text = transcript or video.description or ""
    published = None
    if video.published_at:
        try:
            published = datetime.fromisoformat(
                video.published_at.replace("Z", "+00:00")
            )
        except ValueError:
            pass

    return CanonicalDocument(
        url=url,
        title=video.title,
        raw_text=text,
        source_id=source_id,
        source_name=source_name,
        source_type=SourceType.YOUTUBE_CHANNEL,
        document_type=DocumentType.YOUTUBE_VIDEO,
        author=video.channel_title,
        published_at=published,
        youtube_meta=YouTubeVideoMeta(
            video_id=video.video_id,
            channel_id=video.channel_id,
            channel_name=video.channel_title,
            thumbnail_url=video.thumbnail_url,
        ),
    )


async def fetch_youtube_channel(
    api_key: str,
    channel_url: str,
    *,
    source_id: str = "youtube",
    source_name: str = "YouTube",
    max_results: int = _MAX_RESULTS_PER_CHANNEL,
    timeout: int = 15,
) -> FetchResult:
    """Fetch recent videos from a YouTube channel and return as FetchResult.

    Compatible with persist_fetch_result() and the standard pipeline.
    """
    try:
        # Extract handle from URL
        handle = _extract_handle(channel_url)
        videos = await fetch_channel_videos(
            api_key, handle, max_results=max_results, timeout=timeout
        )

        documents: list[CanonicalDocument] = []
        for video in videos:
            transcript = fetch_transcript(video.video_id)
            doc = _video_to_document(video, transcript, source_id, source_name)
            documents.append(doc)

        logger.info(
            "youtube.channel_fetched",
            extra={
                "channel": channel_url,
                "videos": len(videos),
                "with_transcript": sum(
                    1 for d in documents if d.raw_text and len(d.raw_text) > 200
                ),
            },
        )

        return FetchResult(
            source_id=source_id,
            documents=documents,
            fetched_at=datetime.now(UTC),
            success=True,
        )

    except Exception as exc:
        logger.error(
            "youtube.fetch_failed",
            extra={"channel": channel_url, "error": str(exc)},
        )
        return FetchResult(
            source_id=source_id,
            documents=[],
            fetched_at=datetime.now(UTC),
            success=False,
            error=str(exc),
        )


def _extract_handle(url: str) -> str:
    """Extract the channel handle from a YouTube URL."""
    url = url.strip()
    # https://www.youtube.com/@Bankless -> Bankless
    if "/@" in url:
        return url.split("/@")[-1].split("/")[0].split("?")[0]
    # https://www.youtube.com/c/JacobCryptoBury -> JacobCryptoBury
    if "/c/" in url:
        return url.split("/c/")[-1].split("/")[0].split("?")[0]
    # https://www.youtube.com/channel/UC... -> UC...
    if "/channel/" in url:
        return url.split("/channel/")[-1].split("/")[0].split("?")[0]
    # https://www.youtube.com/user/... -> ...
    if "/user/" in url:
        return url.split("/user/")[-1].split("/")[0].split("?")[0]
    # Bare handle
    return url.split("/")[-1].lstrip("@")
