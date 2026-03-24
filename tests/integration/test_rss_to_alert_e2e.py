"""E2E integration test: RSS feed → CanonicalDocument → DB → Analysis → Alert.

Proves one complete pipeline pass without any external dependencies:
  - RSS adapter:      httpx patched to return synthetic RSS XML bytes
  - SSRF guard:       validate_url patched (no DNS lookup in unit context)
  - DB layer:         real SQLite in-memory (no mocks)
  - Keyword analysis: real KeywordEngine loaded from monitor/ (no LLM API key)
  - Alert channel:    TelegramAlertChannel in dry_run=True (no HTTP to Telegram)

Stages covered:
  1. RSSFeedAdapter.fetch() → FetchResult with CanonicalDocuments
  2. persist_fetch_result()  → saved to DB (status=PERSISTED)
  3. get_pending_documents() → retrieved from DB
  4. AnalysisPipeline.run()  → AnalysisResult via keyword fallback
  5. repo.update_analysis()  → scores written, status → ANALYZED
  6. TelegramAlertChannel.send() dry_run → AlertDeliveryResult(success=True)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.alerts.channels.telegram import TelegramAlertChannel
from app.alerts.service import AlertService
from app.alerts.threshold import ThresholdEngine
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.pipeline import AnalysisPipeline
from app.core.enums import DocumentStatus, SourceType
from app.core.settings import AlertSettings
from app.ingestion.base.interfaces import SourceMetadata
from app.ingestion.rss.adapter import RSSFeedAdapter
from app.storage.document_ingest import persist_fetch_result
from app.storage.repositories.document_repo import DocumentRepository

_MONITOR_DIR = Path(__file__).resolve().parents[2] / "monitor"

# Minimal valid RSS 2.0 feed with one crypto-market item.
# Uses crypto keywords (Bitcoin, ETF, Ethereum, Solana, BTC) to ensure
# keyword hits and a non-zero relevance score from the rule-based pipeline.
_FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Crypto Market News</title>
    <link>https://feeds.example-crypto.com/rss</link>
    <description>Live cryptocurrency market intelligence feed</description>
    <item>
      <title>Bitcoin surges past $80,000 after SEC approves spot BTC ETF</title>
      <link>https://feeds.example-crypto.com/news/btc-etf-80k</link>
      <guid isPermaLink="false">btc-etf-80k-2026-001</guid>
      <pubDate>Tue, 24 Mar 2026 10:00:00 +0000</pubDate>
      <description>
        Bitcoin broke the $80,000 mark after the SEC approved a spot Bitcoin ETF,
        triggering a broad crypto market rally. Institutional investors increased
        BTC exposure significantly. Ethereum rose 8% and Solana gained 12%.
        Crypto trading volume on major exchanges hit record highs.
        Analysts see further upside for the broader crypto market.
      </description>
    </item>
  </channel>
</rss>
"""


@pytest.fixture(scope="module")
def keyword_engine() -> KeywordEngine:
    """Real KeywordEngine from monitor/ — no LLM, no API key."""
    return KeywordEngine.from_monitor_dir(_MONITOR_DIR)


@pytest.fixture(scope="module")
def pipeline(keyword_engine: KeywordEngine) -> AnalysisPipeline:
    """Keyword-only pipeline — no LLM provider required."""
    return AnalysisPipeline(keyword_engine, provider=None, run_llm=False)


@pytest.mark.asyncio
async def test_rss_to_alert_full_e2e(
    session_factory: async_sessionmaker,
    pipeline: AnalysisPipeline,
) -> None:
    """Single E2E pass: RSS feed bytes → persisted doc → analysis → dry-run alert.

    This test is the definitive proof that all pipeline stages are wired correctly
    and can execute without external services.
    """
    # ── Stage 1: RSS adapter — fetch from mocked network ──────────────────────
    metadata = SourceMetadata(
        url="https://feeds.example-crypto.com/rss",
        source_id="e2e-rss-test",
        source_name="E2E Crypto Feed",
        source_type=SourceType.RSS_FEED,
    )
    adapter = RSSFeedAdapter(metadata)

    with (
        patch("app.ingestion.rss.adapter.validate_url"),  # skip SSRF DNS resolution
        patch.object(
            adapter,
            "_fetch_raw",
            new_callable=AsyncMock,
            return_value=_FEED_XML,
        ),
    ):
        fetch_result = await adapter.fetch()

    assert fetch_result.success, f"RSS fetch failed: {fetch_result.error}"
    assert len(fetch_result.documents) == 1
    fetched_doc = fetch_result.documents[0]
    assert "Bitcoin" in fetched_doc.title
    assert fetched_doc.source_id == "e2e-rss-test"
    assert fetched_doc.source_type == SourceType.RSS_FEED

    # ── Stage 2: Persist to DB ─────────────────────────────────────────────────
    stats = await persist_fetch_result(session_factory, fetch_result)

    assert stats.fetched_count == 1
    assert stats.saved_count == 1, (
        f"Expected 1 saved doc; errors={stats.errors}, "
        f"duplicates={stats.batch_duplicates + stats.existing_duplicates}"
    )
    assert stats.failed_count == 0

    # ── Stage 3: Fetch pending from DB ────────────────────────────────────────
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.get_pending_documents(limit=10)

    assert len(pending) == 1
    pending_doc = pending[0]
    assert pending_doc.status == DocumentStatus.PERSISTED
    assert "Bitcoin" in pending_doc.title

    # ── Stage 4: Keyword analysis (rule-only, no LLM) ─────────────────────────
    pipeline_result = await pipeline.run(pending_doc)

    assert pipeline_result.success, f"Pipeline error: {pipeline_result.error}"
    ar = pipeline_result.analysis_result
    assert ar is not None
    assert 0.0 <= ar.relevance_score <= 1.0
    assert 0.0 <= ar.impact_score <= 1.0
    assert 0.0 <= ar.spam_probability <= 1.0
    assert ar.spam_probability < 0.5, "Crypto market news must not be classified as spam"
    pipeline_result.apply_to_document()

    # ── Stage 5: Write analysis scores to DB ──────────────────────────────────
    doc_id = str(pending_doc.id)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        await repo.update_analysis(
            doc_id,
            ar,
            provider_name=pipeline_result.provider_name,
        )

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        analyzed_doc = await repo.get_by_id(doc_id)

    assert analyzed_doc is not None
    assert analyzed_doc.status == DocumentStatus.ANALYZED
    assert analyzed_doc.is_analyzed is True
    assert analyzed_doc.relevance_score is not None
    assert analyzed_doc.priority_score is not None

    # ── Stage 6: Alert via TelegramChannel (dry_run=True) ─────────────────────
    alert_settings = AlertSettings(
        telegram_enabled=True,
        telegram_token="test-token-e2e",
        telegram_chat_id="99999",
        dry_run=True,
        min_priority=1,  # fire for any document to cover the full dispatch path
    )
    telegram = TelegramAlertChannel(alert_settings)
    alert_service = AlertService(
        channels=[telegram],
        threshold=ThresholdEngine(min_priority=1),
    )

    delivery_results = await alert_service.process_document(
        analyzed_doc,
        ar,
        spam_probability=ar.spam_probability,
    )

    assert len(delivery_results) == 1, "Expected exactly one delivery result (telegram)"
    result = delivery_results[0]
    assert result.channel == "telegram"
    assert result.success is True
    assert result.message_id == "dry_run"
