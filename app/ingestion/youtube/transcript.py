"""
YouTube Transcript Pipeline
============================
Fetches transcripts from YouTube videos when available.

Uses `youtube-transcript-api` (no API key required for public videos).
[REQUIRES: pip install youtube-transcript-api]

Status model:
  AVAILABLE       — transcript fetched successfully
  UNAVAILABLE     — video has no captions
  DISABLED        — transcript retrieval is turned off
  RATE_LIMITED    — hit YouTube limits; retry later
  REQUIRES_METADATA — video_id not yet known (channel without video list)
  ERROR           — unexpected error
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class TranscriptStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    RATE_LIMITED = "rate_limited"
    REQUIRES_METADATA = "requires_metadata"
    ERROR = "error"


@dataclass
class TranscriptSegment:
    text: str
    start: float       # seconds from video start
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class TranscriptResult:
    video_id: str
    status: TranscriptStatus
    language: str = "en"
    is_generated: bool = True       # True = auto-generated, False = manual
    segments: list[TranscriptSegment] = field(default_factory=list)
    error: str = ""

    @property
    def full_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    @property
    def word_count(self) -> int:
        return len(self.full_text.split())

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "status": self.status.value,
            "language": self.language,
            "is_generated": self.is_generated,
            "word_count": self.word_count,
            "segment_count": len(self.segments),
            "error": self.error,
        }


class YouTubeTranscriptPipeline:
    """
    Fetches YouTube video transcripts.

    Requires: pip install youtube-transcript-api
    [REQUIRES: no API key — relies on public YouTube captions]

    Rate limits: YouTube may throttle aggressive requests.
    Recommended: 1 request / 2 seconds in production.

    Usage:
        pipeline = YouTubeTranscriptPipeline()
        result = pipeline.fetch("dQw4w9WgXcQ")
        if result.status == TranscriptStatus.AVAILABLE:
            text = result.full_text
    """

    def __init__(
        self,
        preferred_languages: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._preferred_languages = preferred_languages or ["en", "en-US", "en-GB"]
        self._enabled = enabled

    def fetch(self, video_id: str) -> TranscriptResult:
        """
        Fetch transcript for a single video_id synchronously.
        Returns TranscriptResult with appropriate status.
        """
        if not self._enabled:
            return TranscriptResult(video_id=video_id, status=TranscriptStatus.DISABLED)

        if not video_id:
            return TranscriptResult(
                video_id=video_id,
                status=TranscriptStatus.REQUIRES_METADATA,
                error="video_id is required",
            )

        try:
            # [REQUIRES: youtube-transcript-api]
            from youtube_transcript_api import (  # type: ignore[import]
                YouTubeTranscriptApi,
                TranscriptsDisabled,
                NoTranscriptFound,
                VideoUnavailable,
            )
        except ImportError:
            logger.warning(
                "youtube_transcript_api_not_installed",
                note="Run: pip install youtube-transcript-api",
            )
            return TranscriptResult(
                video_id=video_id,
                status=TranscriptStatus.ERROR,
                error="youtube-transcript-api not installed",
            )

        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            # Prefer manual transcripts; fall back to auto-generated
            transcript = None
            is_generated = True
            try:
                transcript = transcript_list.find_manually_created_transcript(
                    self._preferred_languages
                )
                is_generated = False
            except Exception:
                pass

            if transcript is None:
                try:
                    transcript = transcript_list.find_generated_transcript(
                        self._preferred_languages
                    )
                    is_generated = True
                except Exception:
                    pass

            if transcript is None:
                # Take first available in any language
                try:
                    transcript = next(iter(transcript_list))
                    is_generated = True
                except StopIteration:
                    return TranscriptResult(
                        video_id=video_id,
                        status=TranscriptStatus.UNAVAILABLE,
                        error="No transcript available in any language",
                    )

            raw = transcript.fetch()
            segments = [
                TranscriptSegment(
                    text=item.get("text", ""),
                    start=item.get("start", 0.0),
                    duration=item.get("duration", 0.0),
                )
                for item in raw
            ]

            logger.info(
                "transcript_fetched",
                video_id=video_id,
                segments=len(segments),
                is_generated=is_generated,
                language=transcript.language_code,
            )
            return TranscriptResult(
                video_id=video_id,
                status=TranscriptStatus.AVAILABLE,
                language=transcript.language_code,
                is_generated=is_generated,
                segments=segments,
            )

        except Exception as exc:
            exc_name = type(exc).__name__
            if "TranscriptsDisabled" in exc_name:
                return TranscriptResult(
                    video_id=video_id,
                    status=TranscriptStatus.UNAVAILABLE,
                    error="Transcripts disabled for this video",
                )
            if "NoTranscriptFound" in exc_name:
                return TranscriptResult(
                    video_id=video_id,
                    status=TranscriptStatus.UNAVAILABLE,
                    error="No transcript found",
                )
            if "VideoUnavailable" in exc_name:
                return TranscriptResult(
                    video_id=video_id,
                    status=TranscriptStatus.UNAVAILABLE,
                    error="Video unavailable or private",
                )
            if "429" in str(exc) or "Too Many Requests" in str(exc):
                logger.warning("youtube_transcript_rate_limited", video_id=video_id)
                return TranscriptResult(
                    video_id=video_id,
                    status=TranscriptStatus.RATE_LIMITED,
                    error="Rate limited by YouTube",
                )
            logger.error("transcript_fetch_error", video_id=video_id, error=str(exc))
            return TranscriptResult(
                video_id=video_id,
                status=TranscriptStatus.ERROR,
                error=str(exc),
            )

    def fetch_batch(
        self,
        video_ids: list[str],
        stop_on_rate_limit: bool = True,
    ) -> list[TranscriptResult]:
        """Fetch transcripts for multiple video IDs."""
        results = []
        for vid in video_ids:
            result = self.fetch(vid)
            results.append(result)
            if stop_on_rate_limit and result.status == TranscriptStatus.RATE_LIMITED:
                logger.warning(
                    "transcript_batch_stopped_rate_limit",
                    completed=len(results),
                    remaining=len(video_ids) - len(results),
                )
                break
        return results
