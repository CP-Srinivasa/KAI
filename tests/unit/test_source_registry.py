"""Unit tests for Source Registry — schemas, enums, validation."""

import pytest
from pydantic import ValidationError

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.storage.schemas.source import SourceCreate, SourceUpdate


class TestSourceCreate:
    def test_valid_minimal(self):
        s = SourceCreate(
            source_type=SourceType.RSS_FEED,
            original_url="https://example.com/feed.xml",
        )
        assert s.source_type == SourceType.RSS_FEED
        assert s.status == SourceStatus.PLANNED
        assert s.auth_mode == AuthMode.NONE
        assert s.provider is None

    def test_valid_full(self):
        s = SourceCreate(
            source_type=SourceType.NEWS_API,
            provider="newsdata",
            status=SourceStatus.ACTIVE,
            auth_mode=AuthMode.API_KEY,
            original_url="https://newsdata.io/api/1/news",
            normalized_url="https://newsdata.io/api/1/news",
            notes="Primary news API",
        )
        assert s.provider == "newsdata"
        assert s.auth_mode == AuthMode.API_KEY

    def test_url_is_stripped(self):
        s = SourceCreate(
            source_type=SourceType.RSS_FEED,
            original_url="  https://example.com/feed.xml  ",
        )
        assert s.original_url == "https://example.com/feed.xml"

    def test_empty_url_raises(self):
        with pytest.raises(ValidationError):
            SourceCreate(source_type=SourceType.RSS_FEED, original_url="")

    def test_defaults(self):
        s = SourceCreate(
            source_type=SourceType.WEBSITE,
            original_url="https://coindesk.com",
        )
        assert s.status == SourceStatus.PLANNED
        assert s.auth_mode == AuthMode.NONE
        assert s.normalized_url is None
        assert s.notes is None


class TestSourceUpdate:
    def test_all_fields_optional(self):
        u = SourceUpdate()
        assert u.status is None
        assert u.notes is None
        assert u.source_type is None

    def test_partial_update(self):
        u = SourceUpdate(status=SourceStatus.DISABLED, notes="Temporarily off")
        assert u.status == SourceStatus.DISABLED
        assert u.source_type is None


class TestAuthMode:
    def test_all_values(self):
        assert AuthMode.NONE == "none"
        assert AuthMode.API_KEY == "api_key"
        assert AuthMode.OAUTH == "oauth"
        assert AuthMode.BASIC == "basic"
        assert AuthMode.MANUAL == "manual"

    def test_count(self):
        assert len(AuthMode) == 5


class TestSourceType:
    def test_all_source_types_present(self):
        expected = {
            "rss_feed",
            "website",
            "news_api",
            "youtube_channel",
            "podcast_feed",
            "podcast_page",
            "reference_page",
            "social_api",
            "manual_source",
            "unresolved_source",
        }
        assert {t.value for t in SourceType} == expected


class TestSourceStatus:
    def test_all_statuses_present(self):
        expected = {
            "active",
            "planned",
            "disabled",
            "requires_api",
            "manual_resolution",
            "unresolved",
        }
        assert {s.value for s in SourceStatus} == expected
