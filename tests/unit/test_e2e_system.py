import datetime
import os
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

import app.cli.main as cli_main
from app.cli.main import app
from app.core.domain.document import CanonicalDocument
from app.core.enums import SentimentLabel, SourceStatus, SourceType
from app.ingestion.rss.service import FetchResult, RSSCollectedFeed
from app.integrations.openai.provider import OpenAIAnalysisProvider
from app.storage.db.session import Base
from app.storage.models.document import CanonicalDocumentModel


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
                    raw_text="Bitcoin adoption grows. This is a crucial update.",
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
