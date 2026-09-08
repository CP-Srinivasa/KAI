"""Tests for Research Briefs module."""

from datetime import UTC, datetime, timedelta

import pytest

from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline
from app.core.briefs import ResearchBriefBuilder
from app.core.enums import SentimentLabel
from tests.unit.factories import make_document


def test_research_brief_builder_empty():
    builder = ResearchBriefBuilder("DeFi")
    brief = builder.build([])
    assert brief.cluster_name == "DeFi"
    assert brief.title == "Research Brief: DeFi"
    assert brief.document_count == 0
    assert brief.average_priority == 0.0
    assert brief.overall_sentiment == "neutral"
    assert brief.summary == "No current analyzed documents in the report window."
    assert len(brief.top_actionable_signals) == 0
    assert len(brief.top_documents) == 0
    assert len(brief.key_documents) == 0


def test_research_brief_builder_with_valid_documents():
    docs = [
        make_document(
            published_at=datetime.now(UTC),
            title="High Priority DeFi Hack",
            is_analyzed=True,
            priority_score=9,
            sentiment_label=SentimentLabel.BEARISH,
            impact_score=0.9,
            summary="Major hack.",
            tickers=["ETH"],
            crypto_assets=["ETH"],
            entities=["Uniswap"],
        ),
        make_document(
            published_at=datetime.now(UTC),
            title="Regular DeFi News",
            is_analyzed=True,
            priority_score=5,
            sentiment_label=SentimentLabel.BULLISH,
            impact_score=0.4,
            summary="Protocol update.",
            tickers=["ETH"],
            crypto_assets=["ETH"],
            entities=["Aave"],
        ),
        make_document(
            published_at=datetime.now(UTC),
            title="Unanalyzed Doc",
            is_analyzed=False,
        ),
    ]
    builder = ResearchBriefBuilder("DeFi")
    brief = builder.build(docs)
    assert brief.document_count == 2
    assert brief.average_priority == 7.0
    assert brief.summary.startswith("2 analyzed documents")
    assert len(brief.top_actionable_signals) == 1
    assert brief.top_actionable_signals[0].title == "High Priority DeFi Hack"
    assert brief.top_actionable_signals[0].analysis_source in ("external_llm", "rule", "internal")
    assert len(brief.top_documents) == 2
    assert brief.top_documents[0].title == "High Priority DeFi Hack"
    assert len(brief.key_documents) == 1
    assert brief.key_documents[0].title == "Regular DeFi News"
    assert brief.top_assets[0].name == "ETH"
    assert brief.top_assets[0].count == 4
    assert {facet.name for facet in brief.top_entities} == {"Aave", "Uniswap"}


def test_research_brief_builder_handles_missing_priority_safely():
    docs = [
        make_document(
            published_at=datetime.now(UTC),
            title="Analyzed without priority",
            is_analyzed=True,
            priority_score=None,
            summary=None,
            sentiment_label=None,
        )
    ]
    builder = ResearchBriefBuilder("Fallback")
    brief = builder.build(docs)

    assert brief.document_count == 1
    assert brief.average_priority == 0.0
    assert brief.top_documents[0].priority_score == 0
    assert brief.top_documents[0].summary == "Analyzed without priority"
    assert brief.top_documents[0].sentiment_label == "neutral"
    assert brief.top_documents[0].analysis_source == "rule"


def test_research_brief_to_markdown():
    docs = [
        make_document(
            published_at=datetime.now(UTC),
            title="Test Actionable",
            is_analyzed=True,
            priority_score=10,
            sentiment_label=SentimentLabel.BULLISH,
            summary="Markdown Summary",
            url="http://example.com/1",
            tickers=["BTC"],
            entities=["BlackRock"],
        )
    ]
    builder = ResearchBriefBuilder("Test Cluster")
    brief = builder.build(docs)
    md = brief.to_markdown()
    assert "# Research Brief: Test Cluster" in md
    assert "Markdown Summary" in md
    assert "## Top Assets" in md
    assert "**BTC** (1)" in md
    assert "## Top Entities" in md
    assert "**BlackRock** (1)" in md
    assert "### [Test Actionable](http://example.com/1)" in md
    assert "🟢 Bullish" in md


def test_research_brief_to_json():
    docs = []
    builder = ResearchBriefBuilder("JSON Test")
    brief = builder.build(docs)
    data = brief.to_json_dict()
    assert data["cluster_name"] == "JSON Test"
    assert data["title"] == "Research Brief: JSON Test"
    assert data["document_count"] == 0
    assert isinstance(data["generated_at"], str)


@pytest.mark.asyncio
async def test_research_brief_builder_with_fallback_analyzed_document():
    engine = KeywordEngine(
        keywords=frozenset({"halving", "regulation"}),
        watchlist_entries=[
            WatchlistEntry(
                symbol="BTC",
                name="Bitcoin",
                aliases=frozenset({"bitcoin"}),
                tags=(),
                category="crypto",
            )
        ],
        entity_aliases=[],
    )
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)
    doc = make_document(
        published_at=datetime.now(UTC),
        title="Bitcoin regulation update",
        raw_text="BTC regulation and halving continue to drive discussion.",
    )

    result = await pipeline.run(doc)
    assert result.analysis_result is not None

    result.apply_to_document()
    doc.is_analyzed = True

    brief = ResearchBriefBuilder("Fallback").build([doc])

    assert brief.document_count == 1
    assert brief.summary.startswith("1 analyzed documents")
    assert brief.top_documents[0].title == "Bitcoin regulation update"
    assert brief.top_assets[0].name == "BTC"


def test_brief_window_filters_before_limit_and_exposes_source_bounds():
    now = datetime(2026, 9, 7, 12, tzinfo=UTC)
    start = now - timedelta(hours=24)
    docs = [
        make_document(is_analyzed=True, published_at=stamp)
        for stamp in [
            start - timedelta(seconds=1),
            None,
            now + timedelta(seconds=1),
            start,
            now,
            now.replace(tzinfo=None),
        ]
    ]
    brief = ResearchBriefBuilder("trial").build(docs, now=now, limit=2)
    assert brief.document_count == 2
    assert brief.generated_at == brief.window_end == now
    assert brief.window_start == brief.oldest_source_timestamp == start
    assert brief.newest_source_timestamp == now
    assert brief.data_state == "current"
    assert "Report window" in brief.to_markdown()
    assert brief.to_json_dict()["data_state"] == "current"


def test_old_or_undated_documents_produce_explicit_empty_state():
    now = datetime(2026, 9, 7, tzinfo=UTC)
    docs = [
        make_document(is_analyzed=True, published_at=now - timedelta(days=2)),
        make_document(is_analyzed=True, published_at=None),
    ]
    brief = ResearchBriefBuilder("trial").build(docs, now=now)
    assert brief.data_state == "no_current_data"
    assert brief.document_count == 0 and brief.top_documents == []
    assert brief.oldest_source_timestamp is brief.newest_source_timestamp is None
    assert "no_current_data" in brief.to_markdown()
    assert brief.window_end == now


@pytest.mark.asyncio
async def test_brief_remains_current_after_sqlite_roundtrip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.storage.db.session import Base
    from app.storage.models.document import CanonicalDocumentModel
    from app.storage.repositories.document_repo import DocumentRepository

    now = datetime(2026, 9, 8, 12, tzinfo=UTC)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine)
        async with session_factory.begin() as session:
            session.add(
                CanonicalDocumentModel(
                    id="00000000-0000-0000-0000-000000000001",
                    url="https://example.com/trial",
                    title="Trial",
                    is_analyzed=True,
                    published_at=now - timedelta(hours=1),
                )
            )
        async with session_factory() as session:
            docs = await DocumentRepository(session).list(is_analyzed=True)
        brief = ResearchBriefBuilder("sqlite").build(docs, now=now)
        assert brief.document_count == 1
        assert brief.newest_source_timestamp == now - timedelta(hours=1)
        assert "UTC" in brief.source_timestamp_policy
    finally:
        await engine.dispose()


def test_timezone_offset_compares_instants_and_does_not_mutate_input():
    stamp = datetime.fromisoformat("2026-09-08T13:00:00+02:00")
    now = datetime(2026, 9, 8, 12, tzinfo=UTC)
    doc = make_document(is_analyzed=True, published_at=stamp)
    brief = ResearchBriefBuilder("offset").build([doc], now=now)
    assert brief.document_count == 1
    assert doc.published_at == stamp


def test_cli_unknown_watchlist_is_rejected_before_database_access(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from app.cli.commands import research_core

    monkeypatch.setattr(
        research_core, "get_settings", lambda: SimpleNamespace(monitor_dir=tmp_path)
    )
    result = CliRunner().invoke(research_core.research_core_app, ["brief", "missing"])
    assert result.exit_code == 2
    assert "Watchlist is empty or does not exist" in result.output
