import asyncio
import datetime
import json
import os
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

import app.cli.main as cli_main
from app.cli.main import app
from app.core.domain.document import CanonicalDocument
from app.core.enums import (
    AnalysisSource,
    DocumentStatus,
    MarketScope,
    SentimentLabel,
    SourceStatus,
    SourceType,
)
from app.core.settings import AppSettings
from app.ingestion.rss.service import FetchResult, RSSCollectedFeed
from app.integrations.openai.provider import OpenAIAnalysisProvider
from app.research.briefs import ResearchBriefBuilder
from app.research.datasets import export_training_data
from app.research.signals import extract_signal_candidates
from app.storage.db.session import Base
from app.storage.models.document import CanonicalDocumentModel
from app.storage.repositories.document_repo import DocumentRepository
from tests.unit.factories import make_analysis_result, make_llm_output


def test_full_system_e2e_flow(monkeypatch):
    """
    Enforces the concrete system test:
    1. RSS gepollt
    2. Eintrag persistiert
    3. Erscheint als pending
    4. analyze-pending verarbeitet ihn
    5. AnalysisResult gespeichert mit (Relevance, Impact, Novelty, Confidence)
    6. Dokumentstatus ist aktualisiert
    7. Tests laufen grün
    """
    runner = CliRunner()

    db_path = f"test_e2e_db_{uuid.uuid4().hex[:8]}.sqlite"
    async_db_url = f"sqlite+aiosqlite:///{db_path}"
    sync_db_url = f"sqlite:///{db_path}"

    # 1. Synchronously init the DB to sidestep AsyncIO/SQLAlchemy cross-loop thread poisoning
    sync_engine = create_engine(sync_db_url)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    sync_session = sessionmaker(bind=sync_engine)

    try:
        # Mock settings to enforce use of our temporary test database and a fake API key
        if hasattr(cli_main.get_settings, "cache_clear"):
            cli_main.get_settings.cache_clear()

        real_get_settings = cli_main.get_settings

        def fake_get_settings():
            s = real_get_settings()
            s.db.url = async_db_url
            s.providers.openai_api_key = "fake_key_for_test"
            return s

        monkeypatch.setattr(cli_main, "get_settings", fake_get_settings)

        # Mock RSS feed collection to return 1 valid entry
        async def fake_collect(*args, **kwargs):
            class FakeResolved:
                feed_title = "E2E Test Feed"
                resolved_url = "https://example.com/e2e-feed"

            class FakeClass:
                source_type = SourceType.RSS_FEED
                status = SourceStatus.ACTIVE
                notes = ""

            docs = [
                CanonicalDocument(
                    id=uuid.uuid4(),
                    url="https://example.com/e2e-article-1",
                    title="System Test Article about Bitcoin",
                    raw_text=(
                        "Bitcoin adoption grows rapidly worldwide."
                        " This is a crucial market update for investors."
                    ),
                    published_at=datetime.datetime.now(datetime.UTC),
                )
            ]

            return RSSCollectedFeed(
                classification=FakeClass(),
                resolved_feed=FakeResolved(),
                fetch_result=FetchResult(
                    success=True,
                    source_id="btc-echo",
                    fetched_at=datetime.datetime.now(datetime.UTC),
                    documents=docs,
                ),
            )

        monkeypatch.setattr(cli_main, "_collect_rss_feed", fake_collect)

        # Execute Step 1 & 2: Ingest & Persist
        result1 = runner.invoke(app, ["ingest", "rss", "http://fake.url"])
        assert result1.exit_code == 0
        assert "Saved: 1" in result1.stdout

        # 3. Verify it's pending synchronously via our completely independent connection
        with sync_session() as session:
            # SQLAlchemy 2.0 select
            doc = session.execute(
                select(CanonicalDocumentModel).where(~CanonicalDocumentModel.is_analyzed)
            ).scalar_one_or_none()
            assert doc is not None
            assert doc.title == "System Test Article about Bitcoin"
            assert doc.url == "https://example.com/e2e-article-1"
            doc_id_str = str(doc.id)

        # 4. Mock OpenAI provider to avoid network calls during test
        async def fake_analyze(self, text, **kwargs):
            from tests.unit.factories import make_llm_output

            return make_llm_output(
                relevance_score=0.9,
                impact_score=0.8,
                novelty_score=0.7,
                confidence_score=0.95,
                sentiment_label=SentimentLabel.BULLISH,
            )

        monkeypatch.setattr(OpenAIAnalysisProvider, "analyze", fake_analyze)

        # Execute Step 4: analyze-pending
        result2 = runner.invoke(app, ["query", "analyze-pending"])
        assert result2.exit_code == 0
        assert "Analysis complete! 1 success, 0 failed." in result2.stdout

        # 5. Result Validation: Verify scores were written to canonical_documents
        # Note: AnalysisResult is in-memory only — scores are denormalized to canonical_documents
        with sync_session() as session:
            row = session.execute(
                text(
                    "SELECT is_analyzed, relevance_score, impact_score,"
                    " novelty_score, sentiment_label"
                    " FROM canonical_documents WHERE id = :id"
                ),
                {"id": doc_id_str},
            ).fetchone()

            assert row is not None
            assert row[0] == 1  # is_analyzed=True
            assert row[1] >= 0.9  # relevance (blended; boosted by "Bitcoin" keyword)
            assert row[2] == 0.8  # impact_score
            assert row[3] == 0.7  # novelty_score
            assert row[4] == "bullish"  # sentiment_label

    finally:
        sync_engine.dispose()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass  # Windows file lock workaround


def test_analysis_source_e2e_cli_db_and_research_consumers(tmp_path, monkeypatch):
    runner = CliRunner()
    db_path = tmp_path / "analysis_source_e2e.sqlite"
    async_db_url = f"sqlite+aiosqlite:///{db_path}"

    async_engine = create_async_engine(async_db_url)
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def init_db() -> None:
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def seed_doc() -> str:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            persisted = await repo.save(
                CanonicalDocument(
                    url="https://example.com/internal-analysis",
                    title="Bitcoin macro companion test",
                    raw_text="Bitcoin macro liquidity conditions remain supportive.",
                )
            )
            return str(persisted.id)

    async def load_doc(document_id: str) -> CanonicalDocument | None:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            return await repo.get_by_id(document_id)

    class FakeKeywordEngine:
        def match(self, text: str) -> list[object]:
            return []

        def match_tickers(self, text: str) -> list[str]:
            return ["BTC"]

    class FakeCompanionProvider:
        provider_name = "companion"
        model = "kai-analyst-v1"

        async def analyze(self, title: str, text: str, context=None):
            return make_llm_output(
                sentiment_label=SentimentLabel.BULLISH,
                sentiment_score=0.6,
                relevance_score=0.8,
                impact_score=0.55,
                novelty_score=0.45,
                confidence_score=0.75,
                spam_probability=0.03,
                market_scope=MarketScope.CRYPTO,
                affected_assets=["BTC"],
                tags=["companion"],
                actionable=True,
            )

    settings = AppSettings()
    settings.db.url = async_db_url
    settings.monitor_dir = str(tmp_path)

    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.storage.db.session.build_session_factory",
        lambda _db: session_factory,
    )
    monkeypatch.setattr(
        "app.analysis.keywords.engine.KeywordEngine.from_monitor_dir",
        lambda _path: FakeKeywordEngine(),
    )
    monkeypatch.setattr(
        "app.analysis.factory.create_provider",
        lambda provider_type, _settings: FakeCompanionProvider(),
    )

    try:
        asyncio.run(init_db())
        document_id = asyncio.run(seed_doc())

        result = runner.invoke(
            app,
            ["query", "analyze-pending", "--provider", "companion", "--limit", "10"],
        )

        assert result.exit_code == 0
        assert "Analyzing 1 documents" in result.output
        assert "Analysis complete! 1 success, 0 failed." in result.output

        stored = asyncio.run(load_doc(document_id))

        assert stored is not None
        assert stored.status == DocumentStatus.ANALYZED
        assert stored.is_analyzed is True
        assert stored.provider == "companion"
        assert stored.analysis_source == AnalysisSource.INTERNAL
        assert stored.effective_analysis_source == AnalysisSource.INTERNAL

        brief = ResearchBriefBuilder("companion-e2e").build([stored])
        assert brief.top_documents[0].analysis_source == "internal"

        signals = extract_signal_candidates([stored], min_priority=0)
        assert len(signals) == 1
        assert signals[0].analysis_source == "internal"

        dataset_path = tmp_path / "companion_dataset.jsonl"
        export_result = runner.invoke(
            app,
            [
                "research",
                "dataset-export",
                str(dataset_path),
                "--source-type",
                "internal",
                "--limit",
                "10",
            ],
        )

        assert export_result.exit_code == 0
        row = json.loads(dataset_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["metadata"]["provider"] == "companion"
        assert row["metadata"]["analysis_source"] == "internal"
    finally:
        asyncio.run(async_engine.dispose())


def test_ensemble_winner_trace_e2e_cli_db_and_dataset_filtering(tmp_path, monkeypatch):
    runner = CliRunner()
    db_path = tmp_path / "ensemble_winner_e2e.sqlite"
    async_db_url = f"sqlite+aiosqlite:///{db_path}"

    async_engine = create_async_engine(async_db_url)
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def init_db() -> None:
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def seed_docs() -> tuple[str, str]:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            pending = await repo.save(
                CanonicalDocument(
                    url="https://example.com/ensemble-openai",
                    title="Bitcoin ETF ensemble winner test",
                    raw_text=(
                        "Bitcoin ETF demand stays strong amid"
                        " growing institutional interest globally."
                    ),
                )
            )
            internal = await repo.save(
                CanonicalDocument(
                    url="https://example.com/internal-teacher",
                    title="Internal-only analysis row",
                    raw_text=(
                        "Macro liquidity remains stable for BTC"
                        " with supportive monetary conditions."
                    ),
                )
            )
            await repo.update_analysis(
                str(internal.id),
                make_analysis_result(
                    document_id=internal.id,
                    analysis_source=AnalysisSource.INTERNAL,
                    sentiment_label=SentimentLabel.NEUTRAL,
                    sentiment_score=0.0,
                    relevance_score=0.45,
                    impact_score=0.3,
                    novelty_score=0.25,
                    market_scope=MarketScope.CRYPTO,
                    affected_assets=["BTC"],
                    explanation_short="Internal baseline",
                    explanation_long="Internal baseline path.",
                    recommended_priority=5,
                ),
                provider_name="internal",
            )
            return str(pending.id), str(internal.id)

    async def load_doc(document_id: str) -> CanonicalDocument | None:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            return await repo.get_by_id(document_id)

    class FakeKeywordEngine:
        def match(self, text: str) -> list[object]:
            return []

        def match_tickers(self, text: str) -> list[str]:
            return ["BTC"]

    class FakeOpenAIProvider:
        provider_name = "openai"
        model = "gpt-4o"

        async def analyze(self, title: str, text: str, context=None):
            return make_llm_output(
                sentiment_label=SentimentLabel.BULLISH,
                sentiment_score=0.7,
                relevance_score=0.88,
                impact_score=0.62,
                novelty_score=0.41,
                confidence_score=0.82,
                spam_probability=0.02,
                market_scope=MarketScope.CRYPTO,
                affected_assets=["BTC"],
                tags=["ensemble"],
                actionable=True,
            )

    class FakeInternalProvider:
        provider_name = "internal"
        model = "rule-heuristic-v1"

        async def analyze(self, title: str, text: str, context=None):
            return make_llm_output(
                sentiment_label=SentimentLabel.NEUTRAL,
                sentiment_score=0.0,
                relevance_score=0.4,
                impact_score=0.25,
                novelty_score=0.2,
                confidence_score=0.55,
                spam_probability=0.04,
                market_scope=MarketScope.CRYPTO,
                affected_assets=["BTC"],
                tags=["internal"],
                actionable=False,
            )

    from app.analysis.ensemble.provider import EnsembleProvider

    settings = AppSettings()
    settings.db.url = async_db_url
    settings.monitor_dir = str(tmp_path)

    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.storage.db.session.build_session_factory",
        lambda _db: session_factory,
    )
    monkeypatch.setattr(
        "app.analysis.keywords.engine.KeywordEngine.from_monitor_dir",
        lambda _path: FakeKeywordEngine(),
    )
    monkeypatch.setattr(
        "app.analysis.factory.create_provider",
        lambda provider_type, _settings: EnsembleProvider(
            [FakeOpenAIProvider(), FakeInternalProvider()]
        ),
    )

    try:
        asyncio.run(init_db())
        pending_id, internal_id = asyncio.run(seed_docs())

        result = runner.invoke(
            app,
            ["query", "analyze-pending", "--provider", "openai", "--limit", "10"],
        )

        assert result.exit_code == 0
        assert "Analyzing 1 documents" in result.output
        assert "Analysis complete! 1 success, 0 failed." in result.output

        stored = asyncio.run(load_doc(pending_id))
        internal_doc = asyncio.run(load_doc(internal_id))

        assert stored is not None
        assert stored.status == DocumentStatus.ANALYZED
        assert stored.provider == "openai"
        assert stored.analysis_source == AnalysisSource.EXTERNAL_LLM
        assert stored.effective_analysis_source == AnalysisSource.EXTERNAL_LLM
        assert stored.metadata["ensemble_chain"] == ["openai", "internal"]

        brief = ResearchBriefBuilder("ensemble-e2e").build([stored])
        assert brief.top_documents[0].analysis_source == "external_llm"

        signals = extract_signal_candidates([stored], min_priority=0)
        assert len(signals) == 1
        assert signals[0].analysis_source == "external_llm"

        assert internal_doc is not None
        assert internal_doc.analysis_source == AnalysisSource.INTERNAL

        dataset_path = tmp_path / "ensemble_external_dataset.jsonl"
        export_result = runner.invoke(
            app,
            [
                "research",
                "dataset-export",
                str(dataset_path),
                "--source-type",
                "external_llm",
                "--limit",
                "10",
            ],
        )

        assert export_result.exit_code == 0
        rows = [
            json.loads(line)
            for line in dataset_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(rows) == 1
        assert rows[0]["metadata"]["provider"] == "openai"
        assert rows[0]["metadata"]["analysis_source"] == "external_llm"
    finally:
        asyncio.run(async_engine.dispose())


def test_legacy_analysis_source_fallback_remains_stable_for_research_consumers(tmp_path):
    db_path = tmp_path / "analysis_source_legacy.sqlite"
    async_db_url = f"sqlite+aiosqlite:///{db_path}"

    async def exercise_legacy_path() -> CanonicalDocument | None:
        engine = create_async_engine(async_db_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            async with session_factory.begin() as session:
                repo = DocumentRepository(session)
                persisted = await repo.save(
                    CanonicalDocument(
                        url="https://example.com/legacy-openai",
                        title="Legacy OpenAI analyzed document",
                        raw_text=(
                            "Macro policy and bitcoin liquidity"
                            " update with central bank implications."
                        ),
                        provider="openai",
                    )
                )
                await repo.update_analysis(
                    str(persisted.id),
                    make_analysis_result(
                        document_id=persisted.id,
                        sentiment_label=SentimentLabel.BULLISH,
                        sentiment_score=0.7,
                        relevance_score=0.9,
                        impact_score=0.6,
                        novelty_score=0.5,
                        market_scope=MarketScope.MACRO,
                        affected_assets=["BTC"],
                        explanation_short="Legacy external analysis",
                        explanation_long="Legacy external analysis path.",
                        recommended_priority=8,
                    ),
                )

            async with session_factory.begin() as session:
                repo = DocumentRepository(session)
                return await repo.get_by_url("https://example.com/legacy-openai")
        finally:
            await engine.dispose()

    stored = asyncio.run(exercise_legacy_path())

    assert stored is not None
    assert stored.provider == "openai"
    assert stored.analysis_source is None
    assert stored.effective_analysis_source == AnalysisSource.EXTERNAL_LLM

    brief = ResearchBriefBuilder("legacy").build([stored])
    assert brief.top_documents[0].analysis_source == "external_llm"

    signals = extract_signal_candidates([stored], min_priority=0)
    assert len(signals) == 1
    assert signals[0].analysis_source == "external_llm"

    dataset_path = tmp_path / "legacy_dataset.jsonl"
    count = export_training_data([stored], dataset_path)
    row = json.loads(dataset_path.read_text(encoding="utf-8").splitlines()[0])

    assert count == 1
    assert row["metadata"]["provider"] == "openai"
    assert row["metadata"]["analysis_source"] == "external_llm"
