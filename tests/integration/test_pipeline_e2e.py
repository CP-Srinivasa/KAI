"""Integration test: RSS → CanonicalDocument → DB → Analysis → Scoring → Alert gate.

This test suite is the production-readiness proof for the core pipeline.
It uses a REAL SQLite in-memory database (no mocks for DB layer) and
REAL keyword-based analysis (no LLM API key required).

What is proven here:
  1. CanonicalDocument can be persisted to DB (status=PERSISTED)
  2. get_pending_documents() returns persisted documents
  3. AnalysisPipeline runs end-to-end with keyword fallback (no LLM)
  4. Scores are written to DB and document status transitions to ANALYZED
  5. is_alert_worthy() gate evaluates correctly
  6. AlertService (dry_run, no channels) processes a document without error
  7. Deduplication: same content_hash is not re-inserted
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.alerts.service import AlertService
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.pipeline import AnalysisPipeline
from app.analysis.scoring import is_alert_worthy
from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentStatus, SentimentLabel, SourceType
from app.core.settings import AppSettings
from app.storage.repositories.document_repo import DocumentRepository

_MONITOR_DIR = Path(__file__).resolve().parents[2] / "monitor"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def keyword_engine() -> KeywordEngine:
    """Real KeywordEngine loaded from the project monitor/ directory."""
    return KeywordEngine.from_monitor_dir(_MONITOR_DIR)


@pytest.fixture(scope="module")
def pipeline(keyword_engine: KeywordEngine) -> AnalysisPipeline:
    """Pipeline with keyword-only analysis — no LLM provider, no API key needed."""
    return AnalysisPipeline(keyword_engine, provider=None, run_llm=False)


def _make_document(*, title: str, raw_text: str, url: str) -> CanonicalDocument:
    content_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    return CanonicalDocument(
        url=url,
        title=title,
        raw_text=raw_text,
        cleaned_text=raw_text,
        source_type=SourceType.RSS_FEED,
        source_id="integration-test-source",
        content_hash=content_hash,
        status=DocumentStatus.PERSISTED,
    )


# ---------------------------------------------------------------------------
# Stage 1 — Persistence
# ---------------------------------------------------------------------------


async def test_document_save_and_fetch_pending(session_factory: async_sessionmaker) -> None:
    """Document persisted as PERSISTED appears in get_pending_documents()."""
    doc = _make_document(
        title="Bitcoin rally: BTC reaches new high amid institutional demand",
        raw_text=(
            "Bitcoin surged past $80,000 as institutional investors increased exposure. "
            "Ethereum and Solana also gained. Market analysts see further upside."
        ),
        url="https://example.com/btc-rally-001",
    )

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        doc_id = await repo.save_document(doc)

    assert doc_id is not None

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.get_pending_documents(limit=10)

    assert len(pending) == 1
    assert pending[0].url == doc.url
    assert pending[0].status == DocumentStatus.PERSISTED


# ---------------------------------------------------------------------------
# Stage 2 — Analysis pipeline (keyword fallback, no LLM)
# ---------------------------------------------------------------------------


async def test_pipeline_produces_analysis_result(
    session_factory: async_sessionmaker,
    pipeline: AnalysisPipeline,
) -> None:
    """AnalysisPipeline with keyword fallback produces a valid AnalysisResult."""
    doc = _make_document(
        title="Ethereum DeFi protocol adds new staking vault",
        raw_text=(
            "A leading Ethereum DeFi protocol launched a new staking vault with "
            "enhanced yield opportunities. The move signals growing institutional interest "
            "in decentralised finance and crypto asset management."
        ),
        url="https://example.com/eth-defi-001",
    )

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        await repo.save_document(doc)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.get_pending_documents(limit=10)

    assert len(pending) == 1
    result = await pipeline.run(pending[0])

    assert result.success, f"Pipeline failed: {result.error}"
    assert result.analysis_result is not None
    ar = result.analysis_result
    assert 0.0 <= ar.relevance_score <= 1.0
    assert 0.0 <= ar.impact_score <= 1.0
    assert 0.0 <= ar.novelty_score <= 1.0
    assert ar.sentiment_label in SentimentLabel.__members__.values()


# ---------------------------------------------------------------------------
# Stage 3 — DB write-back and status transition
# ---------------------------------------------------------------------------


async def test_analysis_written_to_db_and_status_becomes_analyzed(
    session_factory: async_sessionmaker,
    pipeline: AnalysisPipeline,
) -> None:
    """After update_analysis(), document status = ANALYZED with scores set."""
    doc = _make_document(
        title="Crypto market analysis: Bitcoin, Ethereum weekly review",
        raw_text=(
            "Weekly crypto market review. Bitcoin held support levels. "
            "Ethereum gas fees declined. Altcoin volatility remained elevated. "
            "Trading volume on major exchanges increased significantly."
        ),
        url="https://example.com/weekly-review-001",
    )

    # Stage 3a: persist
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        doc_id = await repo.save_document(doc)

    # Stage 3b: fetch pending + run analysis
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.get_pending_documents(limit=10)

    assert len(pending) == 1
    pipeline_result = await pipeline.run(pending[0])
    assert pipeline_result.success
    pipeline_result.apply_to_document()

    # Stage 3c: write back to DB
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        await repo.update_analysis(
            doc_id,
            pipeline_result.analysis_result,
            provider_name=pipeline_result.provider_name,
        )

    # Stage 3d: verify DB state
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.get_by_id(doc_id)

    assert saved is not None
    assert saved.status == DocumentStatus.ANALYZED
    assert saved.is_analyzed is True
    assert saved.relevance_score is not None
    assert saved.impact_score is not None
    assert saved.novelty_score is not None
    assert saved.priority_score is not None


# ---------------------------------------------------------------------------
# Stage 4 — Alert gate
# ---------------------------------------------------------------------------


async def test_alert_gate_evaluates_correctly(
    session_factory: async_sessionmaker,
    pipeline: AnalysisPipeline,
) -> None:
    """is_alert_worthy() returns a deterministic bool; spam is always blocked."""
    doc = _make_document(
        title="Bitcoin ETF approval sparks crypto rally",
        raw_text=(
            "The SEC approved a spot Bitcoin ETF, triggering a broad crypto market rally. "
            "Institutional inflows hit record highs. Ethereum and Solana followed BTC higher."
        ),
        url="https://example.com/btc-etf-001",
    )

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        await repo.save_document(doc)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.get_pending_documents(limit=10)

    pipeline_result = await pipeline.run(pending[0])
    assert pipeline_result.success
    ar = pipeline_result.analysis_result

    # Gate must return a bool regardless of score value
    result = is_alert_worthy(ar, min_priority=7)
    assert isinstance(result, bool)

    # Spam is always blocked — this is a hard invariant
    spam_result = is_alert_worthy(ar, min_priority=1, spam_probability=0.9)
    assert spam_result is False

    # Low-threshold gate must fire for non-spam documents
    low_threshold = is_alert_worthy(ar, min_priority=1, spam_probability=0.0)
    assert low_threshold is True


# ---------------------------------------------------------------------------
# Stage 5 — AlertService dry run (no channels)
# ---------------------------------------------------------------------------


async def test_alert_service_processes_analyzed_document(
    session_factory: async_sessionmaker,
    pipeline: AnalysisPipeline,
) -> None:
    """AlertService with no channels and dry_run=True processes without error."""
    doc = _make_document(
        title="Stablecoin regulations: USDC issuer Circle files for IPO",
        raw_text=(
            "Circle, the issuer of USDC stablecoin, filed for an IPO amid growing "
            "regulatory clarity on stablecoins. The move signals maturation of the "
            "crypto industry and broader institutional adoption."
        ),
        url="https://example.com/circle-ipo-001",
    )

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        doc_id = await repo.save_document(doc)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.get_pending_documents(limit=10)

    pipeline_result = await pipeline.run(pending[0])
    assert pipeline_result.success
    pipeline_result.apply_to_document()

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        await repo.update_analysis(doc_id, pipeline_result.analysis_result)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        analyzed_doc = await repo.get_by_id(doc_id)

    assert analyzed_doc.status == DocumentStatus.ANALYZED

    # AlertService with no active channels and dry_run=True — must not raise
    settings = AppSettings()
    settings.alerts.dry_run = True
    settings.alerts.telegram_enabled = False
    settings.alerts.email_enabled = False
    alert_service = AlertService(channels=[], threshold=__import__(
        "app.alerts.threshold", fromlist=["ThresholdEngine"]
    ).ThresholdEngine(min_priority=7))

    delivery_results = await alert_service.process_document(
        analyzed_doc,
        pipeline_result.analysis_result,
        spam_probability=pipeline_result.analysis_result.spam_probability,
    )
    # No channels → empty results; no exception means the gate ran cleanly
    assert isinstance(delivery_results, list)


# ---------------------------------------------------------------------------
# Stage 6 — Deduplication
# ---------------------------------------------------------------------------


async def test_deduplication_same_content_hash(session_factory: async_sessionmaker) -> None:
    """Two documents with the same content_hash — only one is persisted."""
    raw_text = "Bitcoin is the leading cryptocurrency by market capitalisation."
    doc_a = _make_document(
        title="Bitcoin market cap overview",
        raw_text=raw_text,
        url="https://example.com/btc-cap-001",
    )
    doc_b = _make_document(
        title="Bitcoin market cap overview (re-post)",
        raw_text=raw_text,
        url="https://example.com/btc-cap-002",
    )
    assert doc_a.content_hash == doc_b.content_hash

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        id_a = await repo.save_document(doc_a)
        id_b = await repo.save_document(doc_b)

    # save_document() is idempotent on content_hash: returns the existing ID, no new row
    assert id_a is not None
    assert id_b == id_a, "Duplicate content_hash must return the original document ID"

    # Only one document in DB — the second save must not insert a new row
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.get_pending_documents(limit=10)
    assert len(pending) == 1
