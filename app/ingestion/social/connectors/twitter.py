"""
Twitter/X Connector
====================
Read-only search via Twitter API v2 (Bearer Token auth).
[REQUIRES: TWITTER_BEARER_TOKEN in .env]

Free tier: 500,000 tweets/month read, 1 app-level token.
Basic tier: higher limits available.

Only uses: GET /2/tweets/search/recent

No posting, no DMs, no account access.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.ingestion.social.connectors.base import (
    BaseSocialConnector,
    ConnectorStatus,
    FetchParams,
    SocialPost,
)

logger = get_logger(__name__)

_API_BASE = "https://api.twitter.com/2"


class TwitterConnector(BaseSocialConnector):
    """
    Twitter/X search connector.
    [REQUIRES: TWITTER_BEARER_TOKEN in .env]

    Uses Twitter API v2 search/recent endpoint.
    Bearer token only — no user login needed.
    """

    def __init__(
        self,
        bearer_token: str = "",
        enabled: bool = True,
    ) -> None:
        self._bearer_token = bearer_token
        self._enabled = enabled

    @property
    def connector_id(self) -> str:
        return "twitter"

    @property
    def status(self) -> ConnectorStatus:
        if not self._enabled:
            return ConnectorStatus.DISABLED
        if not self._bearer_token:
            return ConnectorStatus.REQUIRES_API
        return ConnectorStatus.ACTIVE

    @property
    def requires_action(self) -> str:
        if not self._enabled:
            return "Set TWITTER_ENABLED=true in .env"
        if not self._bearer_token:
            return (
                "Set TWITTER_BEARER_TOKEN in .env. "
                "Get a free Bearer Token at https://developer.twitter.com/en/apps"
            )
        return ""

    async def fetch(self, params: FetchParams) -> list[SocialPost]:
        if self.status != ConnectorStatus.ACTIVE:
            logger.debug("twitter_connector_inactive", status=self.status.value)
            return []

        if not params.query:
            return []

        try:
            import httpx  # noqa: PLC0415
            # Add -is:retweet to filter noise; lang:en for English
            query = f"{params.query} -is:retweet lang:en"
            request_params: dict[str, Any] = {
                "query": query,
                "max_results": min(max(params.max_results, 10), 100),
                "tweet.fields": "created_at,author_id,public_metrics,lang",
                "expansions": "author_id",
                "user.fields": "username,name",
            }
            headers = {
                "Authorization": f"Bearer {self._bearer_token}",
                "User-Agent": "AI-Analyst-Bot/0.1",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{_API_BASE}/tweets/search/recent",
                    params=request_params,
                    headers=headers,
                )
                if resp.status_code == 429:
                    logger.warning("twitter_rate_limited")
                    return []
                if resp.status_code == 403:
                    logger.error("twitter_auth_error", status=403)
                    return []
                resp.raise_for_status()
                data = resp.json()

            # Build author lookup
            users = {
                u["id"]: u
                for u in data.get("includes", {}).get("users", [])
            }

            posts = []
            for tweet in data.get("data", []):
                metrics = tweet.get("public_metrics", {})
                author = users.get(tweet.get("author_id", ""), {})
                created = tweet.get("created_at", "")
                try:
                    pub_at = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
                except (ValueError, TypeError):
                    pub_at = None

                posts.append(SocialPost(
                    post_id=tweet.get("id", ""),
                    source_connector="twitter",
                    title="",
                    body=tweet.get("text", ""),
                    url=f"https://twitter.com/i/web/status/{tweet.get('id', '')}",
                    author=author.get("username", ""),
                    published_at=pub_at,
                    score=metrics.get("like_count", 0),
                    comment_count=metrics.get("reply_count", 0),
                    metadata={
                        "retweet_count": metrics.get("retweet_count", 0),
                        "impression_count": metrics.get("impression_count", 0),
                    },
                ))

            logger.info("twitter_fetched", count=len(posts), query=params.query)
            return posts

        except Exception as e:
            logger.error("twitter_fetch_error", error=str(e))
            return []
