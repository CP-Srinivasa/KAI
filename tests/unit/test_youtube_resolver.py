"""Tests for YouTube Channel Resolver."""

from __future__ import annotations

import pytest

from app.ingestion.resolvers.youtube_resolver import normalize_channel_url


class TestYouTubeURLNormalization:
    def test_handle_format(self) -> None:
        ch = normalize_channel_url("https://www.youtube.com/@Bankless")
        assert ch.url_type == "handle"
        assert ch.handle == "@Bankless"
        assert ch.normalized_url == "https://www.youtube.com/@Bankless"

    def test_handle_with_trailing_slash(self) -> None:
        ch = normalize_channel_url("https://www.youtube.com/@CoinBureau/")
        assert ch.url_type == "handle"
        assert ch.handle == "@CoinBureau"

    def test_handle_with_featured(self) -> None:
        ch = normalize_channel_url("https://www.youtube.com/@AnthonyPompliano/featured")
        assert ch.url_type == "handle"
        assert ch.handle == "@AnthonyPompliano"

    def test_custom_c_format(self) -> None:
        ch = normalize_channel_url("https://www.youtube.com/c/JacobCryptoBury")
        assert ch.url_type == "custom"
        assert ch.normalized_url == "https://www.youtube.com/c/JacobCryptoBury"

    def test_channel_id_format(self) -> None:
        ch = normalize_channel_url("https://www.youtube.com/channel/UCxxxxxx123")
        assert ch.url_type == "channel_id"
        assert ch.channel_id == "UCxxxxxx123"

    def test_unknown_format(self) -> None:
        ch = normalize_channel_url("https://example.com/notayoutubechannel")
        assert ch.url_type == "unknown"

    def test_query_params_stripped(self) -> None:
        ch = normalize_channel_url("https://www.youtube.com/@Bankless?sub=1")
        assert "?" not in ch.normalized_url
