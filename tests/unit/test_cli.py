from datetime import UTC, datetime

from typer.testing import CliRunner

from app.cli import main as cli_main
from app.cli.main import app
from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceStatus, SourceType
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

    async def fake_list(self, is_analyzed: bool, limit: int):
        return [
            CanonicalDocument(url="https://example.com/1", title="Doc 1"),
            CanonicalDocument(url="https://example.com/2", title="Doc 2"),
        ]

    updated_docs = []
    async def fake_update(self, doc: CanonicalDocument) -> None:
        updated_docs.append(doc)

    async def fake_run_batch(self, docs):
        results = []
        for doc in docs:
            results.append(
                PipelineResult(
                    document=doc,
                    llm_output=make_llm_output(),
                    analysis_result=make_analysis_result(document_id=doc.id)
                )
            )
        return results

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self): return object()
                async def __aexit__(self, exc_type, exc, tb): return False
            return FakeSessionContext()

    from _pytest.monkeypatch import MonkeyPatch

    from app.analysis import pipeline
    from app.analysis.keywords import engine as kw_engine
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    mp = MonkeyPatch()
    mp.setattr(db_session, "build_session_factory", lambda _settings: FakeSessionFactory())
    mp.setattr(document_repo.DocumentRepository, "list", fake_list)
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
    async def fake_list(self, is_analyzed: bool, limit: int):
        return []

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self): return object()
                async def __aexit__(self, exc_type, exc, tb): return False
            return FakeSessionContext()

    from _pytest.monkeypatch import MonkeyPatch

    from app.analysis.keywords import engine as kw_engine
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    mp = MonkeyPatch()
    mp.setattr(db_session, "build_session_factory", lambda _settings: FakeSessionFactory())
    mp.setattr(document_repo.DocumentRepository, "list", fake_list)
    mp.setattr(kw_engine.KeywordEngine, "from_monitor_dir", lambda p: object())

    try:
        result = runner.invoke(app, ["query", "analyze-pending"])
        assert result.exit_code == 0
        assert "No pending documents" in result.output
    finally:
        mp.undo()
