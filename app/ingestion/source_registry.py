"""
Source Registry
===============
Central, in-memory registry of all configured ingestion sources.
Sources are loaded from monitor/ files and config at startup.

Responsibilities:
- Hold metadata for all known sources
- Provide lookup by ID, type, status
- Separate concerns: registry knows ABOUT sources, adapters DO the fetching

Sources are classified by type. Not every source is fetchable:
  active    → ready to ingest
  requires_api → needs API key config
  manual_resolution → needs human action
  disabled  → intentionally off
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SourceEntry:
    """
    Lightweight descriptor for a registered source.
    Does not hold adapter logic — only metadata.
    """
    source_id: str
    source_name: str
    source_type: SourceType
    status: SourceStatus
    url: str = ""
    language: str = "en"
    country: str = ""
    categories: list[str] = field(default_factory=list)
    auth_mode: AuthMode = AuthMode.NONE
    credibility_score: float = 0.5
    notes: str = ""
    requires_action: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == SourceStatus.ACTIVE

    @property
    def is_fetchable(self) -> bool:
        return self.status in (SourceStatus.ACTIVE,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_type": self.source_type.value,
            "status": self.status.value,
            "url": self.url,
            "language": self.language,
            "categories": self.categories,
            "credibility_score": self.credibility_score,
            "notes": self.notes,
            "requires_action": self.requires_action,
            "is_fetchable": self.is_fetchable,
        }


class SourceRegistry:
    """
    In-memory registry of all configured sources.
    Thread-safe for reads. Use build() to populate from config.
    """

    def __init__(self) -> None:
        self._sources: dict[str, SourceEntry] = {}

    def register(self, entry: SourceEntry) -> None:
        if entry.source_id in self._sources:
            logger.debug("source_registry_overwrite", source_id=entry.source_id)
        self._sources[entry.source_id] = entry

    def register_many(self, entries: list[SourceEntry]) -> None:
        for entry in entries:
            self.register(entry)
        logger.info("sources_registered", count=len(entries))

    def get(self, source_id: str) -> SourceEntry | None:
        return self._sources.get(source_id)

    def all(self) -> list[SourceEntry]:
        return list(self._sources.values())

    def by_type(self, source_type: SourceType) -> list[SourceEntry]:
        return [s for s in self._sources.values() if s.source_type == source_type]

    def fetchable(self) -> list[SourceEntry]:
        """Return only sources that can be actively fetched."""
        return [s for s in self._sources.values() if s.is_fetchable]

    def by_status(self, status: SourceStatus) -> list[SourceEntry]:
        return [s for s in self._sources.values() if s.status == status]

    def summary(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for s in self._sources.values():
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
            by_type[s.source_type.value] = by_type.get(s.source_type.value, 0) + 1
        return {
            "total": len(self._sources),
            "fetchable": len(self.fetchable()),
            "by_status": by_status,
            "by_type": by_type,
        }

    def __len__(self) -> int:
        return len(self._sources)


# Module-level singleton — populated at app startup via build_registry()
_registry: SourceRegistry | None = None


def get_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry


def build_registry(
    website_sources: list[dict] | None = None,
    rss_feeds: list[dict] | None = None,
) -> SourceRegistry:
    """
    Build and populate the global registry from structured config data.
    Called once at startup by the orchestrator or CLI.
    """
    global _registry
    registry = SourceRegistry()

    if website_sources:
        for ws in website_sources:
            try:
                entry = SourceEntry(
                    source_id=ws["domain"].replace(".", "_").replace("/", "_"),
                    source_name=ws.get("name", ws["domain"]),
                    source_type=SourceType(ws.get("type", "website")),
                    status=SourceStatus(ws.get("status", "active")),
                    url=f"https://{ws['domain']}",
                    language=ws.get("language", "en"),
                    categories=[ws.get("category", "general")],
                    notes=ws.get("notes", ""),
                )
                registry.register(entry)
            except (KeyError, ValueError) as e:
                logger.warning("website_source_skip", error=str(e), data=ws)

    if rss_feeds:
        for feed in rss_feeds:
            try:
                entry = SourceEntry(
                    source_id=feed["source_id"],
                    source_name=feed.get("title", feed["source_id"]),
                    source_type=SourceType.RSS_FEED,
                    status=SourceStatus(feed.get("status", "active")),
                    url=feed["rss_url"],
                    notes=feed.get("notes", ""),
                )
                registry.register(entry)
            except (KeyError, ValueError) as e:
                logger.warning("rss_feed_skip", error=str(e), data=feed)

    _registry = registry
    logger.info("source_registry_built", **registry.summary())
    return registry
