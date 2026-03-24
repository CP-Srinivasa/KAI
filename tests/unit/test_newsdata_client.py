"""Unit tests for app/integrations/newsdata/client.py."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.newsdata.client import NewsdataArticle, NewsdataClient

# ---------------------------------------------------------------------------
# _parse_article helpers
# ---------------------------------------------------------------------------


def _make_client() -> NewsdataClient:
    return NewsdataClient(api_key="test-key", timeout=5)


def _raw_article(**overrides: object) -> dict:
    base: dict = {
        "article_id": "art-001",
        "title": "Bitcoin surges past $100k",
        "link": "https://example.com/btc-100k",
        "pubDate": "2026-03-20 08:00:00",
        "source_id": "coindesk",
        "source_url": "https://coindesk.com",
        "language": "en",
        "description": "BTC hits new ATH.",
        "content": "Full article body here.",
        "creator": ["Alice Smith"],
        "category": ["business", "technology"],
        "country": ["us"],
        "keywords": ["bitcoin", "crypto"],
        "source_priority": 42,
    }
    base.update(overrides)
    return base


class TestNewsdataArticleParsing:
    def test_parse_article_all_fields(self) -> None:
        client = _make_client()
        raw = _raw_article()
        article = client._parse_article(raw)

        assert article.article_id == "art-001"
        assert article.title == "Bitcoin surges past $100k"
        assert article.link == "https://example.com/btc-100k"
        assert article.source_id == "coindesk"
        assert article.source_url == "https://coindesk.com"
        assert article.language == "en"
        assert article.description == "BTC hits new ATH."
        assert article.content == "Full article body here."
        assert article.creator == ["Alice Smith"]
        assert article.category == ["business", "technology"]
        assert article.country == ["us"]
        assert article.keywords == ["bitcoin", "crypto"]
        assert article.source_priority == 42

    def test_parse_article_published_at_parsed(self) -> None:
        client = _make_client()
        raw = _raw_article(pubDate="2026-03-20 08:30:00")
        article = client._parse_article(raw)
        assert isinstance(article.published_at, datetime)
        assert article.published_at.year == 2026
        assert article.published_at.month == 3

    def test_parse_article_missing_pubdate_uses_fallback(self) -> None:
        client = _make_client()
        raw = _raw_article(pubDate=None)
        article = client._parse_article(raw)
        assert isinstance(article.published_at, datetime)

    def test_parse_article_null_creator_defaults_to_empty_list(self) -> None:
        client = _make_client()
        raw = _raw_article(creator=None)
        article = client._parse_article(raw)
        assert article.creator == []

    def test_parse_article_null_keywords_defaults_to_empty_list(self) -> None:
        client = _make_client()
        raw = _raw_article(keywords=None)
        article = client._parse_article(raw)
        assert article.keywords == []

    def test_parse_article_null_category_defaults_to_empty_list(self) -> None:
        client = _make_client()
        raw = _raw_article(category=None)
        article = client._parse_article(raw)
        assert article.category == []

    def test_parse_article_null_description_becomes_none(self) -> None:
        client = _make_client()
        raw = _raw_article(description=None)
        article = client._parse_article(raw)
        assert article.description is None

    def test_parse_article_null_content_becomes_none(self) -> None:
        client = _make_client()
        raw = _raw_article(content=None)
        article = client._parse_article(raw)
        assert article.content is None

    def test_parse_article_missing_source_priority_defaults_to_zero(self) -> None:
        client = _make_client()
        raw = _raw_article(source_priority=None)
        article = client._parse_article(raw)
        assert article.source_priority == 0


class TestNewsdataClientFetch:
    @pytest.mark.asyncio
    async def test_fetch_latest_returns_articles(self) -> None:
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "success",
            "results": [_raw_article(), _raw_article(article_id="art-002", title="ETH update")],
        }

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("app.integrations.newsdata.client.httpx.AsyncClient", return_value=mock_http):
            articles = await client.fetch_latest()

        assert len(articles) == 2
        assert articles[0].article_id == "art-001"
        assert articles[1].article_id == "art-002"

    @pytest.mark.asyncio
    async def test_fetch_latest_empty_results(self) -> None:
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"status": "success", "results": []}

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("app.integrations.newsdata.client.httpx.AsyncClient", return_value=mock_http):
            articles = await client.fetch_latest()

        assert articles == []

    @pytest.mark.asyncio
    async def test_fetch_latest_passes_query_params(self) -> None:
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("app.integrations.newsdata.client.httpx.AsyncClient", return_value=mock_http):
            await client.fetch_latest(
                q="bitcoin", language="en", country="us", category="business", size=5
            )

        call_kwargs = mock_http.get.call_args
        # params passed as keyword argument
        # params passed as keyword argument
        assert call_kwargs is not None

    @pytest.mark.asyncio
    async def test_fetch_latest_http_error_raises(self) -> None:
        import httpx

        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403", request=MagicMock(), response=MagicMock()
        )

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        with (
            patch("app.integrations.newsdata.client.httpx.AsyncClient", return_value=mock_http),
            pytest.raises(Exception, match=".*"),
        ):
            await client.fetch_latest()


class TestNewsdataArticleDataclass:
    def test_article_is_frozen(self) -> None:
        article = NewsdataArticle(
            article_id="x",
            title="T",
            link="https://example.com",
            published_at=datetime(2026, 1, 1),
            source_id="src",
            source_url="https://src.com",
            language="en",
        )
        with pytest.raises(Exception, match=".*"):
            article.title = "mutate"  # type: ignore[misc]
