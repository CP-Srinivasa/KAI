"""Tests for app/ingestion/websites/registry.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.enums import SourceStatus, SourceType
from app.ingestion.source_registry import SourceRegistry
from app.ingestion.websites.registry import build_website_registry, register_websites


def _write_sources_file(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "website_sources.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _write_domains_file(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "news_domains.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class TestBuildWebsiteRegistry:
    def test_basic_load(self, tmp_path: Path) -> None:
        f = _write_sources_file(tmp_path, [
            "coindesk.com|CoinDesk|website|en|crypto|active",
        ])
        entries = build_website_registry(f)
        assert len(entries) == 1
        assert entries[0].source_name == "CoinDesk"
        assert entries[0].source_type == SourceType.WEBSITE
        assert entries[0].status == SourceStatus.ACTIVE

    def test_url_constructed_from_domain(self, tmp_path: Path) -> None:
        f = _write_sources_file(tmp_path, [
            "bloomberg.com|Bloomberg|website|en|finance|active",
        ])
        entries = build_website_registry(f)
        assert entries[0].url == "https://bloomberg.com"

    def test_credibility_from_domains_file(self, tmp_path: Path) -> None:
        sf = _write_sources_file(tmp_path, [
            "coindesk.com|CoinDesk|website|en|crypto|active",
        ])
        df = _write_domains_file(tmp_path, [
            "coindesk.com|0.82|crypto|en",
        ])
        entries = build_website_registry(sf, df)
        assert entries[0].credibility_score == pytest.approx(0.82)

    def test_default_credibility_when_not_in_domains_file(self, tmp_path: Path) -> None:
        sf = _write_sources_file(tmp_path, [
            "newsite.com|NewSite|website|en|news|active",
        ])
        entries = build_website_registry(sf)
        assert entries[0].credibility_score == pytest.approx(0.5)

    def test_comment_lines_skipped(self, tmp_path: Path) -> None:
        f = _write_sources_file(tmp_path, [
            "# This is a comment",
            "coindesk.com|CoinDesk|website|en|crypto|active",
        ])
        entries = build_website_registry(f)
        assert len(entries) == 1

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        f = _write_sources_file(tmp_path, [
            "",
            "coindesk.com|CoinDesk|website|en|crypto|active",
            "",
        ])
        entries = build_website_registry(f)
        assert len(entries) == 1

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        entries = build_website_registry(tmp_path / "nonexistent.txt")
        assert entries == []

    def test_requires_api_status(self, tmp_path: Path) -> None:
        f = _write_sources_file(tmp_path, [
            "twitter.com|Twitter|website|en|social|requires_api",
        ])
        entries = build_website_registry(f)
        assert entries[0].status == SourceStatus.REQUIRES_API
        assert not entries[0].is_fetchable

    def test_source_id_derived_from_domain(self, tmp_path: Path) -> None:
        f = _write_sources_file(tmp_path, [
            "my-site.example.com|MySite|website|en|news|active",
        ])
        entries = build_website_registry(f)
        # Dots and hyphens replaced with underscores
        assert "." not in entries[0].source_id
        assert "-" not in entries[0].source_id


class TestRegisterWebsites:
    def test_registers_into_registry(self, tmp_path: Path) -> None:
        f = _write_sources_file(tmp_path, [
            "a.com|Site A|website|en|news|active",
            "b.com|Site B|website|en|news|active",
        ])
        registry = SourceRegistry()
        count = register_websites(registry, f)
        assert count == 2
        assert len(registry) == 2
