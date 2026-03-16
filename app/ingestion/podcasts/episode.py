"""
Podcast Episode Model
======================
Structured representation of a single podcast episode parsed from RSS.

Captures all metadata relevant for downstream analysis:
- Standard RSS fields
- iTunes/Apple Podcasts extensions
- Podcasting 2.0 namespace fields (transcripts, chapters, etc.)
- Transcript availability status
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TranscriptAvailability(str, Enum):
    IN_FEED = "in_feed"           # Transcript URL or text in RSS feed
    EXTERNAL_FILE = "external_file"  # Linked VTT/SRT/JSON file
    AI_REQUIRED = "ai_required"   # Needs Whisper/LLM [REQUIRES: OPENAI_API_KEY]
    UNAVAILABLE = "unavailable"   # No transcript source found
    PENDING = "pending"           # Not yet checked


@dataclass
class TranscriptRef:
    """Reference to a transcript source found in the RSS feed."""
    url: str = ""
    mime_type: str = ""           # text/vtt, application/json, text/plain
    language: str = "en"
    rel: str = ""                 # captions, transcript, subtitles

    @property
    def is_vtt(self) -> bool:
        return "vtt" in self.mime_type.lower() or self.url.endswith(".vtt")

    @property
    def is_srt(self) -> bool:
        return self.url.endswith(".srt")


@dataclass
class PodcastEpisode:
    """
    A single podcast episode, parsed from an RSS feed entry.

    Transcript availability is determined during parsing:
      - Podcasting 2.0 <podcast:transcript> → IN_FEED or EXTERNAL_FILE
      - No transcript available → UNAVAILABLE (AI_REQUIRED if configured)
    """
    # Identity
    episode_id: str = ""          # guid from RSS
    source_id: str = ""           # registry source_id
    feed_url: str = ""

    # Core content
    title: str = ""
    description: str = ""
    summary: str = ""             # iTunes summary (may differ from description)
    published_at: datetime | None = None
    audio_url: str = ""           # Enclosure URL
    audio_mime: str = "audio/mpeg"
    duration_seconds: int = 0
    episode_url: str = ""         # Link to episode page

    # Episode metadata
    episode_number: str = ""
    season_number: str = ""
    episode_type: str = "full"    # full | trailer | bonus

    # Show/speaker metadata
    show_title: str = ""
    show_author: str = ""         # iTunes author
    show_categories: list[str] = field(default_factory=list)
    language: str = "en"

    # Transcript
    transcript_availability: TranscriptAvailability = TranscriptAvailability.PENDING
    transcript_refs: list[TranscriptRef] = field(default_factory=list)
    transcript_text: str = ""     # Inline text if available

    # Tags
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def has_transcript(self) -> bool:
        return self.transcript_availability in (
            TranscriptAvailability.IN_FEED,
            TranscriptAvailability.EXTERNAL_FILE,
        )

    @property
    def needs_ai_transcript(self) -> bool:
        return self.transcript_availability == TranscriptAvailability.AI_REQUIRED

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "source_id": self.source_id,
            "title": self.title,
            "show_title": self.show_title,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "duration_seconds": self.duration_seconds,
            "audio_url": self.audio_url,
            "episode_url": self.episode_url,
            "transcript_availability": self.transcript_availability.value,
            "has_transcript": self.has_transcript,
            "transcript_refs": [
                {"url": r.url, "mime_type": r.mime_type, "language": r.language}
                for r in self.transcript_refs
            ],
            "tags": self.tags,
        }
