"""Tests for app/ingestion/youtube/registry.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.ingestion.source_registry import SourceRegistry
from app.ingestion.youtube.registry import (
    build_youtube_registry,
    register_youtube_channels,
    _channel_to_source_id,
    _channel_to_entry,
)
from app.ingestion.resolvers.youtube_resolver import YouTubeChannel


def _write_channels_file(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "youtube_channels.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class TestChannelToSourceId:
    def test_handle_based_id(self) -> None:
        ch = YouTubeChannel(
            original_url="https://www.youtube.com/@CoinBureau",
            normalized_url="https://www.youtube.com/@CoinBureau",
            handle="@CoinBureau",
            channel_id=None,
            url_type="handle",
        )
        source_id = _channel_to_source_id(ch)
        assert source_id == "youtube_CoinBureau"
        assert "@" not in source_id

    def test_channel_id_based(self) -> None:
        ch = YouTubeChannel(
            original_url="https://www.youtube.com/channel/UCpPnsOUPkWcuHM4p1Ntun3A",
            normalized_url="https://www.youtube.com/channel/UCpPnsOUPkWcuHM4p1Ntun3A",
            handle=None,
            channel_id="UCpPnsOUPkWcuHM4p1Ntun3A",
            url_type="channel_id",
        )
        source_id = _channel_to_source_id(ch)
        assert source_id == "youtube_UCpPnsOUPkWcuHM4p1Ntun3A"

    def test_no_handle_or_channel_id(self) -> None:
        ch = YouTubeChannel(
            original_url="https://www.youtube.com/c/SomeName",
            normalized_url="https://www.youtube.com/c/SomeName",
            handle=None,
            channel_id=None,
            url_type="custom",
        )
        source_id = _channel_to_source_id(ch)
        assert source_id.startswith("youtube_")


class TestChannelToEntry:
    def test_requires_api_status(self) -> None:
        ch = YouTubeChannel(
            original_url="https://www.youtube.com/@InvestAnswers",
            normalized_url="https://www.youtube.com/@InvestAnswers",
            handle="@InvestAnswers",
            channel_id=None,
            url_type="handle",
        )
        entry = _channel_to_entry(ch)
        assert entry.status == SourceStatus.REQUIRES_API
        assert not entry.is_fetchable

    def test_source_type_is_youtube(self) -> None:
        ch = YouTubeChannel(
            original_url="https://www.youtube.com/@InvestAnswers",
            normalized_url="https://www.youtube.com/@InvestAnswers",
            handle="@InvestAnswers",
            channel_id=None,
            url_type="handle",
        )
        entry = _channel_to_entry(ch)
        assert entry.source_type == SourceType.YOUTUBE_CHANNEL

    def test_auth_mode_is_api_key(self) -> None:
        ch = YouTubeChannel(
            original_url="https://www.youtube.com/@CoinBureau",
            normalized_url="https://www.youtube.com/@CoinBureau",
            handle="@CoinBureau",
            channel_id=None,
            url_type="handle",
        )
        entry = _channel_to_entry(ch)
        assert entry.auth_mode == AuthMode.API_KEY

    def test_requires_action_set(self) -> None:
        ch = YouTubeChannel(
            original_url="https://www.youtube.com/@CoinBureau",
            normalized_url="https://www.youtube.com/@CoinBureau",
            handle="@CoinBureau",
            channel_id=None,
            url_type="handle",
        )
        entry = _channel_to_entry(ch)
        assert "YOUTUBE_API_KEY" in entry.requires_action

    def test_config_contains_metadata(self) -> None:
        ch = YouTubeChannel(
            original_url="https://www.youtube.com/@CoinBureau",
            normalized_url="https://www.youtube.com/@CoinBureau",
            handle="@CoinBureau",
            channel_id=None,
            url_type="handle",
        )
        entry = _channel_to_entry(ch)
        assert entry.config["url_type"] == "handle"
        assert entry.config["handle"] == "@CoinBureau"


class TestBuildYoutubeRegistry:
    def test_loads_from_file(self, tmp_path: Path) -> None:
        f = _write_channels_file(tmp_path, [
            "https://www.youtube.com/@CoinBureau",
            "https://www.youtube.com/@InvestAnswers",
        ])
        entries = build_youtube_registry(f)
        assert len(entries) == 2

    def test_deduplication(self, tmp_path: Path) -> None:
        f = _write_channels_file(tmp_path, [
            "https://www.youtube.com/@CoinBureau",
            "https://www.youtube.com/@CoinBureau",  # duplicate
        ])
        entries = build_youtube_registry(f)
        assert len(entries) == 1

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        entries = build_youtube_registry(tmp_path / "nonexistent.txt")
        assert entries == []

    def test_comments_skipped(self, tmp_path: Path) -> None:
        f = _write_channels_file(tmp_path, [
            "# This is a channel list",
            "https://www.youtube.com/@CoinBureau",
        ])
        entries = build_youtube_registry(f)
        assert len(entries) == 1

    def test_all_require_api(self, tmp_path: Path) -> None:
        f = _write_channels_file(tmp_path, [
            "https://www.youtube.com/@CoinBureau",
            "https://www.youtube.com/@InvestAnswers",
            "https://www.youtube.com/@TimTalksMoney",
        ])
        entries = build_youtube_registry(f)
        assert all(e.status == SourceStatus.REQUIRES_API for e in entries)
        assert all(not e.is_fetchable for e in entries)


class TestRegisterYoutubeChannels:
    def test_registers_into_registry(self, tmp_path: Path) -> None:
        f = _write_channels_file(tmp_path, [
            "https://www.youtube.com/@CoinBureau",
            "https://www.youtube.com/@InvestAnswers",
        ])
        registry = SourceRegistry()
        count = register_youtube_channels(registry, f)
        assert count == 2
        assert len(registry) == 2

    def test_empty_file_registers_nothing(self, tmp_path: Path) -> None:
        f = _write_channels_file(tmp_path, [])
        registry = SourceRegistry()
        count = register_youtube_channels(registry, f)
        assert count == 0
        assert len(registry) == 0
