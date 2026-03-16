"""
Podcast Transcript Pipeline
=============================
Determines transcript availability for podcast episodes from RSS feeds.

Three transcript sources (in priority order):
  1. Podcasting 2.0 <podcast:transcript> elements in feed (best)
  2. iTunes <itunes:summary> containing full text (rare, mediocre)
  3. Whisper AI transcription of audio [REQUIRES: OPENAI_API_KEY] (optional)

Usage:
    parser = PodcastTranscriptParser()
    episodes = parser.parse_feed(feed_url="https://example.com/feed.xml")
    for ep in episodes:
        print(ep.title, ep.transcript_availability.value)

No audio is downloaded unless WHISPER_ENABLED=true and API key is configured.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.ingestion.podcasts.episode import (
    PodcastEpisode,
    TranscriptAvailability,
    TranscriptRef,
)

logger = get_logger(__name__)

# Podcasting 2.0 transcript namespace
_PC20_NS = "https://podcastindex.org/namespace/1.0"
_PC20_PREFIX = "{https://podcastindex.org/namespace/1.0}"

# Minimum description length to consider as transcript
_MIN_TRANSCRIPT_LENGTH = 1500


class PodcastTranscriptParser:
    """
    Parses RSS feed and determines transcript availability per episode.

    Relies on feedparser (already in project deps).
    No additional packages needed unless Whisper is enabled.
    """

    def __init__(
        self,
        whisper_enabled: bool = False,
        min_transcript_length: int = _MIN_TRANSCRIPT_LENGTH,
    ) -> None:
        self._whisper_enabled = whisper_enabled
        self._min_transcript_length = min_transcript_length

    def parse_feed(
        self,
        feed_url: str = "",
        feed_content: str = "",
        source_id: str = "",
    ) -> list[PodcastEpisode]:
        """
        Parse RSS feed and return PodcastEpisode list with transcript metadata.

        Args:
            feed_url:      URL to fetch and parse
            feed_content:  Pre-fetched feed content (for testing)
            source_id:     Registry source_id for these episodes
        """
        try:
            import feedparser  # noqa: PLC0415
        except ImportError:
            logger.error("feedparser_not_installed")
            return []

        if feed_content:
            feed = feedparser.parse(feed_content)
        elif feed_url:
            feed = feedparser.parse(feed_url)
        else:
            return []

        episodes = []
        show_title = feed.feed.get("title", "")
        show_author = feed.feed.get("author", "")
        language = feed.feed.get("language", "en")[:2]

        for entry in feed.entries:
            ep = self._parse_entry(entry, source_id, show_title, show_author, language)
            episodes.append(ep)

        logger.info(
            "podcast_feed_parsed",
            source_id=source_id,
            episodes=len(episodes),
            with_transcript=sum(1 for e in episodes if e.has_transcript),
        )
        return episodes

    def _parse_entry(
        self,
        entry: Any,
        source_id: str,
        show_title: str,
        show_author: str,
        language: str,
    ) -> PodcastEpisode:
        ep = PodcastEpisode(
            episode_id=entry.get("id", ""),
            source_id=source_id,
            title=entry.get("title", ""),
            description=entry.get("summary", ""),
            published_at=self._parse_date(entry),
            episode_url=entry.get("link", ""),
            show_title=show_title,
            show_author=show_author,
            language=language,
        )

        # iTunes metadata
        ep.summary = entry.get("itunes_summary", "") or entry.get("subtitle", "")
        ep.episode_number = str(entry.get("itunes_episode", ""))
        ep.season_number = str(entry.get("itunes_season", ""))
        ep.episode_type = entry.get("itunes_episodetype", "full")
        ep.keywords = [
            k.strip()
            for k in entry.get("itunes_keywords", "").split(",")
            if k.strip()
        ]

        # Audio enclosure
        for enc in entry.get("enclosures", []):
            if "audio" in enc.get("type", ""):
                ep.audio_url = enc.get("href", "")
                ep.audio_mime = enc.get("type", "audio/mpeg")
                try:
                    ep.duration_seconds = int(enc.get("length", 0))
                except (ValueError, TypeError):
                    pass
                break

        # Duration from iTunes
        if not ep.duration_seconds:
            raw_dur = entry.get("itunes_duration", "")
            ep.duration_seconds = self._parse_duration(raw_dur)

        # Transcript discovery
        self._detect_transcripts(ep, entry)

        return ep

    def _detect_transcripts(self, ep: PodcastEpisode, entry: Any) -> None:
        """Check for transcript availability in all known locations."""
        refs: list[TranscriptRef] = []

        # 1. Podcasting 2.0 <podcast:transcript>
        for key in (entry.tags if hasattr(entry, "tags") else []):
            pass  # feedparser flattens these; check via direct keys

        # feedparser exposes Podcasting 2.0 as podcast_transcript (list)
        raw_transcripts = entry.get("podcast_transcript", [])
        if isinstance(raw_transcripts, dict):
            raw_transcripts = [raw_transcripts]
        for t in raw_transcripts:
            url = t.get("url") or t.get("href") or ""
            if url:
                refs.append(TranscriptRef(
                    url=url,
                    mime_type=t.get("type", ""),
                    language=t.get("language", ep.language),
                    rel=t.get("rel", "transcript"),
                ))

        if refs:
            ep.transcript_refs = refs
            ep.transcript_availability = TranscriptAvailability.EXTERNAL_FILE
            logger.debug("transcript_found_podcast20", episode=ep.title, urls=[r.url for r in refs])
            return

        # 2. Long description may contain transcript text
        desc = ep.description or ep.summary
        if len(desc) >= self._min_transcript_length:
            ep.transcript_text = desc
            ep.transcript_availability = TranscriptAvailability.IN_FEED
            logger.debug("transcript_found_in_description", episode=ep.title, chars=len(desc))
            return

        # 3. Whisper available?
        if self._whisper_enabled and ep.audio_url:
            ep.transcript_availability = TranscriptAvailability.AI_REQUIRED
            return

        ep.transcript_availability = TranscriptAvailability.UNAVAILABLE

    def _parse_date(self, entry: Any) -> datetime | None:
        try:
            import time as _time
            t = entry.get("published_parsed")
            if t:
                return datetime(*t[:6])
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_duration(raw: str) -> int:
        """Parse HH:MM:SS or MM:SS or plain seconds to int seconds."""
        if not raw:
            return 0
        try:
            parts = raw.strip().split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(float(raw))
        except (ValueError, TypeError):
            return 0
