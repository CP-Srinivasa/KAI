from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import AnalysisSource, DocumentStatus, MarketScope, SentimentLabel
from app.storage.db.session import Base
from app.storage.repositories.document_repo import DocumentRepository


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_save_sets_persisted_status(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-1",
                title="Bitcoin jumps",
            )
        )

    assert saved.status == DocumentStatus.PERSISTED
    assert saved.is_duplicate is False
    assert saved.is_analyzed is False

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.PERSISTED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is False


@pytest.mark.asyncio
async def test_mark_duplicate_sets_duplicate_status(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-2",
                title="Ethereum jumps",
            )
        )
        await repo.mark_duplicate(str(saved.id))

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.DUPLICATE
    assert stored.is_duplicate is True
    assert stored.is_analyzed is False


@pytest.mark.asyncio
async def test_mark_analyzed_sets_analyzed_status(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-3",
                title="Solana jumps",
            )
        )
        await repo.mark_analyzed(str(saved.id))

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.ANALYZED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is True


@pytest.mark.asyncio
async def test_get_pending_documents_returns_only_persisted_docs(session_factory) -> None:
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        pending = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-pending",
                title="Pending article",
            )
        )
        duplicate = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-duplicate",
                title="Duplicate article",
            )
        )
        failed = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-failed",
                title="Failed article",
            )
        )
        analyzed = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-analyzed",
                title="Analyzed article",
            )
        )
        await repo.mark_duplicate(str(duplicate.id))
        await repo.mark_failed(str(failed.id))
        await repo.mark_analyzed(str(analyzed.id))

    async with session_factory() as session:
        repo = DocumentRepository(session)
        docs = await repo.get_pending_documents(limit=10)

    assert [str(doc.id) for doc in docs] == [str(pending.id)]
    assert docs[0].status == DocumentStatus.PERSISTED
    assert docs[0].is_duplicate is False
    assert docs[0].is_analyzed is False


@pytest.mark.asyncio
async def test_update_analysis_sets_analyzed_status(session_factory) -> None:
    published_at = datetime.now(UTC)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(
                url="https://example.com/article-4",
                title="Macro update",
                published_at=published_at,
            )
        )
        analysis_result = AnalysisResult(
            document_id=str(saved.id),
            analysis_source=AnalysisSource.INTERNAL,
            sentiment_label=SentimentLabel.BULLISH,
            sentiment_score=0.7,
            relevance_score=0.8,
            impact_score=0.6,
            novelty_score=0.5,
            confidence_score=0.9,
            spam_probability=0.1,
            recommended_priority=7,
            market_scope=MarketScope.UNKNOWN,
            explanation_short="Test",
            explanation_long="Test long",
            tags=["macro"],
            affected_sectors=["defi", "layer1"],
        )
        await repo.update_analysis(
            str(saved.id),
            analysis_result,
            provider_name="shadow",
            metadata_updates={"ensemble_chain": ["openai", "shadow"]},
        )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        stored = await repo.get_by_id(str(saved.id))

    assert stored is not None
    assert stored.status == DocumentStatus.ANALYZED
    assert stored.is_duplicate is False
    assert stored.is_analyzed is True
    assert stored.provider == "shadow"
    assert stored.analysis_source == AnalysisSource.INTERNAL
    assert stored.effective_analysis_source == AnalysisSource.INTERNAL
    assert stored.metadata["ensemble_chain"] == ["openai", "shadow"]
    assert stored.sentiment_label == SentimentLabel.BULLISH
    assert stored.priority_score == analysis_result.recommended_priority
    assert stored.categories == ["defi", "layer1"]


@pytest.mark.asyncio
async def test_source_activity_aggregates_per_source(session_factory) -> None:
    from datetime import timedelta

    from app.storage.models.document import CanonicalDocumentModel

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
    rows = [
        ("rss", now - timedelta(hours=1)),
        ("rss", now - timedelta(hours=2)),
        ("rss", now - timedelta(hours=50)),  # outside the 24h window
        ("okx", now - timedelta(hours=3)),
        (None, now - timedelta(hours=4)),  # null source → "unknown"
    ]
    async with session_factory.begin() as session:
        for i, (src, fetched) in enumerate(rows):
            session.add(
                CanonicalDocumentModel(
                    id=f"doc-{i}",
                    url=f"https://example.com/{i}",
                    title=f"t{i}",
                    document_type="news",
                    source_name=src,
                    fetched_at=fetched,
                )
            )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        result = await repo.source_activity(window_hours=24, now=now)

    by = {r.source_name: r for r in result}
    assert by["rss"].total == 3 and by["rss"].window_count == 2  # 50h-old excluded
    assert by["okx"].total == 1 and by["okx"].window_count == 1
    assert by["unknown"].total == 1  # null source coalesced
    assert by["rss"].last_fetched_at is not None
    assert by["rss"].silent is False  # within the 7d silence threshold
    # newest source first: rss (last fetch 1h ago) before okx (3h ago)
    assert result[0].source_name == "rss"


@pytest.mark.asyncio
async def test_source_activity_silent_flag(session_factory) -> None:
    from datetime import timedelta

    from app.storage.models.document import CanonicalDocumentModel

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
    rows = [
        ("fresh", now - timedelta(hours=2)),  # recent → not silent
        ("dead", now - timedelta(hours=200)),  # > 168h → silent
    ]
    async with session_factory.begin() as session:
        for i, (src, fetched) in enumerate(rows):
            session.add(
                CanonicalDocumentModel(
                    id=f"sil-{i}",
                    url=f"https://example.com/sil/{i}",
                    title=f"t{i}",
                    document_type="news",
                    source_name=src,
                    fetched_at=fetched,
                )
            )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        result = await repo.source_activity(silent_after_hours=168, now=now)

    by = {r.source_name: r for r in result}
    assert by["fresh"].silent is False
    assert by["dead"].silent is True  # nothing in 7 days → went quiet


@pytest.mark.asyncio
async def test_source_activity_empty_store(session_factory) -> None:
    async with session_factory() as session:
        repo = DocumentRepository(session)
        assert await repo.source_activity() == []


@pytest.mark.asyncio
async def test_list_directional_news_events_filters_orders_and_windows(session_factory) -> None:
    from app.research.news_outcomes import load_news_events
    from app.storage.models.document import CanonicalDocumentModel

    def _doc(**kw):
        base = {"document_type": "news", "status": "analyzed", "market_scope": "crypto"}
        base.update(kw)
        return CanonicalDocumentModel(**base)

    async with session_factory.begin() as session:
        session.add_all(
            [
                _doc(
                    id="d1",
                    url="u1",
                    title="BTC up",
                    source_name="cointelegraph",
                    sentiment_label="bullish",
                    tickers=["BTC/USDT"],
                    published_at=datetime(2026, 6, 15, tzinfo=UTC),
                    directional_confidence=0.8,
                ),
                _doc(
                    id="d2",
                    url="u2",
                    title="ETH down",
                    source_name="decrypt",
                    sentiment_label="bearish",
                    tickers=["ETH/USDT"],
                    published_at=datetime(2026, 6, 20, tzinfo=UTC),
                    directional_confidence=0.5,
                ),
                _doc(  # excluded: neutral
                    id="d3",
                    url="u3",
                    title="meh",
                    sentiment_label="neutral",
                    tickers=["BTC/USDT"],
                    published_at=datetime(2026, 6, 16, tzinfo=UTC),
                ),
                _doc(  # passes coarse SQL filter, dropped by load_news_events (empty tickers)
                    id="d4",
                    url="u4",
                    title="vague bull",
                    source_name="empty",
                    sentiment_label="bullish",
                    tickers=[],
                    published_at=datetime(2026, 6, 17, tzinfo=UTC),
                ),
            ]
        )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        allrows = await repo.list_directional_news_events(since=None)
        strict = await repo.list_directional_news_events(min_confidence=0.7)
        windowed = await repo.list_directional_news_events(since=datetime(2026, 6, 18, tzinfo=UTC))

    # coarse SQL filter: drops neutral (sentiment), keeps directional; empty-ticker
    # doc slips through here and is dropped by the authority (load_news_events).
    assert all(r["sentiment_label"] != "neutral" for r in allrows)
    assert "cointelegraph" in {r["source_name"] for r in allrows}
    # min_confidence keeps only the 0.8 doc (NULL confidence excluded)
    assert [r["source_name"] for r in strict] == ["cointelegraph"]
    # since-window drops docs before the cutoff
    assert [r["source_name"] for r in windowed] == ["decrypt"]
    # the pure event loader is the authority: only real directional+ticker survive
    events = load_news_events(allrows)
    assert [(e.symbol, e.side) for e in events] == [
        ("BTC/USDT", "long"),
        ("ETH/USDT", "short"),
    ]


# ── Entity-Felder ueberleben den Analyse-Schreibpfad ─────────────────────────
#
# Befund 2026-09-10, gemessen auf kai-pi5 ueber 76.392 Dokumente: `entities`,
# `entity_mentions`, `topics`, `people`, `organizations` und `crypto_assets`
# waren zu exakt 0,00 % belegt, waehrend `tags` (98,42 %), `categories`
# (37,77 %) und `tickers` (39,51 %) normal gefuellt waren. Die Trennlinie lief
# exakt entlang der festen `values`-Liste in `update_analysis`.
#
# Die Extraktion war nie das Problem: die Keyword-Engine gegen 25 echte
# Dokumente ergab 21 Treffer-Dokumente und 70 `topic`-Mentions. `_sync_flat_
# entities` schrieb sie ins Dokument, und das anschliessende UPDATE liess sie
# fallen. Deshalb prueft dieser Test die KETTE, nicht die Einzelfunktion.


def _analyse_ergebnis(document_id: str) -> AnalysisResult:
    return AnalysisResult(
        document_id=document_id,
        analysis_source=AnalysisSource.INTERNAL,
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.7,
        relevance_score=0.8,
        impact_score=0.6,
        novelty_score=0.5,
        confidence_score=0.9,
        spam_probability=0.1,
        recommended_priority=7,
        market_scope=MarketScope.UNKNOWN,
        explanation_short="Test",
        explanation_long="Test long",
        tags=["macro"],
        affected_assets=["BTC"],
        affected_sectors=["defi"],
    )


@pytest.mark.asyncio
async def test_entity_felder_ueberleben_apply_update_und_reload(session_factory) -> None:
    """Die volle Kette: Treffer -> apply_to_document -> UPDATE -> Reload."""
    from app.analysis.keywords.engine import KeywordHit
    from app.analysis.pipeline import PipelineResult
    from app.normalization.entities import hits_to_entity_mentions

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(url="https://example.com/entities-1", title="Bitcoin und Powell")
        )

    # Genau die Treffertypen, die die Engine auf dem Geraet liefert.
    hits = [
        KeywordHit(canonical="halving", category="keyword", occurrences=2),
        KeywordHit(canonical="BTC", category="crypto", occurrences=3),
        KeywordHit(canonical="Jerome Powell", category="person", occurrences=1),
        KeywordHit(canonical="BlackRock", category="organization", occurrences=1),
    ]
    mentions = hits_to_entity_mentions(hits)

    doc = CanonicalDocument(
        id=saved.id,
        url=saved.url,
        title=saved.title,
        tickers=["ETH"],
        categories=["l1"],
        tags=["bestand"],
    )
    ergebnis = _analyse_ergebnis(str(saved.id))
    res = PipelineResult(
        document=doc,
        keyword_hits=hits,
        entity_mentions=mentions,
        analysis_result=ergebnis,
        provider_name="internal",
    )
    res.apply_to_document()

    # Vorbedingung: im Speicher sind die Felder gefuellt. Waere das schon hier
    # leer, pruefte der Test unten nichts.
    assert doc.topics == ["halving"]
    assert doc.crypto_assets == ["BTC"]
    assert doc.people == ["Jerome Powell"]
    assert doc.organizations == ["BlackRock"]
    assert set(doc.entities) == {"Jerome Powell", "BlackRock"}
    assert doc.entity_mentions

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        await repo.update_analysis(str(saved.id), ergebnis, provider_name="internal", document=doc)

    async with session_factory() as session:
        repo = DocumentRepository(session)
        geladen = await repo.get_by_id(str(saved.id))

    assert geladen is not None
    # Der eigentliche Beweis: nach dem Rundweg durch die Datenbank noch da.
    assert geladen.topics == ["halving"]
    assert geladen.crypto_assets == ["BTC"]
    assert geladen.people == ["Jerome Powell"]
    assert geladen.organizations == ["BlackRock"]
    assert set(geladen.entities) == {"Jerome Powell", "BlackRock"}
    assert [m.name for m in geladen.entity_mentions] == [
        "halving",
        "BTC",
        "Jerome Powell",
        "BlackRock",
    ]
    assert geladen.is_analyzed is True


@pytest.mark.asyncio
async def test_ohne_document_bleibt_der_alte_schreibpfad_unveraendert(session_factory) -> None:
    """Der Parameter ist additiv: ein Aufrufer ohne ihn schreibt wie bisher."""
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(url="https://example.com/entities-2", title="Ohne Dokument")
        )

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        await repo.update_analysis(str(saved.id), _analyse_ergebnis(str(saved.id)))

    async with session_factory() as session:
        repo = DocumentRepository(session)
        geladen = await repo.get_by_id(str(saved.id))

    assert geladen is not None
    assert geladen.is_analyzed is True
    assert geladen.topics == []
    assert geladen.entities == []


@pytest.mark.asyncio
async def test_tags_categories_tickers_bleiben_unveraendert(session_factory) -> None:
    """Gegenprobe: der Fix darf die drei bereits funktionierenden Spalten nicht anfassen."""
    from app.analysis.keywords.engine import KeywordHit
    from app.analysis.pipeline import PipelineResult
    from app.normalization.entities import hits_to_entity_mentions

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        saved = await repo.save(
            CanonicalDocument(url="https://example.com/entities-3", title="Unveraendert")
        )

    hits = [KeywordHit(canonical="BTC", category="crypto", occurrences=1)]
    doc = CanonicalDocument(id=saved.id, url=saved.url, title=saved.title)
    ergebnis = _analyse_ergebnis(str(saved.id))
    res = PipelineResult(
        document=doc,
        keyword_hits=hits,
        entity_mentions=hits_to_entity_mentions(hits),
        analysis_result=ergebnis,
        provider_name="internal",
    )
    res.apply_to_document()

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        await repo.update_analysis(str(saved.id), ergebnis, provider_name="internal", document=doc)

    async with session_factory() as session:
        repo = DocumentRepository(session)
        geladen = await repo.get_by_id(str(saved.id))

    assert geladen is not None
    # Weiterhin aus dem AnalysisResult, nicht aus dem Dokument.
    assert geladen.tags == ergebnis.tags
    assert geladen.tickers == ergebnis.affected_assets
    assert geladen.categories == ergebnis.affected_sectors
    # Und die neue Spalte danebengeschrieben, ohne die alten zu verdraengen.
    assert geladen.crypto_assets == ["BTC"]


def test_sync_flat_entities_bildet_alle_vier_typen_ab() -> None:
    """Regression je Typ, den ``_sync_flat_entities`` kennt."""
    from app.analysis.pipeline import _sync_flat_entities
    from app.core.domain.document import EntityMention

    doc = CanonicalDocument(url="https://example.com/typen", title="Typen")
    _sync_flat_entities(
        doc,
        [
            EntityMention(name="halving", entity_type="topic", confidence=0.9, source="rule"),
            EntityMention(name="Powell", entity_type="person", confidence=0.9, source="rule"),
            EntityMention(
                name="BlackRock", entity_type="organization", confidence=0.9, source="rule"
            ),
            EntityMention(name="BTC", entity_type="crypto_asset", confidence=0.9, source="rule"),
        ],
    )

    assert doc.topics == ["halving"]
    assert doc.people == ["Powell"]
    assert doc.organizations == ["BlackRock"]
    assert doc.crypto_assets == ["BTC"]
    assert set(doc.entities) == {"Powell", "BlackRock"}
    # Personen und Organisationen stehen zusaetzlich in ``entities``, Topics und
    # Krypto-Assets nicht — sie haben ihr eigenes Feld.
    assert "halving" not in doc.entities
    assert "BTC" not in doc.entities


def test_equity_etf_macro_bleiben_ein_offener_schema_gap() -> None:
    """Diese drei Typen haben KEIN Zielfeld — bewusst, und sichtbar gehalten.

    ``CanonicalDocument`` kennt kein Feld fuer Aktien, ETFs oder Makro-Begriffe.
    Sie nach ``tickers`` zu schreiben waere kein Fix, sondern eine zweite,
    nicht unterscheidbare Herkunft in einer Spalte, die ``update_analysis``
    bereits aus ``AnalysisResult.affected_assets`` fuellt. Faellt dieser Test,
    hat jemand ein Zielfeld eingefuehrt — dann gehoert die Abbildung hierher.
    """
    from app.analysis.pipeline import _OHNE_ZIELFELD, _sync_flat_entities
    from app.core.domain.document import EntityMention

    assert _OHNE_ZIELFELD == {"equity", "etf", "macro"}

    doc = CanonicalDocument(url="https://example.com/gap", title="Gap")
    _sync_flat_entities(
        doc,
        [
            EntityMention(name="AAPL", entity_type="equity", confidence=0.9, source="rule"),
            EntityMention(name="IBIT", entity_type="etf", confidence=0.9, source="rule"),
            EntityMention(name="CPI", entity_type="macro", confidence=0.9, source="rule"),
        ],
    )

    assert doc.tickers == []
    assert doc.crypto_assets == []
    assert doc.entities == []
    assert doc.topics == []
