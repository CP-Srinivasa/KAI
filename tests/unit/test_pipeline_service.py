"""Tests for app/pipeline/service.py — run_rss_pipeline end-to-end."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.domain.document import CanonicalDocument
from app.core.enums import SentimentLabel, SourceStatus, SourceType
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.classifier import ClassificationResult
from app.ingestion.resolvers.rss import RSSResolveResult
from app.ingestion.rss.service import RSSCollectedFeed
from app.pipeline import service as pipeline_service
from app.storage.document_ingest import IngestPersistStats
from tests.unit.factories import make_llm_output

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_doc(url: str, title: str) -> CanonicalDocument:
    return CanonicalDocument(url=url, title=title)


def _make_collected(
    docs: list[CanonicalDocument], url: str = "https://example.com/feed"
) -> RSSCollectedFeed:
    return RSSCollectedFeed(
        classification=ClassificationResult(SourceType.RSS_FEED, SourceStatus.ACTIVE),
        resolved_feed=RSSResolveResult(
            url=url,
            is_valid=True,
            resolved_url=url,
            feed_title="Test Feed",
            entry_count=len(docs),
            error=None,
        ),
        fetch_result=FetchResult(
            source_id="src-1",
            documents=docs,
            fetched_at=datetime.now(UTC),
            success=True,
        ),
    )


class FakeKeywordEngine:
    def match(self, text: str) -> list:
        return []

    def match_tickers(self, text: str) -> list[str]:
        return ["BTC"] if "Bitcoin" in text else []


class FakeProvider:
    provider_name = "fake"
    model = "fake-model"

    async def analyze(self, title: str, text: str, context=None):
        return make_llm_output(
            sentiment_label=SentimentLabel.BULLISH,
            relevance_score=0.85,
            impact_score=0.70,
            novelty_score=0.60,
            spam_probability=0.02,
        )


class FakeSessionFactory:
    """Minimal async session factory that captures update_analysis calls."""

    def __init__(self) -> None:
        self.updated_docs: list[CanonicalDocument] = []

    def begin(self):
        class _Ctx:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *_):
                return False

        return _Ctx()


# ── fetch failure ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_rss_pipeline_returns_early_on_fetch_failure(monkeypatch) -> None:
    async def fake_collect(**kwargs) -> RSSCollectedFeed:
        return RSSCollectedFeed(
            classification=ClassificationResult(SourceType.WEBSITE, SourceStatus.ACTIVE),
            resolved_feed=RSSResolveResult(
                url="https://bad.example.com",
                is_valid=False,
                resolved_url=None,
                feed_title=None,
                entry_count=0,
                error="not a feed",
            ),
            fetch_result=FetchResult(
                source_id="manual",
                documents=[],
                fetched_at=datetime.now(UTC),
                success=False,
                error="not a feed",
            ),
        )

    monkeypatch.setattr(pipeline_service, "collect_rss_feed", fake_collect)

    stats = await pipeline_service.run_rss_pipeline(
        "https://bad.example.com",
        session_factory=FakeSessionFactory(),
        keyword_engine=FakeKeywordEngine(),
    )

    assert stats.fetched_count == 0
    assert stats.saved_count == 0
    assert stats.analyzed_count == 0
    assert stats.failed_count == 1
    assert stats.top_results == []


# ── no new docs ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_rss_pipeline_returns_early_when_no_saved_docs(monkeypatch) -> None:
    docs = [_make_doc("https://example.com/a", "Old Article")]
    collected = _make_collected(docs)

    async def fake_collect(**kwargs) -> RSSCollectedFeed:
        return collected

    async def fake_persist(
        session_factory, result, *, dry_run=False, **kwargs
    ) -> IngestPersistStats:
        return IngestPersistStats(
            fetched_count=1,
            candidate_count=0,
            batch_duplicates=0,
            existing_duplicates=1,
            saved_count=0,
            failed_count=0,
            preview_documents=[],
        )

    monkeypatch.setattr(pipeline_service, "collect_rss_feed", fake_collect)
    monkeypatch.setattr(pipeline_service, "persist_fetch_result", fake_persist)

    stats = await pipeline_service.run_rss_pipeline(
        "https://example.com/feed",
        session_factory=FakeSessionFactory(),
        keyword_engine=FakeKeywordEngine(),
    )

    assert stats.fetched_count == 1
    assert stats.saved_count == 0
    assert stats.analyzed_count == 0
    assert stats.skipped_count == 1
    assert stats.top_results == []


# ── full pipeline happy path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_rss_pipeline_full_chain(monkeypatch) -> None:
    from app.storage.repositories import document_repo

    doc1 = _make_doc("https://example.com/btc", "Bitcoin hits ATH")
    doc2 = _make_doc("https://example.com/eth", "Ethereum upgrade")
    docs = [doc1, doc2]
    collected = _make_collected(docs)

    updated_docs: list[str] = []

    async def fake_collect(**kwargs) -> RSSCollectedFeed:
        return collected

    async def fake_persist(
        session_factory, result, *, dry_run=False, **kwargs
    ) -> IngestPersistStats:
        return IngestPersistStats(
            fetched_count=2,
            candidate_count=2,
            batch_duplicates=0,
            existing_duplicates=0,
            saved_count=2,
            failed_count=0,
            preview_documents=docs,
        )

    async def fake_update_analysis(
        self,
        document_id: str,
        result,
        *,
        provider_name: str | None = None,
        metadata_updates=None,
    ) -> None:
        updated_docs.append(document_id)

    monkeypatch.setattr(pipeline_service, "collect_rss_feed", fake_collect)
    monkeypatch.setattr(pipeline_service, "persist_fetch_result", fake_persist)
    monkeypatch.setattr(document_repo.DocumentRepository, "update_analysis", fake_update_analysis)

    session_factory = FakeSessionFactory()

    stats = await pipeline_service.run_rss_pipeline(
        "https://example.com/feed",
        session_factory=session_factory,
        keyword_engine=FakeKeywordEngine(),
        provider=FakeProvider(),
    )

    assert stats.fetched_count == 2
    assert stats.saved_count == 2
    assert stats.analyzed_count == 2
    assert stats.failed_count == 0
    assert stats.skipped_count == 0
    assert len(stats.top_results) == 2

    # Both docs should have priority scores applied
    for res in stats.top_results:
        assert res.document.priority_score is not None

    # top_results sorted by priority_score descending
    scores = [r.document.priority_score for r in stats.top_results]
    assert scores == sorted(scores, reverse=True)

    # update_analysis called for each analyzed doc
    assert len(updated_docs) == 2


# ── dry-run mode ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_rss_pipeline_dry_run_skips_db_writes(monkeypatch) -> None:
    from app.storage.repositories import document_repo

    doc = _make_doc("https://example.com/btc", "Bitcoin ETF approved")
    collected = _make_collected([doc])

    persist_calls: list = []
    update_calls: list = []

    async def fake_collect(**kwargs) -> RSSCollectedFeed:
        return collected

    async def fake_persist(
        session_factory, result, *, dry_run=False, **kwargs
    ) -> IngestPersistStats:
        persist_calls.append({"session_factory": session_factory, "dry_run": dry_run})
        return IngestPersistStats(
            fetched_count=1,
            candidate_count=1,
            batch_duplicates=0,
            existing_duplicates=0,
            saved_count=0,
            failed_count=0,
            preview_documents=[doc],
        )

    async def fake_update_analysis(
        self,
        document_id: str,
        result,
        *,
        provider_name: str | None = None,
        metadata_updates=None,
    ) -> None:
        update_calls.append(document_id)

    monkeypatch.setattr(pipeline_service, "collect_rss_feed", fake_collect)
    monkeypatch.setattr(pipeline_service, "persist_fetch_result", fake_persist)
    monkeypatch.setattr(document_repo.DocumentRepository, "update_analysis", fake_update_analysis)

    fake_factory = FakeSessionFactory()
    stats = await pipeline_service.run_rss_pipeline(
        "https://example.com/feed",
        session_factory=fake_factory,
        keyword_engine=FakeKeywordEngine(),
        provider=FakeProvider(),
        dry_run=True,
    )

    # persist_fetch_result called with dry_run=True; session_factory is the real factory
    # (persist_fetch_result short-circuits in dry_run before touching session_factory)
    assert len(persist_calls) == 1
    assert persist_calls[0]["session_factory"] is fake_factory
    assert persist_calls[0]["dry_run"] is True

    # No DB writes
    assert update_calls == []

    # Analysis still ran (scores applied for preview)
    assert stats.analyzed_count == 1
    assert stats.top_results[0].document.priority_score is not None


# ── stats counts ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_rss_pipeline_skipped_count_sums_both_duplicate_types(monkeypatch) -> None:
    from app.storage.repositories import document_repo

    doc = _make_doc("https://example.com/new", "New article")
    collected = _make_collected([doc])

    async def fake_collect(**kwargs) -> RSSCollectedFeed:
        return collected

    async def fake_persist(
        session_factory, result, *, dry_run=False, **kwargs
    ) -> IngestPersistStats:
        return IngestPersistStats(
            fetched_count=5,
            candidate_count=1,
            batch_duplicates=2,
            existing_duplicates=2,
            saved_count=1,
            failed_count=0,
            preview_documents=[doc],
        )

    async def fake_update(
        self, document_id: str, result, *, provider_name: str | None = None, metadata_updates=None
    ) -> None:
        pass

    async def fake_update_status(self, document_id: str, status) -> None:
        pass

    monkeypatch.setattr(pipeline_service, "collect_rss_feed", fake_collect)
    monkeypatch.setattr(pipeline_service, "persist_fetch_result", fake_persist)
    monkeypatch.setattr(document_repo.DocumentRepository, "update_analysis", fake_update)
    monkeypatch.setattr(document_repo.DocumentRepository, "update_status", fake_update_status)

    stats = await pipeline_service.run_rss_pipeline(
        "https://example.com/feed",
        session_factory=FakeSessionFactory(),
        keyword_engine=FakeKeywordEngine(),
    )

    assert stats.skipped_count == 4  # batch_duplicates(2) + existing_duplicates(2)
    assert stats.saved_count == 1
