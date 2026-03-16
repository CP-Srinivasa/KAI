"""Tests for app/ingestion/podcasts/resolver_pipeline.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.enums import SourceStatus, SourceType
from app.ingestion.podcasts.resolver_pipeline import (
    PipelineResult,
    run_pipeline,
    register_resolved_podcasts,
)
from app.ingestion.source_registry import SourceRegistry


def _write_raw_feeds(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "podcast_feeds_raw.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


PODIGEE_URL = "https://meinpodcast.podigee.io/feed"
EPICENTER_URL = "https://epicenter.works/feed.rss"
APPLE_URL = "https://podcasts.apple.com/de/podcast/on-the-brink/id1482649698"
SPOTIFY_URL = "https://open.spotify.com/show/5B1KKiQJB5GBqcNKzMtmJl"


class TestPipelineResult:
    def test_summary(self) -> None:
        from app.ingestion.source_registry import SourceEntry

        resolved = [
            SourceEntry(
                source_id="x",
                source_name="X",
                source_type=SourceType.PODCAST_FEED,
                status=SourceStatus.ACTIVE,
                url="https://x.example.com/feed",
            )
        ]
        result = PipelineResult(resolved=resolved, unresolved=[], total_input=3)
        s = result.summary()
        assert s["total_input"] == 3
        assert s["resolved"] == 1
        assert s["unresolved"] == 0

    def test_counts(self) -> None:
        from app.ingestion.resolvers.podcast_resolver import ClassifiedSource, URLCategory
        cs = ClassifiedSource(
            url="https://example.com",
            category=URLCategory.APPLE_PODCAST,
            source_type=SourceType.PODCAST_PAGE,
            status=SourceStatus.REQUIRES_API,
        )
        result = PipelineResult(resolved=[], unresolved=[cs], total_input=1)
        assert result.resolved_count == 0
        assert result.unresolved_count == 1


class TestRunPipeline:
    def test_direct_rss_goes_to_resolved(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [EPICENTER_URL])
        result = run_pipeline(f)
        assert result.resolved_count >= 1
        assert result.unresolved_count == 0

    def test_podigee_resolved_to_rss(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [PODIGEE_URL])
        result = run_pipeline(f)
        assert result.resolved_count == 1
        # RSS URL should contain .podigee.io/feed/mp3
        entry = result.resolved[0]
        assert "podigee.io" in entry.url
        assert "/feed/mp3" in entry.url

    def test_apple_goes_to_unresolved(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [APPLE_URL])
        result = run_pipeline(f)
        assert result.resolved_count == 0
        assert result.unresolved_count == 1
        assert result.unresolved[0].status == SourceStatus.REQUIRES_API

    def test_spotify_goes_to_unresolved(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [SPOTIFY_URL])
        result = run_pipeline(f)
        assert result.resolved_count == 0
        assert result.unresolved_count == 1

    def test_mixed_batch(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [
            PODIGEE_URL,
            EPICENTER_URL,
            APPLE_URL,
            SPOTIFY_URL,
        ])
        result = run_pipeline(f)
        assert result.resolved_count == 2
        assert result.unresolved_count == 2
        assert result.total_input == 4

    def test_comments_skipped(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [
            "# This is a comment",
            EPICENTER_URL,
        ])
        result = run_pipeline(f)
        assert result.total_input == 1  # Comment excluded by load_podcast_feeds_raw

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = run_pipeline(tmp_path / "nonexistent.txt")
        assert result.resolved_count == 0
        assert result.unresolved_count == 0
        assert result.total_input == 0

    def test_resolved_entries_have_active_status(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [EPICENTER_URL])
        result = run_pipeline(f)
        for entry in result.resolved:
            assert entry.status == SourceStatus.ACTIVE

    def test_resolved_entries_have_valid_source_id(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [PODIGEE_URL])
        result = run_pipeline(f)
        entry = result.resolved[0]
        assert entry.source_id
        assert len(entry.source_id) > 0


class TestRegisterResolvedPodcasts:
    def test_registers_resolved_into_registry(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [EPICENTER_URL, PODIGEE_URL])
        registry = SourceRegistry()
        result = register_resolved_podcasts(registry, f)
        assert result.resolved_count == 2
        assert len(registry) == 2

    def test_unresolved_not_registered(self, tmp_path: Path) -> None:
        f = _write_raw_feeds(tmp_path, [APPLE_URL, SPOTIFY_URL])
        registry = SourceRegistry()
        result = register_resolved_podcasts(registry, f)
        assert len(registry) == 0
        assert result.unresolved_count == 2
