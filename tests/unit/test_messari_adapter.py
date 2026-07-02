"""Unit tests for app/ingestion/messari/adapter.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.enums import DocumentType, SourceType
from app.ingestion.base.interfaces import SourceMetadata
from app.ingestion.messari.adapter import MessariAdapter


def _make_source_metadata(**meta_overrides: object) -> SourceMetadata:
    meta = {}
    meta.update(meta_overrides)
    return SourceMetadata(
        source_id="messari-test",
        source_name="Messari Ingestion",
        source_type=SourceType.NEWS_API,
        url="https://api.messari.io/metrics/v2/assets",
        metadata=meta,
    )


class TestMessariAdapterInit:
    def test_adapter_reads_metadata(self) -> None:
        meta = _make_source_metadata(api_key="test-api-key", limit=50)
        adapter = MessariAdapter(meta)
        assert adapter.source_id == "messari-test"
        assert adapter._api_key == "test-api-key"
        assert adapter._limit == 50

    def test_adapter_defaults(self) -> None:
        meta = _make_source_metadata()
        adapter = MessariAdapter(meta)
        assert adapter._api_key == ""
        assert adapter._limit == 100


class TestMessariAdapterFetch:
    @pytest.mark.asyncio
    async def test_fetch_success_filters_and_returns_documents(self) -> None:
        meta = _make_source_metadata()
        adapter = MessariAdapter(meta)

        # Mock payload: one matching news/research, one not, one invalid
        mock_payload = {
            "data": [
                {
                    "id": "1",
                    "symbol": "BTC",
                    "name": "Bitcoin",
                    "slug": "bitcoin",
                    "hasNews": True,
                    "hasResearch": False,
                    "sector": "Currencies",
                    "category": "Payment",
                    "rank": 1,
                    "tags": ["pow", "store_of_value"],
                },
                {
                    "id": "2",
                    "symbol": "ETH",
                    "name": "Ethereum",
                    "slug": "ethereum",
                    "hasNews": False,
                    "hasResearch": False,  # Should be skipped!
                },
                {
                    "id": "3",
                    "symbol": "SOL",
                    "name": "Solana",
                    "slug": "solana",
                    "hasNews": False,
                    "hasResearch": True,  # Should be included!
                    "sector": "Smart Contracts",
                    "category": "Layer1",
                    "rank": 5,
                    "tags": ["pos"],
                },
            ]
        }

        with patch.object(adapter, "_fetch_raw", AsyncMock(return_value=mock_payload)):
            result = await adapter.fetch()

        assert result.success is True
        assert len(result.documents) == 2

        # Verify BTC document
        btc_doc = next(d for d in result.documents if d.tickers == ["BTC"])
        assert btc_doc.title == "Messari Asset Coverage Update: Bitcoin (BTC)"
        assert btc_doc.external_id == "messari-1"
        assert btc_doc.provider == "messari"
        assert btc_doc.source_type == SourceType.NEWS_API
        assert btc_doc.document_type == DocumentType.ARTICLE
        assert btc_doc.crypto_assets == ["BTC"]
        assert btc_doc.categories == ["Payment"]
        assert btc_doc.tags == ["pow", "store_of_value"]
        assert "hasNews=True" in btc_doc.raw_text

        # Verify SOL document
        sol_doc = next(d for d in result.documents if d.tickers == ["SOL"])
        assert sol_doc.title == "Messari Asset Coverage Update: Solana (SOL)"
        assert sol_doc.external_id == "messari-3"

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_success_false(self) -> None:
        meta = _make_source_metadata()
        adapter = MessariAdapter(meta)

        with patch.object(adapter, "_fetch_raw", AsyncMock(side_effect=Exception("API error"))):
            result = await adapter.fetch()

        assert result.success is False
        assert len(result.documents) == 0
        assert "API error" in result.error

    @pytest.mark.asyncio
    async def test_validate_returns_true_on_success(self) -> None:
        meta = _make_source_metadata()
        adapter = MessariAdapter(meta)

        with patch.object(adapter, "_fetch_raw", AsyncMock(return_value={"data": []})):
            assert await adapter.validate() is True

    @pytest.mark.asyncio
    async def test_validate_returns_false_on_failure(self) -> None:
        meta = _make_source_metadata()
        adapter = MessariAdapter(meta)

        with patch.object(adapter, "_fetch_raw", AsyncMock(side_effect=Exception("Failed"))):
            assert await adapter.validate() is False
