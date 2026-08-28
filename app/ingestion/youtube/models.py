"""Gemeinsames Video-Modell fuer Feed-Parser und Adapter.

Eigene Datei, damit ``feed.py`` und ``adapter.py`` sich nicht gegenseitig
importieren muessen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    title: str
    description: str
    channel_id: str
    channel_title: str
    published_at: str
    thumbnail_url: str | None = None
