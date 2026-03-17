from datetime import UTC, datetime

from typer.testing import CliRunner

from app.cli import main as cli_main
from app.cli.main import app
from app.core.domain.document import CanonicalDocument
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.resolvers.rss import RSSResolveResult

runner = CliRunner()


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

    async def fake_resolve(url: str, timeout: int = 10) -> RSSResolveResult:
        return RSSResolveResult(
            url=url,
            is_valid=True,
            resolved_url=url,
            feed_title="Test Feed",
            entry_count=len(docs),
        )

    async def fake_fetch(self) -> FetchResult:
        return FetchResult(
            source_id=self.source_id,
            documents=docs,
            fetched_at=datetime.now(UTC),
            success=True,
        )

    def fail_build_session_factory(_settings):
        raise AssertionError("dry-run should not build a DB session factory")

    monkeypatch.setattr(cli_main, "resolve_rss_feed", fake_resolve)
    monkeypatch.setattr(cli_main.RSSFeedAdapter, "fetch", fake_fetch)
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

    async def fake_resolve(url: str, timeout: int = 10) -> RSSResolveResult:
        return RSSResolveResult(
            url=url,
            is_valid=True,
            resolved_url="https://example.com/feed.xml",
            feed_title="Test Feed",
            entry_count=len(docs),
        )

    async def fake_fetch(self) -> FetchResult:
        return FetchResult(
            source_id=self.source_id,
            documents=docs,
            fetched_at=datetime.now(UTC),
            success=True,
        )

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSessionFactory:
        def begin(self) -> FakeSessionContext:
            return FakeSessionContext()

    class FakeDocumentRepository:
        saved_urls: list[str] = []
        existing_urls: set[str] = {"https://example.com/article-2"}

        def __init__(self, session) -> None:
            self._session = session

        async def get_by_url(self, url: str):
            return object() if url in self.existing_urls else None

        async def get_by_hash(self, content_hash: str):
            return None

        async def save(self, doc: CanonicalDocument) -> CanonicalDocument:
            self.saved_urls.append(doc.url)
            self.existing_urls.add(doc.url)
            return doc

    monkeypatch.setattr(cli_main, "resolve_rss_feed", fake_resolve)
    monkeypatch.setattr(cli_main.RSSFeedAdapter, "fetch", fake_fetch)
    monkeypatch.setattr(cli_main, "build_session_factory", lambda _settings: FakeSessionFactory())
    monkeypatch.setattr(cli_main, "DocumentRepository", FakeDocumentRepository)

    result = runner.invoke(app, ["ingest", "rss", "https://example.com/feed"])

    assert result.exit_code == 0
    assert "RSS feed validated: https://example.com/feed.xml" in result.output
    assert "Existing duplicates skipped: 1" in result.output
    assert "Saved: 1" in result.output
    assert FakeDocumentRepository.saved_urls == ["https://example.com/article-1"]


def test_ingest_rss_rejects_invalid_feed(monkeypatch) -> None:
    async def fake_resolve(url: str, timeout: int = 10) -> RSSResolveResult:
        return RSSResolveResult(
            url=url,
            is_valid=False,
            resolved_url=None,
            feed_title=None,
            entry_count=0,
            error="Response is not a valid RSS or Atom feed",
        )

    monkeypatch.setattr(cli_main, "resolve_rss_feed", fake_resolve)

    result = runner.invoke(app, ["ingest", "rss", "https://example.com"])

    assert result.exit_code == 1
    assert "URL is not a valid RSS/Atom feed" in result.output
    assert "website" in result.output
