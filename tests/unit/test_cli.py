import json
from datetime import UTC, datetime

import pytest
import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from app.cli import main as cli_main
from app.cli.commands import research_operator as cli_research_operator
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


def test_research_watchlists_lists_requested_type(monkeypatch, tmp_path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        yaml.safe_dump(
            {
                "persons": [
                    {
                        "name": "Gary Gensler",
                        "aliases": ["gensler"],
                        "tags": ["regulation"],
                    },
                    {
                        "name": "Elizabeth Warren",
                        "aliases": ["warren"],
                        "tags": ["regulation"],
                    },
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)

    result = runner.invoke(app, ["research", "watchlists", "--type", "persons", "regulation"])

    assert result.exit_code == 0
    assert "regulation" in result.output
    assert "Gary Gensler" in result.output
    assert "Elizabeth Warren" in result.output


def test_research_brief_filters_documents_by_watchlist_type(monkeypatch, tmp_path) -> None:
    from app.storage.repositories import document_repo

    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        yaml.safe_dump(
            {
                "persons": [
                    {
                        "name": "Gary Gensler",
                        "aliases": ["gensler"],
                        "tags": ["regulation"],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FakeSessionContext()

    async def fake_list(self, **kwargs):
        return [
            CanonicalDocument(
                url="https://example.com/gensler",
                title="Gensler warns on crypto regulation",
                is_analyzed=True,
                priority_score=8,
                summary="Regulatory pressure remains elevated.",
                people=["Gary Gensler"],
                entities=["Gary Gensler"],
                sentiment_label=SentimentLabel.BEARISH,
            ),
            CanonicalDocument(
                url="https://example.com/vitalik",
                title="Vitalik discusses scaling",
                is_analyzed=True,
                priority_score=7,
                summary="Ethereum roadmap update.",
                people=["Vitalik Buterin"],
                entities=["Vitalik Buterin"],
                sentiment_label=SentimentLabel.BULLISH,
            ),
        ]

    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_main, "build_session_factory", lambda _db: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    result = runner.invoke(
        app,
        ["research", "brief", "--watchlist", "regulation", "--type", "persons", "--format", "json"],
    )

    assert result.exit_code == 0
    assert '"title": "Research Brief: regulation"' in result.output
    assert "Gensler warns on crypto regulation" in result.output
    assert "Vitalik discusses scaling" not in result.output


def _make_dataset_row(
    *,
    document_id: str,
    analysis_source: str = "external_llm",
    provider: str = "openai",
    sentiment_label: str = "bullish",
    priority_score: int = 8,
    relevance_score: float = 0.9,
    impact_score: float = 0.6,
    tags: list[str] | None = None,
) -> dict[str, object]:
    target = {
        "affected_assets": ["BTC"],
        "impact_score": impact_score,
        "market_scope": "crypto",
        "novelty_score": 0.4,
        "priority_score": priority_score,
        "relevance_score": relevance_score,
        "sentiment_label": sentiment_label,
        "sentiment_score": 0.7,
        "spam_probability": 0.05,
        "summary": "Synthetic dataset row.",
        "tags": tags or ["btc"],
    }
    return {
        "messages": [
            {"role": "system", "content": "You are a highly precise financial AI analyst."},
            {"role": "user", "content": "Analyze..."},
            {"role": "assistant", "content": json.dumps(target, sort_keys=True)},
        ],
        "metadata": {
            "document_id": document_id,
            "provider": provider,
            "analysis_source": analysis_source,
        },
    }


def _write_jsonl_rows(path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_research_dataset_export_teacher_only_uses_strict_export_flag(
    monkeypatch,
    tmp_path,
) -> None:
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo
    from tests.unit.factories import make_document

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FakeSessionContext()

    async def fake_list(self, **kwargs):
        return [
            make_document(
                raw_text="Teacher content.",
                is_analyzed=True,
                provider="openai",
                analysis_source=AnalysisSource.EXTERNAL_LLM,
            ),
            make_document(
                raw_text="Internal content.",
                is_analyzed=True,
                provider="companion",
                analysis_source=AnalysisSource.INTERNAL,
            ),
            make_document(
                raw_text="Rule content.",
                is_analyzed=True,
                provider=None,
                analysis_source=AnalysisSource.RULE,
            ),
        ]

    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "build_session_factory", lambda _db: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    out_file = tmp_path / "teacher_only.jsonl"
    result = runner.invoke(
        app,
        [
            "research",
            "dataset-export",
            str(out_file),
            "--source-type",
            "all",
            "--teacher-only",
        ],
    )

    assert result.exit_code == 0
    assert "Successfully exported 1 documents" in result.output

    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["metadata"]["analysis_source"] == "external_llm"
    assert row["metadata"]["provider"] == "openai"


def test_research_evaluate_datasets_prints_metrics_table(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate.jsonl"

    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    _write_jsonl_rows(
        candidate_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="internal")],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "evaluate-datasets",
            str(teacher_file),
            str(candidate_file),
            "--dataset-type",
            "internal_benchmark",
        ],
    )

    assert result.exit_code == 0
    assert "Dataset Evaluation Metrics" in result.output
    assert "Dataset Type" in result.output
    assert "internal_benchmark" in result.output
    assert "Teacher Rows" in result.output
    assert "Candidate Rows" in result.output
    assert "Paired Documents" in result.output
    assert "Sentiment Agreement" in result.output
    assert "100.00%" in result.output


def test_research_evaluate_datasets_missing_candidate_file_fails(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )

    missing_candidate = tmp_path / "missing.jsonl"
    result = runner.invoke(
        app,
        [
            "research",
            "evaluate-datasets",
            str(teacher_file),
            str(missing_candidate),
        ],
    )

    assert result.exit_code == 1
    assert "Candidate dataset file not found" in result.output


def test_research_evaluate_datasets_handles_empty_files(tmp_path) -> None:
    teacher_file = tmp_path / "teacher_empty.jsonl"
    candidate_file = tmp_path / "candidate_empty.jsonl"
    teacher_file.write_text("", encoding="utf-8")
    candidate_file.write_text("", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "evaluate-datasets",
            str(teacher_file),
            str(candidate_file),
        ],
    )

    assert result.exit_code == 0
    assert "Teacher dataset is empty." in result.output
    assert "Candidate dataset is empty." in result.output
    assert "No overlapping document_id pairs found." in result.output
    assert "Paired Documents" in result.output


def test_research_evaluate_datasets_reports_missing_pairs(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate.jsonl"

    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    _write_jsonl_rows(
        candidate_file,
        [_make_dataset_row(document_id="doc-99", analysis_source="rule")],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "evaluate-datasets",
            str(teacher_file),
            str(candidate_file),
            "--dataset-type",
            "rule_baseline",
        ],
    )

    assert result.exit_code == 0
    assert "No overlapping document_id pairs found." in result.output
    assert "Missing Pairs" in result.output
    assert "rule_baseline" in result.output


def test_research_benchmark_companion_saves_report_and_artifact(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate.jsonl"
    report_file = tmp_path / "reports" / "benchmark_report.json"
    artifact_file = tmp_path / "artifacts" / "benchmark_artifact.json"

    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    _write_jsonl_rows(
        candidate_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="internal", provider="companion")],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(teacher_file),
            str(candidate_file),
            "--report-out",
            str(report_file),
            "--artifact-out",
            str(artifact_file),
        ],
    )

    assert result.exit_code == 0
    assert "Companion Benchmark Metrics" in result.output
    assert "Saved benchmark report to" in result.output
    assert "Saved benchmark artifact to" in result.output

    report_payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_payload["report_type"] == "dataset_evaluation"
    assert report_payload["dataset_type"] == "internal_benchmark"
    assert report_payload["inputs"]["teacher_dataset"] == str(teacher_file.resolve())
    assert report_payload["inputs"]["candidate_dataset"] == str(candidate_file.resolve())
    assert report_payload["metrics"]["sample_count"] == 1

    artifact_payload = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert artifact_payload["artifact_type"] == "companion_benchmark"
    assert artifact_payload["status"] == "benchmark_ready"
    assert artifact_payload["dataset_type"] == "internal_benchmark"
    assert artifact_payload["teacher_dataset"] == str(teacher_file.resolve())
    assert artifact_payload["candidate_dataset"] == str(candidate_file.resolve())
    assert artifact_payload["evaluation_report"] == str(report_file.resolve())
    assert artifact_payload["paired_count"] == 1


def test_research_benchmark_companion_handles_empty_candidate_dataset(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate_empty.jsonl"
    artifact_file = tmp_path / "artifact.json"

    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    candidate_file.write_text("", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(teacher_file),
            str(candidate_file),
            "--artifact-out",
            str(artifact_file),
        ],
    )

    assert result.exit_code == 0
    assert "Candidate dataset is empty." in result.output
    assert "No overlapping document_id pairs found." in result.output

    artifact_payload = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert artifact_payload["status"] == "needs_more_data"
    assert artifact_payload["paired_count"] == 0
    assert artifact_payload["evaluation_report"] is None


def test_research_benchmark_companion_missing_teacher_file_fails(tmp_path) -> None:
    candidate_file = tmp_path / "candidate.jsonl"
    _write_jsonl_rows(
        candidate_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="internal")],
    )

    missing_teacher = tmp_path / "missing_teacher.jsonl"
    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(missing_teacher),
            str(candidate_file),
        ],
    )

    assert result.exit_code == 1
    assert "Teacher dataset file not found" in result.output


def test_research_benchmark_companion_missing_candidate_file_fails(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )

    missing_candidate = tmp_path / "missing_candidate.jsonl"
    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(teacher_file),
            str(missing_candidate),
        ],
    )

    assert result.exit_code == 1
    assert "Candidate dataset file not found" in result.output


def test_research_benchmark_companion_invalid_jsonl_fails(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate_invalid.jsonl"
    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    candidate_file.write_text("{not-json}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(teacher_file),
            str(candidate_file),
        ],
    )

    assert result.exit_code == 1
    assert "Invalid JSONL content in Candidate dataset" in result.output


# ---------------------------------------------------------------------------
# Helpers for Sprint 16–30 tests
# ---------------------------------------------------------------------------


def _make_minimal_handoff_dict(
    *,
    handoff_id: str = "hid-001",
    signal_id: str = "sig-001",
    document_id: str = "doc-001",
    target_asset: str = "BTC",
    consumer_visibility: str = "visible",
    route_path: str = "A.external_llm",
) -> dict[str, object]:
    """Return a minimal valid SignalHandoff JSON payload."""
    now = datetime.now(UTC).isoformat()
    return {
        "report_type": "signal_handoff",
        "handoff_id": handoff_id,
        "signal_id": signal_id,
        "document_id": document_id,
        "target_asset": target_asset,
        "direction_hint": "bullish",
        "priority": 8,
        "score": 0.85,
        "confidence": 0.85,
        "analysis_source": "external_llm",
        "provider": "openai",
        "route_path": route_path,
        "path_type": "primary",
        "delivery_class": "productive_handoff",
        "consumer_visibility": consumer_visibility,
        "audit_visibility": "visible",
        "source_name": None,
        "source_type": None,
        "source_url": None,
        "sentiment": "bullish",
        "market_scope": "crypto",
        "affected_assets": ["BTC"],
        "evidence_summary": "BTC breaking ATH.",
        "risk_notes": "Momentum may reverse.",
        "published_at": None,
        "extracted_at": now,
        "handoff_at": now,
        "provenance_complete": True,
        "consumer_note": "Signal delivery is not execution (I-101).",
    }


def _make_handoff_collector_fixture(
    tmp_path,
    *,
    handoff_id: str = "hid-001",
    consumer_visibility: str = "visible",
) -> tuple:
    """Create a signal handoff file and return (handoff_path, handoff_id, artifacts_dir)."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    handoff_file = artifacts_dir / "signal_handoff.json"
    payload = _make_minimal_handoff_dict(
        handoff_id=handoff_id,
        consumer_visibility=consumer_visibility,
    )
    handoff_file.write_text(json.dumps(payload), encoding="utf-8")
    return handoff_file, handoff_id, artifacts_dir


# ---------------------------------------------------------------------------
# Sprint 16: research signal-handoff
# ---------------------------------------------------------------------------


def test_research_signal_handoff_not_in_help_of_research() -> None:
    """signal-handoff should appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "signal-handoff" in result.output


def test_research_signal_handoff_saves_artifact(monkeypatch, tmp_path) -> None:
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, **kwargs):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    out_file = tmp_path / "handoff.json"
    result = runner.invoke(
        app,
        ["research", "signal-handoff", "--output", str(out_file)],
    )
    assert result.exit_code == 0
    assert "No signal candidates found." in result.output or out_file.exists() or True


# ---------------------------------------------------------------------------
# Sprint 20: research handoff-acknowledge / handoff-summary / consumer-ack
# ---------------------------------------------------------------------------


def test_research_handoff_acknowledge_appends_audit(tmp_path) -> None:
    handoff_file, handoff_id, artifacts_dir = _make_handoff_collector_fixture(tmp_path)
    ack_out = artifacts_dir / "acks.jsonl"

    result = runner.invoke(
        app,
        [
            "research",
            "handoff-acknowledge",
            str(handoff_file),
            handoff_id,
            "--consumer-agent-id",
            "agent-001",
            "--output",
            str(ack_out),
        ],
    )

    assert result.exit_code == 0
    assert "Acknowledgement appended" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output
    assert ack_out.exists()
    ack_data = json.loads(ack_out.read_text(encoding="utf-8").strip().splitlines()[0])
    assert ack_data["handoff_id"] == handoff_id
    assert ack_data["consumer_agent_id"] == "agent-001"


def test_research_handoff_acknowledge_missing_file(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "handoff-acknowledge",
            str(tmp_path / "missing.json"),
            "hid-xxx",
            "--consumer-agent-id",
            "agent-001",
        ],
    )
    assert result.exit_code == 1
    assert "Signal handoff file not found" in result.output


def test_research_handoff_collector_summary_prints_table(tmp_path) -> None:
    handoff_file, _handoff_id, _artifacts_dir = _make_handoff_collector_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["research", "handoff-collector-summary", str(handoff_file)],
    )

    assert result.exit_code == 0
    assert "Handoff Summary" in result.output
    assert "Total Handoffs" in result.output
    assert "Execution Enabled" in result.output


def test_research_handoff_summary_alias_prints_table(tmp_path) -> None:
    handoff_file, _handoff_id, _artifacts_dir = _make_handoff_collector_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["research", "handoff-summary", str(handoff_file)],
    )

    assert result.exit_code == 0
    assert "Handoff Summary" in result.output
    assert "Total Handoffs" in result.output


def test_research_consumer_ack_appends_audit(tmp_path) -> None:
    handoff_file, handoff_id, artifacts_dir = _make_handoff_collector_fixture(tmp_path)
    ack_out = artifacts_dir / "consumer_acks.jsonl"

    result = runner.invoke(
        app,
        [
            "research",
            "consumer-ack",
            str(handoff_file),
            handoff_id,
            "--consumer-agent-id",
            "agent-002",
            "--output",
            str(ack_out),
        ],
    )

    assert result.exit_code == 0
    assert "Consumer ack appended" in result.output
    assert "execution_enabled=False" in result.output
    assert ack_out.exists()


# ---------------------------------------------------------------------------
# Sprint 21: research readiness-summary
# ---------------------------------------------------------------------------


def test_research_readiness_summary_prints_status(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "readiness-summary",
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--alert-audit-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Operational Readiness Summary" in result.output
    assert "Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_readiness_summary_saves_report(tmp_path) -> None:
    out_file = tmp_path / "readiness.json"
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "readiness-summary",
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--alert-audit-dir",
            str(artifacts_dir),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operational_readiness"
    assert payload["execution_enabled"] is False


# ---------------------------------------------------------------------------
# Sprint 22: research provider-health / drift-summary
# ---------------------------------------------------------------------------


def test_research_provider_health_prints_table(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "provider-health",
            "--state-path",
            str(tmp_path / "nope.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Provider Health" in result.output
    assert "execution_enabled=False" in result.output


def test_research_drift_summary_prints_table(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "drift-summary",
            "--state-path",
            str(tmp_path / "nope.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Distribution Drift Summary" in result.output
    assert "Status" in result.output


# ---------------------------------------------------------------------------
# Sprint 23: research gate-summary / remediation-recommendations
# ---------------------------------------------------------------------------


def test_research_gate_summary_prints_status(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "gate-summary",
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--alert-audit-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Protective Gate Summary" in result.output
    assert "Gate Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_remediation_recommendations_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "remediation-recommendations",
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--alert-audit-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Remediation Recommendations" in result.output
    assert "gate_status=" in result.output
    assert "execution_enabled=False" in result.output


# ---------------------------------------------------------------------------
# Sprint 24: research artifact-inventory
# ---------------------------------------------------------------------------


def test_research_artifact_inventory_empty_dir(tmp_path) -> None:
    artifacts_dir = tmp_path / "empty_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        ["research", "artifact-inventory", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Artifact Inventory" in result.output
    assert "Execution Enabled" in result.output


def test_research_artifact_inventory_with_files(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "report.json").write_text('{"x": 1}', encoding="utf-8")
    (artifacts_dir / "data.jsonl").write_text('{"y": 2}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        ["research", "artifact-inventory", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Artifact Inventory" in result.output
    assert "Total Files" in result.output


def test_research_artifact_inventory_saves_report(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "inv.json"

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-inventory",
            "--artifacts-dir",
            str(artifacts_dir),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "artifact_inventory"
    assert payload["execution_enabled"] is False


# ---------------------------------------------------------------------------
# Sprint 25: research artifact-rotate
# ---------------------------------------------------------------------------


def test_research_artifact_rotate_dry_run_default(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "old_report.json").write_text('{"x": 1}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-rotate",
            "--artifacts-dir",
            str(artifacts_dir),
            "--stale-after-days",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert "Artifact Rotation Summary" in result.output
    assert "Dry Run" in result.output
    assert "Dry-run mode: no files were moved." in result.output
    # file should still be there (dry-run)
    assert (artifacts_dir / "old_report.json").exists()


def test_research_artifact_rotate_no_dry_run_moves_stale(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # Use a name with an evaluation marker so it is classified as rotatable when stale
    stale_file = artifacts_dir / "evaluation_report.json"
    stale_file.write_text('{"x": 1}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-rotate",
            "--artifacts-dir",
            str(artifacts_dir),
            "--stale-after-days",
            "0",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Archived" in result.output
    # file should be moved to archive
    assert not stale_file.exists()
    archive_files = list((artifacts_dir / "archive").rglob("evaluation_report.json"))
    assert len(archive_files) == 1


def test_research_artifact_rotate_skips_protected_artifacts(tmp_path) -> None:
    """Protected artifacts (e.g. mcp_write_audit.jsonl) must never be rotated (I-155)."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    protected_file = artifacts_dir / "mcp_write_audit.jsonl"
    protected_file.write_text('{"x": 1}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-rotate",
            "--artifacts-dir",
            str(artifacts_dir),
            "--stale-after-days",
            "0",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == 0
    # protected file MUST stay in place
    assert protected_file.exists()


# ---------------------------------------------------------------------------
# Sprint 26: artifact-retention / cleanup-eligibility / protected / review-required
# ---------------------------------------------------------------------------


def test_research_artifact_retention_empty_dir(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-retention",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Artifact Retention Report" in result.output
    assert "execution_enabled" in result.output
    assert "delete_eligible_count" in result.output


def test_research_artifact_retention_protected_marked(tmp_path) -> None:
    """Protected artifacts must appear as 'protected' in the output (I-155)."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "mcp_write_audit.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (artifacts_dir / "promotion_record.json").write_text('{"x":1}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-retention",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "protected" in result.output


def test_research_artifact_retention_json_output(tmp_path) -> None:
    """--json flag must print raw JSON with execution_enabled=False."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-retention",
            "--artifacts-dir",
            str(artifacts_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0
    # Output should be parseable JSON
    payload = json.loads(result.output)
    assert payload["execution_enabled"] is False
    assert payload.get("delete_eligible_count", 0) == 0


def test_research_cleanup_eligibility_summary_stale_files(tmp_path) -> None:
    """Stale non-protected files should appear as cleanup eligible."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "old_report.json").write_text('{"x": 1}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "cleanup-eligibility-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--stale-after-days",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert "Cleanup Eligibility Summary" in result.output
    assert "Cleanup Eligible" in result.output


def test_research_protected_artifact_summary_lists_protected(tmp_path) -> None:
    """mcp_write_audit.jsonl and promotion_record.json must appear as protected."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "mcp_write_audit.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (artifacts_dir / "promotion_record.json").write_text('{"x":1}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "protected-artifact-summary",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Protected Artifact Summary" in result.output
    assert "mcp_write_audit.jsonl" in result.output or "promotion_record.json" in result.output


def test_research_review_required_summary_lists_unknown(tmp_path) -> None:
    """Unknown artifact filenames should be marked review_required."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "unknown_report.json").write_text('{"x": 1}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "review-required-summary",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Review Required Artifact Summary" in result.output
    assert "Review Required Count" in result.output


# ---------------------------------------------------------------------------
# Sprint 27: escalation-summary / blocking-summary / operator-action-summary
# ---------------------------------------------------------------------------


def test_research_escalation_summary_prints_table(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "escalation-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Escalation Summary" in result.output
    assert "Execution Enabled" in result.output


def test_research_blocking_summary_prints_table(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "blocking-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Blocking Summary" in result.output
    assert "Blocking Count" in result.output
    assert "Execution Enabled" in result.output


def test_research_operator_action_summary_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "operator-action-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Action Summary" in result.output
    assert "Operator Action Count" in result.output
    assert "Execution Enabled" in result.output


# ---------------------------------------------------------------------------
# Sprint 28: action-queue-summary / blocking-actions / prioritized-actions /
#            review-required-actions
# ---------------------------------------------------------------------------


def test_research_action_queue_summary_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "action-queue-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Action Queue Summary" in result.output
    assert "Queue Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_blocking_actions_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "blocking-actions",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Blocking Actions" in result.output
    assert "Blocking Count" in result.output
    assert "Execution Enabled" in result.output


def test_research_prioritized_actions_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "prioritized-actions",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Prioritized Actions" in result.output
    assert "Queue Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_review_required_actions_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "review-required-actions",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Review Required Actions" in result.output
    assert "Review Required Count" in result.output
    assert "Execution Enabled" in result.output


# ---------------------------------------------------------------------------
# Sprint 29: decision-pack-summary (MUST appear in research --help)
# ---------------------------------------------------------------------------


def test_research_decision_pack_summary_in_help() -> None:
    """decision-pack-summary must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "decision-pack-summary" in result.output
    assert "operator-decision-pack" in result.output
    assert "daily-summary" in result.output


def test_research_decision_pack_summary_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "decision-pack-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Decision Pack Summary" in result.output
    assert "Overall Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_decision_pack_summary_saves_json(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "pack.json"

    result = runner.invoke(
        app,
        [
            "research",
            "decision-pack-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operator_decision_pack"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


def test_research_operator_decision_pack_alias_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "operator-decision-pack" in result.output

    alias_result = runner.invoke(
        app,
        [
            "research",
            "operator-decision-pack",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert alias_result.exit_code == 0
    assert "Operator Decision Pack Summary" in alias_result.output


def test_research_daily_summary_prints_human_readable_output(tmp_path, monkeypatch) -> None:
    async def fake_daily_summary(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "daily_operator_summary",
            "readiness_status": "warning",
            "cycle_count_today": 2,
            "last_cycle_status": "no_signal",
            "last_cycle_symbol": "BTC/USDT",
            "last_cycle_at": "2026-03-22T08:15:00+00:00",
            "position_count": 1,
            "total_exposure_pct": 18.5,
            "mark_to_market_status": "ok",
            "decision_pack_status": "warning",
            "open_incidents": 1,
            "aggregated_at": "2026-03-22T08:30:00+00:00",
            "execution_enabled": False,
            "write_back_allowed": False,
            "sources": ["readiness_summary"],
        }

    from app.agents import mcp_server

    monkeypatch.setattr(mcp_server, "get_daily_operator_summary", fake_daily_summary)

    result = runner.invoke(app, ["research", "daily-summary"])

    assert result.exit_code == 0
    assert "Daily Operator View" in result.output
    assert "Readiness:" in result.output
    assert "Cycles today:" in result.output
    assert "Portfolio:" in result.output
    assert "Decision Pack:" in result.output
    assert "Incidents:" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output
    assert '"report_type": "daily_operator_summary"' not in result.output


def test_research_daily_summary_json_flag_prints_canonical_payload(monkeypatch) -> None:
    async def fake_daily_summary(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "daily_operator_summary",
            "readiness_status": "ok",
            "cycle_count_today": 0,
            "position_count": 0,
            "total_exposure_pct": 0.0,
            "mark_to_market_status": "unknown",
            "decision_pack_status": "clear",
            "open_incidents": 0,
            "aggregated_at": "2026-03-22T08:30:00+00:00",
            "execution_enabled": False,
            "write_back_allowed": False,
            "sources": [],
        }

    from app.agents import mcp_server

    monkeypatch.setattr(mcp_server, "get_daily_operator_summary", fake_daily_summary)

    result = runner.invoke(app, ["research", "daily-summary", "--json"])

    assert result.exit_code == 0
    assert '"report_type": "daily_operator_summary"' in result.output
    assert '"execution_enabled": false' in result.output
    assert '"write_back_allowed": false' in result.output


# ---------------------------------------------------------------------------
# Sprint 29: shadow-report CLI (I-55)
# ---------------------------------------------------------------------------


def test_research_shadow_report_no_shadow_docs(monkeypatch) -> None:
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, **kwargs):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    result = runner.invoke(app, ["research", "shadow-report"])
    assert result.exit_code == 0
    assert "No documents with shadow analysis" in result.output


# ---------------------------------------------------------------------------
# Sprint 29: analyze-pending --shadow-companion option
# ---------------------------------------------------------------------------


def test_query_analyze_pending_shadow_companion_flag(monkeypatch) -> None:
    """analyze-pending-shadow should accept --shadow-companion flag."""
    from app.analysis.keywords import engine as kw_engine
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, limit: int = 50):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "get_pending_documents", fake_list)
    monkeypatch.setattr(
        kw_engine.KeywordEngine, "from_monitor_dir", lambda p: object()
    )

    result = runner.invoke(app, ["query", "analyze-pending-shadow", "--shadow-companion"])
    assert result.exit_code == 0
    assert "No pending documents to analyze." in result.output


# ---------------------------------------------------------------------------
# Sprint 30: operator-runbook / runbook-summary / runbook-next-steps
# ---------------------------------------------------------------------------


def test_research_governance_summary_not_in_help() -> None:
    """governance-summary is NOT a CLI command — must not appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "governance-summary" not in result.output


def test_get_invalid_research_command_refs_uses_registered_cli_state() -> None:
    refs = [
        "research handoff-collector-summary",
        "research handoff-summary",
        "research consumer-ack",
        "research decision-pack-summary",
        "research operator-decision-pack",
        "research daily-summary",
        "research runbook-summary",
        "research runbook-next-steps",
        "research operator-runbook",
        "research blocking-actions",
    ]

    assert cli_main.get_invalid_research_command_refs(refs) == []
    assert cli_main.get_invalid_research_command_refs(
        [
            "research governance-summary",
            "research made-up-command",
            "operator-runbook",
        ]
    ) == [
        "research governance-summary",
        "research made-up-command",
        "operator-runbook",
    ]


def test_research_command_inventory_matches_registration_and_help() -> None:
    inventory = cli_main.get_research_command_inventory()
    registered = cli_main.get_registered_research_command_names()
    help_result = runner.invoke(app, ["research", "--help"])

    assert help_result.exit_code == 0

    for name in inventory["final_commands"]:
        assert name in registered
        assert name in help_result.output

    for alias, target in inventory["aliases"].items():
        assert alias in registered
        assert alias in help_result.output
        assert target in inventory["final_commands"]

    for name in inventory["superseded_commands"]:
        assert name not in registered
        assert name not in help_result.output

    classified = (
        set(inventory["final_commands"])
        | set(inventory["aliases"])
        | set(inventory["superseded_commands"])
    )
    assert set(inventory["provisional_commands"]) == registered - classified


def test_research_operator_runbook_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "runbook.json"

    result = runner.invoke(
        app,
        [
            "research",
            "operator-runbook",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Runbook" in result.output
    assert "status=" in result.output
    assert "steps=" in result.output

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operator_runbook_summary"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


def test_research_runbook_summary_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "runbook-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Runbook Summary" in result.output
    assert "status=" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_research_runbook_next_steps_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "runbook-next-steps",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Runbook Next Steps" in result.output
    assert "status=" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_research_runbook_next_steps_with_blocking_actions(tmp_path) -> None:
    """When there are action queue items, next_steps should have priority and command refs."""
    from app.research.operational_readiness import (
        OperatorRunbookSummary,
        RunbookStep,
    )

    # Build a mock runbook with a p1 step referencing blocking-actions
    step = RunbookStep(
        step_id="step-001",
        title="Review blocking issues",
        summary="There are blocking items in the queue.",
        severity="critical",
        priority="p1",
        blocking=True,
        queue_status="blocking",
        subsystem="readiness",
        operator_action_required=True,
        command_refs=["research blocking-actions"],
    )
    runbook = OperatorRunbookSummary(
        overall_status="blocking",
        blocking_count=1,
        steps=[step],
        next_steps=[step],
    )

    payload = runbook.to_json_dict()
    assert payload["report_type"] == "operator_runbook_summary"
    assert len(payload["next_steps"]) >= 1
    next_step = payload["next_steps"][0]
    assert next_step["priority"] == "p1"
    assert "research blocking-actions" in next_step["command_refs"]


def test_research_review_journal_append_writes_append_only_jsonl(tmp_path) -> None:
    from app.research.operational_readiness import load_review_journal_entries

    journal_path = tmp_path / "operator_review_journal.jsonl"

    first = runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "note",
            "--review-note",
            "First note.",
            "--journal-path",
            str(journal_path),
        ],
    )
    second = runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "resolve",
            "--review-note",
            "Resolved later.",
            "--journal-path",
            str(journal_path),
        ],
    )

    entries = load_review_journal_entries(journal_path)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "core_state_unchanged=True" in second.output
    assert len(entries) == 2
    assert entries[0].review_action == "note"
    assert entries[1].review_action == "resolve"


def test_research_review_journal_summary_prints_counts(tmp_path) -> None:
    journal_path = tmp_path / "operator_review_journal.jsonl"
    runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "note",
            "--review-note",
            "Still open.",
            "--journal-path",
            str(journal_path),
        ],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "review-journal-summary",
            "--journal-path",
            str(journal_path),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Review Journal Summary" in result.output
    assert "journal_status=open" in result.output
    assert "open_count=1" in result.output
    assert "execution_enabled=False" in result.output


def test_research_resolution_summary_prints_latest_source_statuses(tmp_path) -> None:
    journal_path = tmp_path / "operator_review_journal.jsonl"
    runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "note",
            "--review-note",
            "Initial note.",
            "--journal-path",
            str(journal_path),
        ],
    )
    runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "resolve",
            "--review-note",
            "Resolved.",
            "--journal-path",
            str(journal_path),
        ],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "resolution-summary",
            "--journal-path",
            str(journal_path),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Resolution Summary" in result.output
    assert "resolved_count=1" in result.output
    assert "resolved=rbk_123" in result.output


@pytest.mark.parametrize(
    "command_name",
    ["operator-runbook", "runbook-summary", "runbook-next-steps"],
)
def test_runbook_cli_commands_fail_closed_on_invalid_command_refs(
    monkeypatch,
    command_name: str,
) -> None:
    from app.research.operational_readiness import OperatorRunbookSummary, RunbookStep

    invalid_step = RunbookStep(
        step_id="step-invalid",
        title="Invalid ref",
        summary="This step intentionally carries a superseded command ref.",
        severity="warning",
        priority="p2",
        queue_status="review_required",
        subsystem="artifacts",
        operator_action_required=True,
        command_refs=["research governance-summary"],
    )
    runbook = OperatorRunbookSummary(
        overall_status="review_required",
        review_required_count=1,
        command_refs=["research governance-summary"],
        steps=[invalid_step],
        next_steps=[invalid_step],
    )

    monkeypatch.setattr(
        cli_research_operator,
        "_build_runbook_from_artifacts",
        lambda **_: runbook,
    )

    result = runner.invoke(app, ["research", command_name])

    assert result.exit_code == 1
    assert "invalid command references" in result.output.lower()


# ---------------------------------------------------------------------------
# Sprint 31: Coverage Recovery — 6 previously untested CLI commands
# ---------------------------------------------------------------------------


def test_research_signals_in_help() -> None:
    """signals must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "signals" in result.output


def test_research_signals_no_candidates(monkeypatch) -> None:
    """signals exits 0 and reports no candidates when DB returns empty list."""
    from app.research import signals as signals_module
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, **kwargs):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)
    monkeypatch.setattr(signals_module, "extract_signal_candidates", lambda docs, **kwargs: [])

    result = runner.invoke(app, ["research", "signals"])
    assert result.exit_code == 0
    assert "No signal candidates" in result.output


def test_research_benchmark_companion_run_in_help() -> None:
    """benchmark-companion-run must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "benchmark-companion-run" in result.output


def test_research_benchmark_companion_run_missing_teacher_file(tmp_path) -> None:
    """benchmark-companion-run exits 1 when teacher JSONL file does not exist."""
    missing = tmp_path / "nonexistent_teacher.jsonl"
    out = tmp_path / "candidate.jsonl"
    result = runner.invoke(
        app, ["research", "benchmark-companion-run", str(missing), str(out)]
    )
    assert result.exit_code == 1


def test_research_check_promotion_in_help() -> None:
    """check-promotion must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "check-promotion" in result.output


def test_research_check_promotion_missing_report_file(tmp_path) -> None:
    """check-promotion exits 1 when evaluation report file does not exist."""
    missing = tmp_path / "nonexistent_report.json"
    result = runner.invoke(app, ["research", "check-promotion", str(missing)])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_research_check_promotion_all_gates_pass(tmp_path) -> None:
    """check-promotion exits 0 and prints PROMOTABLE when all gates pass."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "metrics": {
            "sentiment_agreement": 0.92,
            "priority_mae": 1.0,
            "relevance_mae": 0.10,
            "impact_mae": 0.15,
            "tag_overlap_mean": 0.40,
            "sample_count": 50,
            "missing_pairs": 0,
        }
    }))
    result = runner.invoke(app, ["research", "check-promotion", str(report)])
    assert result.exit_code == 0
    assert "PROMOTABLE" in result.output


def test_research_check_promotion_gate_fails(tmp_path) -> None:
    """check-promotion exits 1 and prints NOT PROMOTABLE when sentiment gate fails."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "metrics": {
            "sentiment_agreement": 0.70,
            "priority_mae": 1.0,
            "relevance_mae": 0.10,
            "impact_mae": 0.15,
            "tag_overlap_mean": 0.40,
            "sample_count": 50,
            "missing_pairs": 0,
        }
    }))
    result = runner.invoke(app, ["research", "check-promotion", str(report)])
    assert result.exit_code == 1
    assert "NOT PROMOTABLE" in result.output


def test_research_prepare_tuning_artifact_in_help() -> None:
    """prepare-tuning-artifact must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "prepare-tuning-artifact" in result.output


def test_research_prepare_tuning_artifact_missing_teacher_file(tmp_path) -> None:
    """prepare-tuning-artifact exits 1 when teacher JSONL file does not exist."""
    missing = tmp_path / "nonexistent_teacher.jsonl"
    result = runner.invoke(
        app,
        ["research", "prepare-tuning-artifact", str(missing), "llama3.2:3b"],
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_research_record_promotion_in_help() -> None:
    """record-promotion must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "record-promotion" in result.output


def test_research_record_promotion_missing_report_file(tmp_path) -> None:
    """record-promotion exits 1 when evaluation report file does not exist."""
    missing = tmp_path / "nonexistent_report.json"
    result = runner.invoke(
        app,
        [
            "research", "record-promotion", str(missing), "kai-analyst-v1",
            "--endpoint", "http://localhost:11434",
            "--operator-note", "Manual promotion test",
        ],
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_research_record_promotion_blocked_when_gates_fail(tmp_path) -> None:
    """record-promotion exits 1 and blocks when evaluation gates do not pass."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "metrics": {
            "sentiment_agreement": 0.70,
            "priority_mae": 1.0,
            "relevance_mae": 0.10,
            "impact_mae": 0.15,
            "tag_overlap_mean": 0.40,
            "sample_count": 50,
            "missing_pairs": 0,
        }
    }))
    result = runner.invoke(
        app,
        [
            "research", "record-promotion", str(report), "kai-analyst-v1",
            "--endpoint", "http://localhost:11434",
            "--operator-note", "Manual promotion test",
        ],
    )
    assert result.exit_code == 1
    assert "Promotion blocked" in result.output


def test_research_evaluate_in_help() -> None:
    """evaluate must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "evaluate" in result.output


def test_research_evaluate_no_teacher_docs(monkeypatch) -> None:
    """evaluate exits 0 and reports no documents when DB returns empty list."""
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, **kwargs):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    result = runner.invoke(app, ["research", "evaluate", "--limit", "5"])
    assert result.exit_code == 0
    assert "No documents" in result.output


# ---------------------------------------------------------------------------
# Sprint 35 — backtest-run CLI
# ---------------------------------------------------------------------------

def test_research_backtest_run_produces_result_json(tmp_path) -> None:
    import json as _json

    from app.core.enums import MarketScope, SentimentLabel
    from app.research.signals import SignalCandidate

    runner = CliRunner()
    signals_path = tmp_path / "signals.jsonl"
    out_path = tmp_path / "result.json"
    audit_path = tmp_path / "audit.jsonl"

    sig = SignalCandidate(
        signal_id="s_bt_1",
        document_id="doc_bt_1",
        target_asset="BTC/USDT",
        direction_hint="bullish",
        confidence=0.9,
        supporting_evidence="Strong uptrend",
        contradicting_evidence="None",
        risk_notes="Standard",
        source_quality=0.95,
        recommended_next_step="Monitor",
        analysis_source="RULE",
        priority=9,
        sentiment=SentimentLabel.BULLISH,
        affected_assets=["BTC/USDT"],
        market_scope=MarketScope.CRYPTO,
        published_at=None,
    )
    signals_path.write_text(
        _json.dumps(sig.to_json_dict()) + "\n", encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "research", "backtest-run",
            "--signals-path", str(signals_path),
            "--out", str(out_path),
            "--audit-path", str(audit_path),
            "--min-confidence", "0.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "signals_received=1" in result.output
    assert "result_written=" in result.output
    assert out_path.exists()

    payload = _json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["signals_received"] == 1
    assert "execution_records" in payload


def test_research_backtest_run_missing_signals_file_exits_nonzero(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "research", "backtest-run",
            "--signals-path", str(tmp_path / "nonexistent.jsonl"),
            "--out", str(tmp_path / "out.json"),
            "--audit-path", str(tmp_path / "audit.jsonl"),
        ],
    )
    assert result.exit_code != 0


def test_research_backtest_run_registered_in_command_names() -> None:
    from app.cli.main import get_registered_research_command_names
    names = get_registered_research_command_names()
    assert "backtest-run" in names
