"""Tests for Podcast Source Resolver."""

from __future__ import annotations

import pytest

from app.core.enums import SourceStatus, SourceType
from app.ingestion.resolvers.podcast_resolver import (
    URLCategory,
    classify_batch,
    classify_url,
)


class TestApplePodcastClassification:
    def test_apple_podcast_classified(self) -> None:
        url = "https://podcasts.apple.com/de/podcast/bitcoin-verstehen/id1513814577"
        result = classify_url(url)
        assert result.category == URLCategory.APPLE_PODCAST
        assert result.status == SourceStatus.REQUIRES_API
        assert result.source_type == SourceType.PODCAST_PAGE
        assert "1513814577" in (result.source_id or "")
        assert "itunes.apple.com" in result.requires_action


class TestSpotifyClassification:
    def test_spotify_show_classified(self) -> None:
        url = "https://open.spotify.com/show/7sDXM8BlxsUqzL2IqmLqwE"
        result = classify_url(url)
        assert result.category == URLCategory.SPOTIFY_SHOW
        assert result.status == SourceStatus.REQUIRES_API
        assert "7sDXM8BlxsUqzL2IqmLqwE" in (result.source_id or "")

    def test_spotify_anchor_classified(self) -> None:
        url = "https://podcasters.spotify.com/pod/show/teachmedefi"
        result = classify_url(url)
        assert result.category == URLCategory.SPOTIFY_ANCHOR
        assert result.status == SourceStatus.RSS_RESOLUTION_NEEDED


class TestPodigeeClassification:
    def test_podigee_resolves_to_rss(self) -> None:
        url = "https://saschahuber.podigee.io/"
        result = classify_url(url)
        assert result.category == URLCategory.PODIGEE_FEED
        assert result.status == SourceStatus.ACTIVE
        assert result.resolved_rss_url == "https://saschahuber.podigee.io/feed/mp3"

    def test_podigee_source_type(self) -> None:
        url = "https://saschahuber.podigee.io/"
        result = classify_url(url)
        assert result.source_type == SourceType.PODCAST_FEED


class TestReferencePageClassification:
    def test_a16z_crypto_is_reference(self) -> None:
        url = "https://a16zcrypto.com/posts/article/crypto-readings-resources/"
        result = classify_url(url)
        assert result.category == URLCategory.REFERENCE_RESOURCE
        assert result.status == SourceStatus.DISABLED

    def test_coinledger_is_reference(self) -> None:
        url = "https://coinledger.io/bitcoin-rainbow-chart"
        result = classify_url(url)
        assert result.category == URLCategory.REFERENCE_RESOURCE

    def test_tradingview_is_reference(self) -> None:
        url = "https://www.tradingview.com"
        result = classify_url(url)
        assert result.category == URLCategory.REFERENCE_RESOURCE

    def test_coinbase_learn_is_reference(self) -> None:
        url = "https://www.coinbase.com/learn"
        result = classify_url(url)
        assert result.category == URLCategory.REFERENCE_RESOURCE


class TestDirectRSSClassification:
    def test_rss_path_detected(self) -> None:
        url = "https://example.com/feed/rss"
        result = classify_url(url)
        assert result.category == URLCategory.DIRECT_RSS
        assert result.status == SourceStatus.ACTIVE
        assert result.resolved_rss_url == url

    def test_epicenter_episodes_not_rss(self) -> None:
        # /episodes/ is NOT an RSS feed
        url = "https://epicenter.tv/episodes/"
        result = classify_url(url)
        assert result.category != URLCategory.DIRECT_RSS


class TestBatchClassification:
    def test_skips_comments_and_blanks(self) -> None:
        urls = [
            "# This is a comment",
            "",
            "  ",
            "https://open.spotify.com/show/7sDXM8BlxsUqzL2IqmLqwE",
        ]
        results = classify_batch(urls)
        assert len(results) == 1
