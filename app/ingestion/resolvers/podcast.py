"""Podcast source resolver.

Classifies raw podcast URLs and attempts feed resolution where possible.

Resolution outcomes:
- RSS/Atom path detected         → podcast_feed / active (already a feed)
- Podigee subdomain              → podcast_feed / active (pattern: {handle}.podigee.io/feed/mp3)
- Apple Podcasts                 → podcast_page / requires_api
- Spotify (open or podcasters)   → podcast_page / requires_api
- Anything else                  → unresolved_source / unresolved
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.enums import SourceStatus, SourceType
from app.ingestion.classifier import classify_url


@dataclass(frozen=True)
class PodcastSource:
    raw_url: str
    source_type: SourceType
    status: SourceStatus
    resolved_url: str | None
    notes: str | None = None


def _resolve_podigee(url: str) -> str:
    """Construct the feed URL for a Podigee podcast."""
    base = url.rstrip("/")
    if base.endswith("/feed/mp3"):
        return base
    return f"{base}/feed/mp3"


def resolve_podcast_url(raw_url: str) -> PodcastSource:
    """Resolve a single raw podcast URL."""
    result = classify_url(raw_url)

    if result.source_type == SourceType.RSS_FEED:
        return PodcastSource(
            raw_url=raw_url,
            source_type=SourceType.PODCAST_FEED,
            status=SourceStatus.ACTIVE,
            resolved_url=raw_url,
        )

    if result.source_type == SourceType.PODCAST_FEED:  # Podigee
        return PodcastSource(
            raw_url=raw_url,
            source_type=SourceType.PODCAST_FEED,
            status=SourceStatus.ACTIVE,
            resolved_url=_resolve_podigee(raw_url),
            notes="Resolved via Podigee pattern",
        )

    if result.source_type == SourceType.PODCAST_PAGE:
        # Apple/Spotify → requires_api; generic landing pages → unresolved
        if result.status == SourceStatus.REQUIRES_API:
            return PodcastSource(
                raw_url=raw_url,
                source_type=SourceType.PODCAST_PAGE,
                status=SourceStatus.REQUIRES_API,
                resolved_url=None,
                notes=result.notes,
            )
        return PodcastSource(
            raw_url=raw_url,
            source_type=SourceType.UNRESOLVED_SOURCE,
            status=SourceStatus.UNRESOLVED,
            resolved_url=None,
            notes=result.notes or "Podcast landing page — no feed URL detected",
        )

    return PodcastSource(
        raw_url=raw_url,
        source_type=SourceType.UNRESOLVED_SOURCE,
        status=SourceStatus.UNRESOLVED,
        resolved_url=None,
        notes="No resolution strategy available",
    )


def load_and_resolve_podcasts(
    monitor_dir: Path,
) -> tuple[list[PodcastSource], list[PodcastSource]]:
    """Load podcast_feeds_raw.txt and resolve each entry.

    Returns (resolved, unresolved) — where resolved means status=active.
    """
    path = monitor_dir / "podcast_feeds_raw.txt"
    if not path.exists():
        return [], []

    resolved: list[PodcastSource] = []
    unresolved: list[PodcastSource] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        source = resolve_podcast_url(line)
        if source.status == SourceStatus.ACTIVE:
            resolved.append(source)
        else:
            unresolved.append(source)

    return resolved, unresolved
