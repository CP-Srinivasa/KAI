"""
Reddit Connector
=================
Fetches posts from relevant subreddits via Reddit API (PRAW or direct HTTP).
[REQUIRES: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET in .env]

Read-only. No posting, no voting.

Useful subreddits: r/CryptoCurrency, r/Bitcoin, r/ethereum, r/investing,
                   r/SecurityAnalysis, r/stocks

Auth: Reddit API requires OAuth2 app credentials (free tier available).
Rate limit: 60 requests/minute for authenticated apps.
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

_REDDIT_API = "https://oauth.reddit.com"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"


class RedditConnector(BaseSocialConnector):
    """
    Reddit post fetcher via Reddit API.
    [REQUIRES: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET in .env]

    Authentication uses the "script" app type (server-side).
    Does not require user login for reading public subreddits.
    """

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        user_agent: str = "AI-Analyst-Bot/0.1",
        enabled: bool = True,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._enabled = enabled
        self._access_token: str | None = None

    @property
    def connector_id(self) -> str:
        return "reddit"

    @property
    def status(self) -> ConnectorStatus:
        if not self._enabled:
            return ConnectorStatus.DISABLED
        if not self._client_id or not self._client_secret:
            return ConnectorStatus.REQUIRES_API
        return ConnectorStatus.ACTIVE

    @property
    def requires_action(self) -> str:
        if not self._enabled:
            return "Set REDDIT_ENABLED=true in .env"
        if not self._client_id or not self._client_secret:
            return (
                "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env. "
                "Register a free 'script' app at https://www.reddit.com/prefs/apps"
            )
        return ""

    async def fetch(self, params: FetchParams) -> list[SocialPost]:
        if self.status != ConnectorStatus.ACTIVE:
            logger.debug("reddit_connector_inactive", status=self.status.value)
            return []

        subreddit = params.subreddit or "CryptoCurrency"
        try:
            token = await self._get_token()
            if not token:
                return []

            import httpx  # noqa: PLC0415
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": self._user_agent,
            }
            url = f"{_REDDIT_API}/r/{subreddit}/search.json"
            query_params: dict[str, Any] = {
                "q": params.query,
                "sort": params.sort,
                "t": params.time_filter,
                "limit": min(params.max_results, 100),
                "type": "link",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers, params=query_params)
                if resp.status_code == 429:
                    logger.warning("reddit_rate_limited")
                    return []
                resp.raise_for_status()
                data = resp.json()

            posts = []
            for child in data.get("data", {}).get("children", []):
                d = child.get("data", {})
                post = SocialPost(
                    post_id=d.get("id", ""),
                    source_connector="reddit",
                    title=d.get("title", ""),
                    body=d.get("selftext", "")[:1000],
                    url=d.get("url", ""),
                    author=d.get("author", ""),
                    published_at=datetime.fromtimestamp(d.get("created_utc", 0)) if d.get("created_utc") else None,
                    score=d.get("score", 0),
                    comment_count=d.get("num_comments", 0),
                    subreddit=d.get("subreddit", subreddit),
                    tags=d.get("link_flair_text", "").split(",") if d.get("link_flair_text") else [],
                    metadata={"upvote_ratio": d.get("upvote_ratio", 0.5)},
                )
                posts.append(post)

            logger.info("reddit_fetched", subreddit=subreddit, count=len(posts), query=params.query)
            return posts

        except Exception as e:
            logger.error("reddit_fetch_error", error=str(e))
            return []

    async def _get_token(self) -> str | None:
        """Obtain OAuth2 access token using client credentials."""
        if self._access_token:
            return self._access_token
        try:
            import httpx  # noqa: PLC0415
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    _TOKEN_URL,
                    auth=(self._client_id, self._client_secret),
                    data={"grant_type": "client_credentials"},
                    headers={"User-Agent": self._user_agent},
                )
                resp.raise_for_status()
                self._access_token = resp.json().get("access_token")
                return self._access_token
        except Exception as e:
            logger.error("reddit_auth_error", error=str(e))
            return None
