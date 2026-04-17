"""X/Twitter API v2 client — recent search + user timeline.

Uses Bearer Token (App-only auth). Rate limits:
- /2/tweets/search/recent: 450 req/15min (App), 180 req/15min (User)
- /2/users/:id/tweets: 1500 req/15min (App)

API docs: https://developer.x.com/en/docs/twitter-api/tweets
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.x.com/2"
_DEFAULT_TIMEOUT = 15
_DEFAULT_MAX_RESULTS = 10

_TWEET_FIELDS = "created_at,author_id,public_metrics,lang,source,entities"
_USER_FIELDS = "name,username,description,public_metrics,verified"


@dataclass(frozen=True)
class Tweet:
    tweet_id: str
    text: str
    author_id: str
    author_username: str
    author_name: str
    created_at: datetime
    lang: str
    retweet_count: int = 0
    like_count: int = 0
    reply_count: int = 0
    quote_count: int = 0
    impression_count: int = 0
    hashtags: list[str] = field(default_factory=list)
    cashtags: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TwitterUser:
    user_id: str
    username: str
    name: str
    description: str
    followers_count: int = 0
    tweet_count: int = 0
    verified: bool = False


class TwitterClient:
    """Async X/Twitter API v2 client using Bearer Token auth."""

    def __init__(self, bearer_token: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._bearer_token = bearer_token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._bearer_token}"}

    async def search_recent(
        self,
        query: str,
        *,
        max_results: int = _DEFAULT_MAX_RESULTS,
        sort_order: str = "recency",
    ) -> list[Tweet]:
        """Search recent tweets (last 7 days) matching a query."""
        params: dict[str, Any] = {
            "query": query,
            "max_results": max(10, min(max_results, 100)),
            "sort_order": sort_order,
            "tweet.fields": _TWEET_FIELDS,
            "expansions": "author_id",
            "user.fields": _USER_FIELDS,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{_BASE_URL}/tweets/search/recent",
                params=params,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        users = _index_users(data.get("includes", {}).get("users", []))
        return [_parse_tweet(t, users) for t in data.get("data", [])]

    async def get_user_by_username(self, username: str) -> TwitterUser | None:
        """Lookup a single user by @handle (without the @)."""
        clean = username.lstrip("@")
        params = {"user.fields": _USER_FIELDS}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{_BASE_URL}/users/by/username/{clean}",
                params=params,
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json().get("data")
        if not data:
            return None
        return _parse_user(data)

    async def get_user_tweets(
        self,
        user_id: str,
        *,
        max_results: int = _DEFAULT_MAX_RESULTS,
        exclude_retweets: bool = True,
    ) -> list[Tweet]:
        """Get recent tweets from a specific user by user ID."""
        params: dict[str, Any] = {
            "max_results": max(5, min(max_results, 100)),
            "tweet.fields": _TWEET_FIELDS,
            "expansions": "author_id",
            "user.fields": _USER_FIELDS,
        }
        if exclude_retweets:
            params["exclude"] = "retweets"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{_BASE_URL}/users/{user_id}/tweets",
                params=params,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        users = _index_users(data.get("includes", {}).get("users", []))
        return [_parse_tweet(t, users) for t in data.get("data", [])]

    async def fetch_watchlist_tweets(
        self,
        handles: list[str],
        *,
        max_per_user: int = 5,
    ) -> list[Tweet]:
        """Fetch recent tweets from a list of @handles. Fail-soft per user."""
        all_tweets: list[Tweet] = []
        for handle in handles:
            try:
                user = await self.get_user_by_username(handle)
                if user is None:
                    logger.debug("twitter: user not found: %s", handle)
                    continue
                tweets = await self.get_user_tweets(
                    user.user_id, max_results=max_per_user,
                )
                all_tweets.extend(tweets)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("twitter: rate limited at @%s, stopping batch", handle)
                    break
                logger.warning("twitter: HTTP %d for @%s", exc.response.status_code, handle)
            except Exception as exc:
                logger.warning("twitter: error fetching @%s: %s", handle, exc)
        return all_tweets


def _index_users(raw_users: list[dict]) -> dict[str, dict]:
    return {u["id"]: u for u in raw_users if "id" in u}


def _parse_user(raw: dict) -> TwitterUser:
    metrics = raw.get("public_metrics", {})
    return TwitterUser(
        user_id=raw["id"],
        username=raw.get("username", ""),
        name=raw.get("name", ""),
        description=raw.get("description", ""),
        followers_count=metrics.get("followers_count", 0),
        tweet_count=metrics.get("tweet_count", 0),
        verified=raw.get("verified", False),
    )


def _parse_tweet(raw: dict, users: dict[str, dict]) -> Tweet:
    author_id = raw.get("author_id", "")
    author = users.get(author_id, {})
    metrics = raw.get("public_metrics", {})
    entities = raw.get("entities", {})

    created_str = raw.get("created_at", "")
    try:
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        created_at = datetime.now(UTC)

    hashtags = [h["tag"] for h in entities.get("hashtags", []) if "tag" in h]
    cashtags = [c["tag"] for c in entities.get("cashtags", []) if "tag" in c]
    urls = [u["expanded_url"] for u in entities.get("urls", []) if "expanded_url" in u]

    return Tweet(
        tweet_id=raw.get("id", ""),
        text=raw.get("text", ""),
        author_id=author_id,
        author_username=author.get("username", ""),
        author_name=author.get("name", ""),
        created_at=created_at,
        lang=raw.get("lang", ""),
        retweet_count=metrics.get("retweet_count", 0),
        like_count=metrics.get("like_count", 0),
        reply_count=metrics.get("reply_count", 0),
        quote_count=metrics.get("quote_count", 0),
        impression_count=metrics.get("impression_count", 0),
        hashtags=hashtags,
        cashtags=cashtags,
        urls=urls,
    )
