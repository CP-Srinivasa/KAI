"""
Social Connector Base
======================
Abstract base for all social media / news API connectors.

Status model (per connector instance):
  ACTIVE       — configured and operational
  REQUIRES_API — API key/token not set
  DISABLED     — explicitly disabled
  PLANNED      — not yet implemented, placeholder only
  RATE_LIMITED — temporarily blocked

Every connector exposes:
  - status()        → ConnectorStatus
  - healthcheck()   → dict
  - fetch(query)    → list[SocialPost]

Connectors are read-only. No write/post/like operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ConnectorStatus(str, Enum):
    ACTIVE = "active"
    REQUIRES_API = "requires_api"
    DISABLED = "disabled"
    PLANNED = "planned"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


@dataclass
class SocialPost:
    """Normalized output from any social connector."""
    post_id: str
    source_connector: str           # "reddit", "twitter", "google_news", etc.
    title: str = ""
    body: str = ""
    url: str = ""
    author: str = ""
    published_at: datetime | None = None
    score: int = 0                  # upvotes / likes / relevance score
    comment_count: int = 0
    subreddit: str = ""             # Reddit-specific
    sentiment_hint: str = ""        # pre-signal if available
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return f"{self.title} {self.body}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "source_connector": self.source_connector,
            "title": self.title,
            "body": self.body[:500],
            "url": self.url,
            "author": self.author,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "score": self.score,
            "comment_count": self.comment_count,
            "subreddit": self.subreddit,
            "tags": self.tags,
        }


@dataclass
class FetchParams:
    """Common fetch parameters for social connectors."""
    query: str = ""
    subreddit: str = ""             # Reddit
    max_results: int = 25
    sort: str = "relevance"         # relevance | date | score
    time_filter: str = "day"        # hour | day | week | month


class BaseSocialConnector(ABC):
    """Abstract base for social/news connectors."""

    @property
    @abstractmethod
    def connector_id(self) -> str:
        """Unique identifier e.g. 'reddit', 'twitter'."""
        ...

    @property
    @abstractmethod
    def status(self) -> ConnectorStatus:
        """Current operational status."""
        ...

    @property
    @abstractmethod
    def requires_action(self) -> str:
        """Human-readable setup instruction when not ACTIVE."""
        ...

    @abstractmethod
    async def fetch(self, params: FetchParams) -> list[SocialPost]:
        """Fetch posts/articles matching params. Returns [] if not active."""
        ...

    def healthcheck(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "status": self.status.value,
            "healthy": self.status == ConnectorStatus.ACTIVE,
            "requires_action": self.requires_action if self.status != ConnectorStatus.ACTIVE else "",
        }

    def to_registry_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "status": self.status.value,
            "requires_action": self.requires_action,
        }
