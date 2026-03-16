"""
Tests for social connector configs, status handling, and registry.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.ingestion.social.connectors.base import (
    BaseSocialConnector,
    ConnectorStatus,
    FetchParams,
    SocialPost,
)
from app.ingestion.social.connectors.reddit import RedditConnector
from app.ingestion.social.connectors.twitter import TwitterConnector
from app.ingestion.social.connectors.google_news import GoogleNewsConnector
from app.ingestion.social.connectors.yahoo_news import YahooNewsConnector
from app.ingestion.social.connectors.bing_news import BingNewsConnector
from app.ingestion.social.connectors.facebook import FacebookConnector
from app.ingestion.social.registry import SocialConnectorRegistry


# ─────────────────────────────────────────────
# SocialPost
# ─────────────────────────────────────────────

class TestSocialPost:
    def test_basic_post(self):
        post = SocialPost(
            post_id="p1",
            source_connector="reddit",
            title="Bitcoin hits ATH",
            url="https://reddit.com/r/bitcoin/1",
            score=150,
        )
        assert post.title == "Bitcoin hits ATH"
        assert post.score == 150
        assert post.source_connector == "reddit"

    def test_to_dict(self):
        post = SocialPost(
            post_id="p2",
            source_connector="twitter",
            title="ETH upgrade live",
            url="https://twitter.com/status/123",
            body="Ethereum just completed the merge.",
            score=42,
        )
        d = post.to_dict()
        assert d["post_id"] == "p2"
        assert d["title"] == "ETH upgrade live"
        assert d["score"] == 42
        assert d["source_connector"] == "twitter"

    def test_default_score_zero(self):
        post = SocialPost(post_id="p3", source_connector="google_news", title="BTC news")
        assert post.score == 0

    def test_full_text_combines_title_body(self):
        post = SocialPost(
            post_id="p4",
            source_connector="reddit",
            title="BTC bull",
            body="market is pumping",
        )
        assert "BTC bull" in post.full_text
        assert "market is pumping" in post.full_text


# ─────────────────────────────────────────────
# FetchParams
# ─────────────────────────────────────────────

class TestFetchParams:
    def test_defaults(self):
        params = FetchParams(query="Bitcoin ETF")
        assert params.query == "Bitcoin ETF"
        assert params.max_results == 25   # actual default in source
        assert params.subreddit == ""

    def test_custom_params(self):
        params = FetchParams(
            query="crypto",
            subreddit="CryptoCurrency",
            max_results=10,
            sort="hot",
            time_filter="week",
        )
        assert params.subreddit == "CryptoCurrency"
        assert params.max_results == 10
        assert params.time_filter == "week"


# ─────────────────────────────────────────────
# ConnectorStatus
# ─────────────────────────────────────────────

class TestConnectorStatus:
    def test_values(self):
        assert ConnectorStatus.ACTIVE.value == "active"
        assert ConnectorStatus.REQUIRES_API.value == "requires_api"
        assert ConnectorStatus.DISABLED.value == "disabled"
        assert ConnectorStatus.PLANNED.value == "planned"
        assert ConnectorStatus.RATE_LIMITED.value == "rate_limited"
        assert ConnectorStatus.ERROR.value == "error"


# ─────────────────────────────────────────────
# RedditConnector
# ─────────────────────────────────────────────

class TestRedditConnector:
    def test_disabled_without_credentials(self):
        c = RedditConnector(client_id="", client_secret="", enabled=True)
        assert c.status == ConnectorStatus.REQUIRES_API

    def test_disabled_when_enabled_false(self):
        c = RedditConnector(client_id="id", client_secret="secret", enabled=False)
        assert c.status == ConnectorStatus.DISABLED

    def test_active_with_credentials(self):
        c = RedditConnector(client_id="id", client_secret="secret", enabled=True)
        assert c.status == ConnectorStatus.ACTIVE

    def test_connector_id(self):
        c = RedditConnector(client_id="", client_secret="")
        assert c.connector_id == "reddit"

    def test_healthcheck_shape(self):
        c = RedditConnector(client_id="id", client_secret="secret", enabled=True)
        hc = c.healthcheck()
        assert hc["connector_id"] == "reddit"
        assert "status" in hc
        assert "requires_action" in hc

    def test_fetch_returns_empty_when_disabled(self):
        import asyncio
        c = RedditConnector(client_id="", client_secret="", enabled=False)
        params = FetchParams(query="bitcoin")
        result = asyncio.get_event_loop().run_until_complete(c.fetch(params))
        assert result == []


# ─────────────────────────────────────────────
# TwitterConnector
# ─────────────────────────────────────────────

class TestTwitterConnector:
    def test_disabled_without_token(self):
        c = TwitterConnector(bearer_token="", enabled=True)
        assert c.status == ConnectorStatus.REQUIRES_API

    def test_active_with_token(self):
        c = TwitterConnector(bearer_token="test_token", enabled=True)
        assert c.status == ConnectorStatus.ACTIVE

    def test_connector_id(self):
        c = TwitterConnector(bearer_token="")
        assert c.connector_id == "twitter"

    def test_fetch_returns_empty_when_disabled(self):
        import asyncio
        c = TwitterConnector(bearer_token="", enabled=False)
        params = FetchParams(query="ethereum")
        result = asyncio.get_event_loop().run_until_complete(c.fetch(params))
        assert result == []


# ─────────────────────────────────────────────
# GoogleNewsConnector
# ─────────────────────────────────────────────

class TestGoogleNewsConnector:
    def test_active_by_default(self):
        c = GoogleNewsConnector(enabled=True)
        assert c.status == ConnectorStatus.ACTIVE

    def test_disabled_when_false(self):
        c = GoogleNewsConnector(enabled=False)
        assert c.status == ConnectorStatus.DISABLED

    def test_connector_id(self):
        c = GoogleNewsConnector()
        assert c.connector_id == "google_news"

    def test_requires_no_api_key(self):
        c = GoogleNewsConnector(enabled=True)
        assert not c.requires_action

    def test_fetch_returns_empty_when_disabled(self):
        import asyncio
        c = GoogleNewsConnector(enabled=False)
        params = FetchParams(query="BTC")
        result = asyncio.get_event_loop().run_until_complete(c.fetch(params))
        assert result == []


# ─────────────────────────────────────────────
# YahooNewsConnector
# ─────────────────────────────────────────────

class TestYahooNewsConnector:
    def test_active_by_default(self):
        c = YahooNewsConnector(enabled=True)
        assert c.status == ConnectorStatus.ACTIVE

    def test_connector_id(self):
        c = YahooNewsConnector()
        assert c.connector_id == "yahoo_news"

    def test_requires_no_api_key(self):
        c = YahooNewsConnector(enabled=True)
        assert not c.requires_action


# ─────────────────────────────────────────────
# BingNewsConnector
# ─────────────────────────────────────────────

class TestBingNewsConnector:
    def test_disabled_without_key(self):
        c = BingNewsConnector(api_key="", enabled=True)
        assert c.status == ConnectorStatus.REQUIRES_API

    def test_active_with_key(self):
        c = BingNewsConnector(api_key="mykey", enabled=True)
        assert c.status == ConnectorStatus.ACTIVE

    def test_connector_id(self):
        c = BingNewsConnector(api_key="")
        assert c.connector_id == "bing_news"

    def test_fetch_returns_empty_when_disabled(self):
        import asyncio
        c = BingNewsConnector(api_key="", enabled=False)
        params = FetchParams(query="solana")
        result = asyncio.get_event_loop().run_until_complete(c.fetch(params))
        assert result == []


# ─────────────────────────────────────────────
# FacebookConnector
# ─────────────────────────────────────────────

class TestFacebookConnector:
    def test_planned_when_disabled(self):
        """Without enabled=True, Facebook connector is PLANNED."""
        c = FacebookConnector()
        assert c.status == ConnectorStatus.PLANNED

    def test_connector_id(self):
        c = FacebookConnector()
        assert c.connector_id == "facebook"

    def test_fetch_returns_empty(self):
        """fetch() always returns [] — even with token + enabled."""
        import asyncio
        c = FacebookConnector()
        params = FetchParams(query="bitcoin")
        result = asyncio.get_event_loop().run_until_complete(c.fetch(params))
        assert result == []

    def test_requires_action_not_empty(self):
        c = FacebookConnector()
        assert c.requires_action  # Has instructions string


# ─────────────────────────────────────────────
# SocialConnectorRegistry
# ─────────────────────────────────────────────

class TestSocialConnectorRegistry:
    def test_default_registry_builds(self):
        """from_settings() with no env vars should build without error."""
        registry = SocialConnectorRegistry.default()
        assert registry is not None

    def test_default_has_all_connectors(self):
        registry = SocialConnectorRegistry.default()
        ids = [c.connector_id for c in registry._connectors.values()]
        assert "reddit" in ids
        assert "twitter" in ids
        assert "google_news" in ids
        assert "yahoo_news" in ids
        assert "bing_news" in ids
        assert "facebook" in ids

    def test_status_report_has_all(self):
        registry = SocialConnectorRegistry.default()
        report = registry.status_report()
        assert len(report) == 6
        for item in report:
            assert "connector_id" in item
            assert "status" in item

    def test_active_connector_ids_returns_active_only(self):
        registry = SocialConnectorRegistry.default()
        active_ids = registry.active_connector_ids()
        # google_news and yahoo_news should be active (no API key needed)
        assert "google_news" in active_ids
        assert "yahoo_news" in active_ids
        # reddit needs key → not active in default config
        assert "reddit" not in active_ids

    def test_get_connector(self):
        registry = SocialConnectorRegistry.default()
        c = registry.get_connector("google_news")
        assert c is not None
        assert c.connector_id == "google_news"

    def test_get_connector_missing(self):
        registry = SocialConnectorRegistry.default()
        assert registry.get_connector("nonexistent") is None

    def test_fetch_all_returns_empty_when_no_active(self):
        """If all connectors are inactive, fetch_all returns []."""
        import asyncio

        class InactiveConnector(BaseSocialConnector):
            @property
            def connector_id(self) -> str:
                return "inactive"

            @property
            def status(self) -> ConnectorStatus:
                return ConnectorStatus.DISABLED

            @property
            def requires_action(self) -> str:
                return ""

            async def fetch(self, params: FetchParams):
                return []

        registry = SocialConnectorRegistry(connectors=[InactiveConnector()])
        params = FetchParams(query="test")
        result = asyncio.get_event_loop().run_until_complete(
            registry.fetch_all(params)
        )
        assert result == []

    def test_fetch_all_deduplicates_by_url(self):
        """Duplicate URLs across connectors should be deduped."""
        import asyncio

        post_a = SocialPost(
            post_id="1", source_connector="c1", title="Post",
            url="https://example.com/a", score=10
        )
        post_b = SocialPost(
            post_id="2", source_connector="c2", title="Post",
            url="https://example.com/a", score=5
        )

        class FakeConnector(BaseSocialConnector):
            def __init__(self, cid, posts):
                self._cid = cid
                self._posts = posts

            @property
            def connector_id(self): return self._cid
            @property
            def status(self): return ConnectorStatus.ACTIVE
            @property
            def requires_action(self): return ""

            async def fetch(self, params): return self._posts

        registry = SocialConnectorRegistry(connectors=[
            FakeConnector("c1", [post_a]),
            FakeConnector("c2", [post_b]),
        ])
        params = FetchParams(query="test")
        result = asyncio.get_event_loop().run_until_complete(
            registry.fetch_all(params)
        )
        # Only one post with the duplicate URL
        urls = [p.url for p in result]
        assert urls.count("https://example.com/a") == 1

    def test_fetch_from_specific_connector(self):
        """fetch_from dispatches to only the named connector."""
        import asyncio

        post = SocialPost(
            post_id="x1", source_connector="google_news",
            title="Google News Post", url="https://news.google.com/x1",
        )

        registry = SocialConnectorRegistry.default()
        google = registry.get_connector("google_news")
        assert google is not None

        with patch.object(google, "fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [post]
            params = FetchParams(query="BTC")
            result = asyncio.get_event_loop().run_until_complete(
                registry.fetch_from("google_news", params)
            )

        assert len(result) == 1
        assert result[0].title == "Google News Post"
