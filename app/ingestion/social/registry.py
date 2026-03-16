"""
Social Connector Registry
==========================
Central registry for all social/news connectors.

Builds and holds connector instances from settings.
Provides unified fetch interface across all active connectors.

Usage:
    registry = SocialConnectorRegistry.from_settings(settings)
    posts = await registry.fetch_all(FetchParams(query="Bitcoin ETF"))
    status = registry.status_report()
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.ingestion.social.connectors.base import (
    BaseSocialConnector,
    ConnectorStatus,
    FetchParams,
    SocialPost,
)
from app.ingestion.social.connectors.bing_news import BingNewsConnector
from app.ingestion.social.connectors.facebook import FacebookConnector
from app.ingestion.social.connectors.google_news import GoogleNewsConnector
from app.ingestion.social.connectors.reddit import RedditConnector
from app.ingestion.social.connectors.twitter import TwitterConnector
from app.ingestion.social.connectors.yahoo_news import YahooNewsConnector

logger = get_logger(__name__)


class SocialConnectorRegistry:
    """Holds all social connector instances and provides unified fetch."""

    def __init__(self, connectors: list[BaseSocialConnector] | None = None) -> None:
        self._connectors: dict[str, BaseSocialConnector] = {}
        for c in (connectors or []):
            self._connectors[c.connector_id] = c

    @classmethod
    def from_settings(cls, settings: Any | None = None) -> "SocialConnectorRegistry":
        """
        Build registry from app settings.
        Falls back to environment variables if settings is None.
        """
        import os  # noqa: PLC0415

        def _env(key: str, default: str = "") -> str:
            if settings is None:
                return os.getenv(key, default)
            return getattr(settings, key.lower(), default)

        connectors: list[BaseSocialConnector] = [
            RedditConnector(
                client_id=_env("REDDIT_CLIENT_ID"),
                client_secret=_env("REDDIT_CLIENT_SECRET"),
                enabled=_env("REDDIT_ENABLED", "false").lower() == "true",
            ),
            TwitterConnector(
                bearer_token=_env("TWITTER_BEARER_TOKEN"),
                enabled=_env("TWITTER_ENABLED", "false").lower() == "true",
            ),
            GoogleNewsConnector(
                enabled=_env("GOOGLE_NEWS_ENABLED", "true").lower() == "true",
            ),
            YahooNewsConnector(
                enabled=_env("YAHOO_NEWS_ENABLED", "true").lower() == "true",
            ),
            BingNewsConnector(
                api_key=_env("BING_SEARCH_API_KEY"),
                enabled=_env("BING_NEWS_ENABLED", "false").lower() == "true",
            ),
            FacebookConnector(
                page_access_token=_env("FACEBOOK_PAGE_ACCESS_TOKEN"),
                page_id=_env("FACEBOOK_PAGE_ID"),
                enabled=False,  # Always disabled — requires Meta app approval
            ),
        ]
        return cls(connectors=connectors)

    @classmethod
    def default(cls) -> "SocialConnectorRegistry":
        """Build registry with default (no-key) configuration."""
        return cls.from_settings(None)

    # ── Fetch API ─────────────────────────────────────────────────────────

    async def fetch_all(
        self,
        params: FetchParams,
        connector_ids: list[str] | None = None,
    ) -> list[SocialPost]:
        """
        Fetch from all active connectors (or specified subset).

        Args:
            params:        Shared fetch parameters
            connector_ids: If set, only use these connectors

        Returns:
            Merged list of SocialPost, sorted by score (descending)
        """
        targets = self._active_connectors(connector_ids)
        if not targets:
            return []

        import asyncio  # noqa: PLC0415
        tasks = [c.fetch(params) for c in targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_posts: list[SocialPost] = []
        for connector, result in zip(targets, results):
            if isinstance(result, Exception):
                logger.error(
                    "social_fetch_error",
                    connector=connector.connector_id,
                    error=str(result),
                )
            else:
                all_posts.extend(result)

        # Sort by score (engagement), deduplicate by URL
        seen_urls: set[str] = set()
        deduped: list[SocialPost] = []
        for post in sorted(all_posts, key=lambda p: p.score, reverse=True):
            if post.url and post.url in seen_urls:
                continue
            seen_urls.add(post.url)
            deduped.append(post)

        logger.info(
            "social_fetch_complete",
            connectors=[c.connector_id for c in targets],
            total=len(deduped),
            query=params.query,
        )
        return deduped

    async def fetch_from(
        self,
        connector_id: str,
        params: FetchParams,
    ) -> list[SocialPost]:
        """Fetch from a single connector by ID."""
        connector = self._connectors.get(connector_id)
        if not connector:
            logger.warning("social_connector_not_found", connector_id=connector_id)
            return []
        return await connector.fetch(params)

    # ── Status ────────────────────────────────────────────────────────────

    def status_report(self) -> list[dict[str, Any]]:
        """Return status of all registered connectors."""
        return [c.healthcheck() for c in self._connectors.values()]

    def get_connector(self, connector_id: str) -> BaseSocialConnector | None:
        return self._connectors.get(connector_id)

    def active_connector_ids(self) -> list[str]:
        return [
            cid for cid, c in self._connectors.items()
            if c.status == ConnectorStatus.ACTIVE
        ]

    def _active_connectors(
        self,
        ids: list[str] | None = None,
    ) -> list[BaseSocialConnector]:
        if ids:
            return [
                self._connectors[cid]
                for cid in ids
                if cid in self._connectors
                and self._connectors[cid].status == ConnectorStatus.ACTIVE
            ]
        return [c for c in self._connectors.values() if c.status == ConnectorStatus.ACTIVE]
