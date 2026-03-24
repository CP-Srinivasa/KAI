"""Unit tests for app/integrations/newsdata/adapter.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.enums import DocumentType, SourceType
from app.ingestion.base.interfaces import SourceMetadata
from app.integrations.newsdata.adapter import NewsdataAdapter
from app.integrations.newsdata.client import NewsdataArticle


def _make_source_metadata(**meta_overrides: object) -> SourceMetadata:
    meta: dict = {"api_key": "test-key", "language": "en"}
    meta.update(meta_overrides)
    return SourceMetadata(
        source_id="newsdata-en",
        source_name="Newsdata English",
        source_type=SourceType.NEWS_API,
        url="https://newsdata.io",
        metadata=meta,
    )


def _make_article(**overrides: object) -> NewsdataArticle:
    defaults: dict = {
        "article_id": "art-001",
        "title": "ETH breaks $5k",
        "link": "https://example.com/eth-5k",
        "published_at": datetime(2026, 3, 20, 10, 0, 0, tzinfo=UTC),
        "source_id": "cryptonews",
        "source_url": "https://cryptonews.com",
        "language": "en",
        "description": "Ethereum rallies.",
        "content": "Full content here.",
        "creator": ["Bob Lee"],
        "category": ["technology"],
        "country": ["us"],
        "keywords": ["ethereum", "defi"],
        "source_priority": 10,
    }
    defaults.update(overrides)
    return NewsdataArticle(**defaults)


class TestNewsdataAdapterInit:
    def test_adapter_reads_metadata(self) -> None:
        meta = _make_source_metadata(q="bitcoin", language="en,de", size=5)
        adapter = NewsdataAdapter(meta)
        assert adapter.source_id == "newsdata-en"
        assert adapter._q == "bitcoin"
        assert adapter._language == "en,de"
        assert adapter._size == 5

    def test_adapter_default_size(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)
        assert adapter._size == 10

    def test_adapter_default_language(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)
        assert adapter._language == "en"


class TestNewsdataAdapterFetch:
    @pytest.mark.asyncio
    async def test_fetch_success_returns_documents(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)
        articles = [_make_article(), _make_article(article_id="art-002", title="BTC update")]

        with patch.object(adapter._client, "fetch_latest", AsyncMock(return_value=articles)):
            result = await adapter.fetch()

        assert result.success is True
        assert len(result.documents) == 2
        assert result.metadata["article_count"] == 2

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_empty_documents(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        mock_fetch = AsyncMock(side_effect=Exception("timeout"))
        with patch.object(adapter._client, "fetch_latest", mock_fetch):
            result = await adapter.fetch()

        assert result.success is False
        assert result.documents == []
        assert result.error == "timeout"

    @pytest.mark.asyncio
    async def test_fetch_document_source_type_is_news_api(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        mock_fetch = AsyncMock(return_value=[_make_article()])
        with patch.object(adapter._client, "fetch_latest", mock_fetch):
            result = await adapter.fetch()

        assert result.documents[0].source_type == SourceType.NEWS_API

    @pytest.mark.asyncio
    async def test_fetch_document_type_is_article(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        mock_fetch = AsyncMock(return_value=[_make_article()])
        with patch.object(adapter._client, "fetch_latest", mock_fetch):
            result = await adapter.fetch()

        assert result.documents[0].document_type == DocumentType.ARTICLE

    @pytest.mark.asyncio
    async def test_fetch_document_provider_is_newsdata(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        mock_fetch = AsyncMock(return_value=[_make_article()])
        with patch.object(adapter._client, "fetch_latest", mock_fetch):
            result = await adapter.fetch()

        assert result.documents[0].provider == "newsdata"

    @pytest.mark.asyncio
    async def test_fetch_document_external_id_set(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        mock_fetch = AsyncMock(return_value=[_make_article()])
        with patch.object(adapter._client, "fetch_latest", mock_fetch):
            result = await adapter.fetch()

        assert result.documents[0].external_id == "art-001"

    @pytest.mark.asyncio
    async def test_fetch_document_url_and_title(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)
        article = _make_article(title="Bitcoin surge", link="https://example.com/btc")

        with patch.object(adapter._client, "fetch_latest", AsyncMock(return_value=[article])):
            result = await adapter.fetch()

        doc = result.documents[0]
        assert doc.url == "https://example.com/btc"
        assert doc.title == "Bitcoin surge"

    @pytest.mark.asyncio
    async def test_fetch_document_content_prefers_content_over_description(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)
        article = _make_article(content="Full body", description="Short desc")

        with patch.object(adapter._client, "fetch_latest", AsyncMock(return_value=[article])):
            result = await adapter.fetch()

        assert result.documents[0].raw_text == "Full body"

    @pytest.mark.asyncio
    async def test_fetch_document_uses_description_when_no_content(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)
        article = _make_article(content=None, description="Only description available")

        with patch.object(adapter._client, "fetch_latest", AsyncMock(return_value=[article])):
            result = await adapter.fetch()

        assert result.documents[0].raw_text == "Only description available"

    @pytest.mark.asyncio
    async def test_fetch_document_metadata_populated(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        mock_fetch = AsyncMock(return_value=[_make_article()])
        with patch.object(adapter._client, "fetch_latest", mock_fetch):
            result = await adapter.fetch()

        doc_meta = result.documents[0].metadata
        assert doc_meta["source_id"] == "cryptonews"
        assert doc_meta["language"] == "en"
        assert doc_meta["categories"] == ["technology"]
        assert doc_meta["keywords"] == ["ethereum", "defi"]

    @pytest.mark.asyncio
    async def test_fetch_document_author_joined(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)
        article = _make_article(creator=["Alice", "Bob"])

        with patch.object(adapter._client, "fetch_latest", AsyncMock(return_value=[article])):
            result = await adapter.fetch()

        assert result.documents[0].metadata["authors"] == "Alice, Bob"

    @pytest.mark.asyncio
    async def test_fetch_document_no_author_is_none(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)
        article = _make_article(creator=[])

        with patch.object(adapter._client, "fetch_latest", AsyncMock(return_value=[article])):
            result = await adapter.fetch()

        assert result.documents[0].metadata["authors"] is None

    @pytest.mark.asyncio
    async def test_fetch_source_id_on_result(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        with patch.object(adapter._client, "fetch_latest", AsyncMock(return_value=[])):
            result = await adapter.fetch()

        assert result.source_id == "newsdata-en"


class TestNewsdataAdapterValidate:
    @pytest.mark.asyncio
    async def test_validate_true_when_articles_returned(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        mock_fetch = AsyncMock(return_value=[_make_article()])
        with patch.object(adapter._client, "fetch_latest", mock_fetch):
            assert await adapter.validate() is True

    @pytest.mark.asyncio
    async def test_validate_false_on_exception(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        mock_fetch = AsyncMock(side_effect=Exception("fail"))
        with patch.object(adapter._client, "fetch_latest", mock_fetch):
            assert await adapter.validate() is False

    @pytest.mark.asyncio
    async def test_validate_false_on_empty_results(self) -> None:
        meta = _make_source_metadata()
        adapter = NewsdataAdapter(meta)

        with patch.object(adapter._client, "fetch_latest", AsyncMock(return_value=[])):
            assert await adapter.validate() is False
