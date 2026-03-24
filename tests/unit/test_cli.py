from datetime import UTC, datetime

from typer.testing import CliRunner

from app.cli import main as cli_main
from app.cli.main import app
from app.core.domain.document import CanonicalDocument
from app.core.enums import (
    DocumentStatus,
    MarketScope,
    SentimentLabel,
    SourceStatus,
    SourceType,
)
from app.core.settings import AppSettings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.classifier import ClassificationResult
from app.ingestion.resolvers.rss import RSSResolveResult
from app.ingestion.rss.service import RSSCollectedFeed
from app.storage.document_ingest import IngestPersistStats

runner = CliRunner()


def _collected_feed(
    *,
    url: str,
    docs: list[CanonicalDocument],
    resolved_url: str | None = None,
    is_valid: bool = True,
    error: str | None = None,
) -> RSSCollectedFeed:
    return RSSCollectedFeed(
        classification=ClassificationResult(SourceType.RSS_FEED, SourceStatus.ACTIVE),
        resolved_feed=RSSResolveResult(
            url=url,
            is_valid=is_valid,
            resolved_url=resolved_url or url,
            feed_title="Test Feed" if is_valid else None,
            entry_count=len(docs),
            error=error,
        ),
        fetch_result=FetchResult(
            source_id="manual",
            documents=docs,
            fetched_at=datetime.now(UTC),
            success=is_valid and error is None,
            error=error,
        ),
    )


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "trading-bot" in result.output.lower() or "Usage" in result.output


def test_sources_classify_rss() -> None:
    result = runner.invoke(app, ["sources", "classify", "https://cointelegraph.com/rss"])
    assert result.exit_code == 0
    assert "rss_feed" in result.output


def test_sources_classify_youtube() -> None:
    result = runner.invoke(app, ["sources", "classify", "https://www.youtube.com/@Bankless"])
    assert result.exit_code == 0
    assert "youtube_channel" in result.output


def test_sources_classify_apple_podcast() -> None:
    result = runner.invoke(
        app,
        ["sources", "classify", "https://podcasts.apple.com/de/podcast/x/id123"],
    )
    assert result.exit_code == 0
    assert "requires_api" in result.output


def test_podcasts_resolve() -> None:
    result = runner.invoke(app, ["podcasts", "resolve"])
    assert result.exit_code == 0
    assert "Resolved" in result.output
    assert "Unresolved" in result.output


def test_youtube_resolve() -> None:
    result = runner.invoke(app, ["youtube", "resolve"])
    assert result.exit_code == 0
    assert "channels" in result.output.lower() or "handle" in result.output.lower()


def test_query_validate() -> None:
    result = runner.invoke(app, ["query", "validate", "bitcoin AND (ethereum OR solana) NOT scam"])
    assert result.exit_code == 0
    assert "✓ Valid Syntax!" in result.output
    assert "AST" in result.output
    # Check that part of the parsed AST structure is shown
    assert "AND(" in result.output
    assert "NOT(" in result.output


def test_query_validate_fail() -> None:
    result = runner.invoke(app, ["query", "validate", "bitcoin AND"])
    assert result.exit_code == 1
    assert "Syntax Error" in result.output


def test_ingest_rss_dry_run_skips_storage(monkeypatch) -> None:
    docs = [
        CanonicalDocument(url="https://example.com/article-1", title="Bitcoin rises"),
        CanonicalDocument(url="https://example.com/article-1", title="Bitcoin rises"),
        CanonicalDocument(url="https://example.com/article-2", title="Ethereum upgrade"),
    ]

    async def fake_collect(url: str, *, source_id: str, source_name: str) -> RSSCollectedFeed:
        return _collected_feed(url=url, docs=docs)

    async def fake_persist(
        session_factory,
        result: FetchResult,
        *,
        dry_run: bool,
        existing_limit: int = 1000,
    ):
        assert session_factory is None
        assert dry_run is True
        return IngestPersistStats(
            fetched_count=len(docs),
            candidate_count=2,
            batch_duplicates=1,
            existing_duplicates=0,
            saved_count=0,
            failed_count=0,
            preview_documents=[docs[0], docs[2]],
        )

    def fail_build_session_factory(_settings):
        raise AssertionError("dry-run should not build a DB session factory")

    monkeypatch.setattr(cli_main, "_collect_rss_feed", fake_collect)
    monkeypatch.setattr(cli_main, "persist_fetch_result", fake_persist)
    monkeypatch.setattr(cli_main, "build_session_factory", fail_build_session_factory)

    result = runner.invoke(app, ["ingest", "rss", "https://example.com/feed", "--dry-run"])

    assert result.exit_code == 0
    assert "RSS feed validated:" in result.output
    assert "Batch duplicates skipped:" in result.output
    assert "Dry run:" in result.output
    assert "would store up to 2 documents" in result.output
    assert "Bitcoin rises" in result.output


def test_ingest_rss_persists_only_new_documents(monkeypatch) -> None:
    docs = [
        CanonicalDocument(url="https://example.com/article-1", title="Bitcoin rises"),
        CanonicalDocument(url="https://example.com/article-1", title="Bitcoin rises"),
        CanonicalDocument(url="https://example.com/article-2", title="Ethereum upgrade"),
    ]

    async def fake_collect(url: str, *, source_id: str, source_name: str) -> RSSCollectedFeed:
        return _collected_feed(url=url, docs=docs, resolved_url="https://example.com/feed.xml")

    saved_preview = [CanonicalDocument(url="https://example.com/article-1", title="Bitcoin rises")]

    async def fake_persist(
        session_factory,
        result: FetchResult,
        *,
        dry_run: bool,
        existing_limit: int = 1000,
    ):
        assert session_factory == "session-factory"
        assert dry_run is False
        return IngestPersistStats(
            fetched_count=len(docs),
            candidate_count=2,
            batch_duplicates=1,
            existing_duplicates=1,
            saved_count=1,
            failed_count=0,
            preview_documents=saved_preview,
        )

    monkeypatch.setattr(cli_main, "_collect_rss_feed", fake_collect)
    monkeypatch.setattr(cli_main, "build_session_factory", lambda _settings: "session-factory")
    monkeypatch.setattr(cli_main, "persist_fetch_result", fake_persist)

    result = runner.invoke(app, ["ingest", "rss", "https://example.com/feed"])

    assert result.exit_code == 0
    assert "RSS feed validated: https://example.com/feed.xml" in result.output
    assert "Existing duplicates skipped: 1" in result.output
    assert "Saved: 1" in result.output
    assert "https://example.com/article-1" in result.output


def test_ingest_rss_rejects_invalid_feed(monkeypatch) -> None:
    async def fake_collect(url: str, *, source_id: str, source_name: str) -> RSSCollectedFeed:
        return RSSCollectedFeed(
            classification=ClassificationResult(SourceType.WEBSITE, SourceStatus.ACTIVE),
            resolved_feed=RSSResolveResult(
                url=url,
                is_valid=False,
                resolved_url=None,
                feed_title=None,
                entry_count=0,
                error="Response is not a valid RSS or Atom feed",
            ),
            fetch_result=FetchResult(
                source_id=source_id,
                documents=[],
                fetched_at=datetime.now(UTC),
                success=False,
                error=(
                    "URL is not a valid RSS/Atom feed. "
                    "Classified as website (active). "
                    "Response is not a valid RSS or Atom feed"
                ),
            ),
        )

    monkeypatch.setattr(cli_main, "_collect_rss_feed", fake_collect)

    result = runner.invoke(app, ["ingest", "rss", "https://example.com"])

    assert result.exit_code == 1
    assert "URL is not a valid RSS/Atom feed" in result.output
    assert "website" in result.output


def test_query_analyze_pending() -> None:
    from app.analysis.pipeline import PipelineResult
    from tests.unit.factories import make_analysis_result, make_llm_output

    async def fake_list(self, limit: int = 50):
        return [
            CanonicalDocument(url="https://example.com/1", title="Doc 1"),
            CanonicalDocument(url="https://example.com/2", title="Doc 2"),
        ]

    updated_docs = []

    async def fake_update(
        self, document_id: str, result, *, provider_name: str | None = None, metadata_updates=None
    ) -> None:
        updated_docs.append(document_id)

    async def fake_run_batch(self, docs):
        results = []
        for doc in docs:
            results.append(
                PipelineResult(
                    document=doc,
                    llm_output=make_llm_output(),
                    analysis_result=make_analysis_result(document_id=doc.id),
                )
            )
        return results

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FakeSessionContext()

    from _pytest.monkeypatch import MonkeyPatch

    from app.analysis import pipeline
    from app.analysis.keywords import engine as kw_engine
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    mp = MonkeyPatch()
    mp.setattr(db_session, "build_session_factory", lambda _settings: FakeSessionFactory())
    mp.setattr(document_repo.DocumentRepository, "get_pending_documents", fake_list)
    mp.setattr(document_repo.DocumentRepository, "update_analysis", fake_update)
    mp.setattr(pipeline.AnalysisPipeline, "run_batch", fake_run_batch)
    mp.setattr(kw_engine.KeywordEngine, "from_monitor_dir", lambda p: object())

    try:
        result = runner.invoke(app, ["query", "analyze-pending", "--limit", "2"])
        assert result.exit_code == 0
        assert "Analyzing 2 documents" in result.output
        assert "Analysis complete! 2 success" in result.output
        assert len(updated_docs) == 2
    finally:
        mp.undo()


def test_query_analyze_pending_empty() -> None:
    async def fake_list(self, limit: int = 50):
        return []

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FakeSessionContext()

    from _pytest.monkeypatch import MonkeyPatch

    from app.analysis.keywords import engine as kw_engine
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    mp = MonkeyPatch()
    mp.setattr(db_session, "build_session_factory", lambda _settings: FakeSessionFactory())
    mp.setattr(document_repo.DocumentRepository, "get_pending_documents", fake_list)
    mp.setattr(kw_engine.KeywordEngine, "from_monitor_dir", lambda p: object())

    try:
        result = runner.invoke(app, ["query", "analyze-pending"])
        assert result.exit_code == 0
        assert "No pending documents" in result.output
    finally:
        mp.undo()


def test_query_analyze_pending_without_openai_key_uses_fallback_analysis(monkeypatch) -> None:
    from app.analysis.keywords import engine as kw_engine
    from app.analysis.keywords.engine import KeywordHit
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    settings = AppSettings()
    settings.providers.openai_api_key = ""
    captured_results = []

    class FakeKeywordEngine:
        def match(self, text: str) -> list[KeywordHit]:
            return [
                KeywordHit(canonical="BTC", category="crypto", occurrences=2),
                KeywordHit(canonical="regulation", category="keyword", occurrences=1),
            ]

        def match_tickers(self, text: str) -> list[str]:
            return ["BTC"]

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FakeSessionContext()

    async def fake_list(self, limit: int = 50):
        return [
            CanonicalDocument(
                url="https://example.com/fallback-doc",
                title="Bitcoin regulation update",
                raw_text="BTC regulation pressure remains elevated.",
            )
        ]

    async def fake_update(
        self, document_id: str, result, *, provider_name: str | None = None, metadata_updates=None
    ) -> None:
        captured_results.append(result)

    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "build_session_factory", lambda _settings: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "get_pending_documents", fake_list)
    monkeypatch.setattr(document_repo.DocumentRepository, "update_analysis", fake_update)
    monkeypatch.setattr(
        kw_engine.KeywordEngine,
        "from_monitor_dir",
        lambda _path: FakeKeywordEngine(),
    )

    result = runner.invoke(app, ["query", "analyze-pending", "--limit", "1"])

    assert result.exit_code == 0
    assert "No API key found for provider 'openai'" in result.output
    assert "Analysis complete! 1 success, 0 failed." in result.output
    assert len(captured_results) == 1
    assert captured_results[0].recommended_priority is not None
    assert captured_results[0].affected_assets == ["BTC"]
    assert captured_results[0].explanation_short.startswith("Rule-based fallback analysis")


def test_ingest_rss_saved_documents_flow_into_analyze_pending(monkeypatch) -> None:
    from app.analysis.keywords import engine as kw_engine
    from app.integrations.openai import provider as openai_provider
    from app.storage import document_ingest
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo
    from tests.unit.factories import make_llm_output

    settings = AppSettings()
    settings.providers.openai_api_key = "test-openai-key"
    stored_docs: list[CanonicalDocument] = []
    saved_pending_statuses: list[DocumentStatus] = []

    docs = [
        CanonicalDocument(
            url="https://example.com/article-1?utm_source=rss",
            title="Bitcoin ETF approved",
            raw_text="Bitcoin ETF approval drives the market.",
        ),
        CanonicalDocument(
            url="https://example.com/article-1",
            title="Bitcoin ETF Approved",
            raw_text="Bitcoin ETF approval drives the market.",
        ),
        CanonicalDocument(
            url="https://example.com/article-2",
            title="Ethereum upgrade ships",
            raw_text="Ethereum ships its latest upgrade.",
        ),
    ]

    async def fake_collect(url: str, *, source_id: str, source_name: str) -> RSSCollectedFeed:
        return _collected_feed(url=url, docs=docs, resolved_url="https://example.com/feed.xml")

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FakeSessionContext()

    class FakeDocumentRepository:
        def __init__(self, session) -> None:
            self._session = session

        async def list(self, **kwargs) -> list[CanonicalDocument]:
            docs_to_return = stored_docs
            is_analyzed = kwargs.get("is_analyzed")
            if is_analyzed is not None:
                docs_to_return = [doc for doc in stored_docs if doc.is_analyzed is is_analyzed]
            limit = kwargs.get("limit", len(docs_to_return))
            return docs_to_return[:limit]

        async def get_by_url(self, url: str):
            return next((doc for doc in stored_docs if doc.url == url), None)

        async def get_by_hash(self, content_hash: str):
            return next((doc for doc in stored_docs if doc.content_hash == content_hash), None)

        async def save_document(self, doc: CanonicalDocument) -> str:
            saved_pending_statuses.append(doc.status)
            stored_docs.append(
                doc.model_copy(
                    update={
                        "status": DocumentStatus.PERSISTED,
                        "is_duplicate": False,
                        "is_analyzed": False,
                    }
                )
            )
            return str(doc.id)

        async def save(self, doc: CanonicalDocument) -> CanonicalDocument:
            persisted = doc.model_copy(
                update={
                    "status": DocumentStatus.PERSISTED,
                    "is_duplicate": False,
                    "is_analyzed": False,
                }
            )
            stored_docs.append(persisted)
            return persisted

        async def get_pending_documents(self, limit: int = 50):
            docs_to_return = [
                doc
                for doc in stored_docs
                if doc.status == DocumentStatus.PERSISTED
                and not doc.is_analyzed
                and not doc.is_duplicate
            ]
            return docs_to_return[:limit]

        async def update_analysis(
            self,
            document_id: str,
            result,
            *,
            provider_name: str | None = None,
            metadata_updates=None,
        ) -> None:
            for index, existing in enumerate(stored_docs):
                if str(existing.id) == document_id:
                    updated_metadata = dict(existing.metadata)
                    if metadata_updates:
                        updated_metadata.update(metadata_updates)
                    stored_docs[index] = existing.model_copy(
                        update={
                            "provider": provider_name,
                            "status": DocumentStatus.ANALYZED,
                            "is_analyzed": True,
                            "is_duplicate": False,
                            "priority_score": result.recommended_priority,
                            "relevance_score": result.relevance_score,
                            "tickers": result.affected_assets,
                            "metadata": updated_metadata,
                        }
                    )
                    return
            raise AssertionError(f"Document {document_id} was not persisted before analysis")

    class FakeKeywordEngine:
        def match(self, text: str) -> list[object]:
            return []

        def match_tickers(self, text: str) -> list[str]:
            return ["BTC"] if "Bitcoin" in text else ["ETH"]

    class FakeOpenAIProvider:
        provider_name = "openai"
        model = "test-model"

        async def analyze(self, title: str, text: str, context=None):
            return make_llm_output(
                sentiment_label=SentimentLabel.BULLISH,
                relevance_score=0.82,
                impact_score=0.76,
                novelty_score=0.64,
                spam_probability=0.05,
                market_scope=MarketScope.CRYPTO,
                affected_assets=["BTC"] if "Bitcoin" in title else ["ETH"],
                tags=["rss"],
                actionable=True,
            )

    monkeypatch.setattr(cli_main, "_collect_rss_feed", fake_collect)
    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_main, "build_session_factory", lambda _db: FakeSessionFactory())
    monkeypatch.setattr(db_session, "build_session_factory", lambda _db: FakeSessionFactory())
    monkeypatch.setattr(document_ingest, "DocumentRepository", FakeDocumentRepository)
    monkeypatch.setattr(document_repo, "DocumentRepository", FakeDocumentRepository)
    monkeypatch.setattr(
        kw_engine.KeywordEngine,
        "from_monitor_dir",
        lambda _path: FakeKeywordEngine(),
    )
    monkeypatch.setattr(
        openai_provider.OpenAIAnalysisProvider,
        "from_settings",
        lambda _providers: FakeOpenAIProvider(),
    )

    ingest_result = runner.invoke(
        app,
        ["ingest", "rss", "https://example.com/feed", "--persist"],
    )

    assert ingest_result.exit_code == 0
    assert "Batch duplicates skipped: 1" in ingest_result.output
    assert "Saved: 2" in ingest_result.output
    assert len(stored_docs) == 2
    assert saved_pending_statuses == [DocumentStatus.PENDING, DocumentStatus.PENDING]
    assert all(doc.status == DocumentStatus.PERSISTED for doc in stored_docs)
    assert all(not doc.is_analyzed for doc in stored_docs)

    saved_doc_ids = {doc.id for doc in stored_docs}
    saved_urls = {doc.url for doc in stored_docs}

    analyze_result = runner.invoke(app, ["query", "analyze-pending", "--limit", "10"])

    assert analyze_result.exit_code == 0
    assert "Analyzing 2 documents" in analyze_result.output
    assert "Analysis complete! 2 success, 0 failed." in analyze_result.output
    assert {doc.id for doc in stored_docs} == saved_doc_ids
    assert saved_urls == {
        "https://example.com/article-1",
        "https://example.com/article-2",
    }
    assert all(doc.status == DocumentStatus.ANALYZED for doc in stored_docs)
    assert all(doc.is_analyzed for doc in stored_docs)
    assert all(doc.priority_score is not None for doc in stored_docs)
    assert all(doc.relevance_score is not None for doc in stored_docs)
    assert {ticker for doc in stored_docs for ticker in doc.tickers} == {"BTC", "ETH"}


# NOTE: Research command tests have been migrated to tests/unit/cli/:
#   test_research_core.py     — watchlists, brief, dataset-export, signals
#   test_research_companion.py — evaluate-datasets, benchmark-companion, companion pipeline
#   test_research_readiness.py — readiness, gate, drift, artifacts, runbook, review-journal
#   test_research_operator.py  — escalation, blocking, decision-pack, daily-summary, backtest
#   test_research_trading.py   — signal-handoff, handoff-acknowledge, consumer-ack
