"""Tests for QueryExecutor — in-memory QuerySpec filter."""

from datetime import UTC, datetime

from app.analysis.query.executor import QueryExecutor
from app.core.domain.document import CanonicalDocument, QuerySpec
from app.core.enums import DocumentType, MarketScope, SortBy, SourceType


def _doc(
    url: str = "https://example.com",
    title: str = "Test",
    raw_text: str = "",
    published_at: datetime | None = None,
    source_type: SourceType | None = SourceType.RSS_FEED,
    document_type: DocumentType = DocumentType.ARTICLE,
    market_scope: MarketScope = MarketScope.CRYPTO,
    relevance_score: float | None = None,
    impact_score: float | None = None,
    credibility_score: float | None = None,
    sentiment_score: float | None = None,
    language: str | None = None,
    categories: list[str] | None = None,
    views: int | None = None,
    is_duplicate: bool = False,
) -> CanonicalDocument:
    return CanonicalDocument(
        url=url,
        title=title,
        raw_text=raw_text,
        published_at=published_at,
        source_type=source_type,
        document_type=document_type,
        market_scope=market_scope,
        relevance_score=relevance_score,
        impact_score=impact_score,
        credibility_score=credibility_score,
        sentiment_score=sentiment_score,
        language=language,
        categories=categories or [],
        views=views,
        is_duplicate=is_duplicate,
    )


executor = QueryExecutor()


# ── Dedup filter ──────────────────────────────────────────────────────────────


def test_exclude_duplicates_default():
    docs = [
        _doc(url="https://a.com", title="A", is_duplicate=False),
        _doc(url="https://b.com", title="B", is_duplicate=True),
    ]
    result = executor.execute(QuerySpec(exclude_duplicates=True), docs)
    assert len(result) == 1
    assert result[0].title == "A"


def test_include_duplicates():
    docs = [
        _doc(url="https://a.com", title="A", is_duplicate=False),
        _doc(url="https://b.com", title="B", is_duplicate=True),
    ]
    result = executor.execute(QuerySpec(exclude_duplicates=False), docs)
    assert len(result) == 2


# ── Date filters ──────────────────────────────────────────────────────────────


def test_from_date_filter():
    t1 = datetime(2024, 1, 1, tzinfo=UTC)
    t2 = datetime(2024, 6, 1, tzinfo=UTC)
    docs = [
        _doc(url="https://a.com", title="A", published_at=t1),
        _doc(url="https://b.com", title="B", published_at=t2),
    ]
    result = executor.execute(QuerySpec(from_date=datetime(2024, 3, 1, tzinfo=UTC)), docs)
    assert len(result) == 1
    assert result[0].title == "B"


def test_to_date_filter():
    t1 = datetime(2024, 1, 1, tzinfo=UTC)
    t2 = datetime(2024, 6, 1, tzinfo=UTC)
    docs = [
        _doc(url="https://a.com", title="A", published_at=t1),
        _doc(url="https://b.com", title="B", published_at=t2),
    ]
    result = executor.execute(QuerySpec(to_date=datetime(2024, 3, 1, tzinfo=UTC)), docs)
    assert len(result) == 1
    assert result[0].title == "A"


# ── Taxonomy filters ───────────────────────────────────────────────────────────


def test_source_type_filter():
    docs = [
        _doc(url="https://a.com", title="A", source_type=SourceType.RSS_FEED),
        _doc(url="https://b.com", title="B", source_type=SourceType.YOUTUBE_CHANNEL),
    ]
    result = executor.execute(QuerySpec(source_types=[SourceType.YOUTUBE_CHANNEL]), docs)
    assert len(result) == 1
    assert result[0].title == "B"


def test_document_type_filter():
    docs = [
        _doc(url="https://a.com", title="A", document_type=DocumentType.ARTICLE),
        _doc(url="https://b.com", title="B", document_type=DocumentType.YOUTUBE_VIDEO),
    ]
    result = executor.execute(QuerySpec(document_types=[DocumentType.YOUTUBE_VIDEO]), docs)
    assert len(result) == 1
    assert result[0].title == "B"


def test_market_scope_filter():
    docs = [
        _doc(url="https://a.com", title="A", market_scope=MarketScope.CRYPTO),
        _doc(url="https://b.com", title="B", market_scope=MarketScope.EQUITIES),
    ]
    result = executor.execute(QuerySpec(market_scopes=[MarketScope.EQUITIES]), docs)
    assert len(result) == 1
    assert result[0].title == "B"


def test_language_filter():
    docs = [
        _doc(url="https://a.com", title="A", language="en"),
        _doc(url="https://b.com", title="B", language="de"),
    ]
    result = executor.execute(QuerySpec(languages=["de"]), docs)
    assert len(result) == 1
    assert result[0].title == "B"


def test_categories_filter():
    docs = [
        _doc(url="https://a.com", title="A", categories=["defi"]),
        _doc(url="https://b.com", title="B", categories=["etf", "regulation"]),
    ]
    result = executor.execute(QuerySpec(categories=["regulation"]), docs)
    assert len(result) == 1
    assert result[0].title == "B"


# ── Score filters ──────────────────────────────────────────────────────────────


def test_min_credibility_filter():
    docs = [
        _doc(url="https://a.com", title="A", credibility_score=0.3),
        _doc(url="https://b.com", title="B", credibility_score=0.9),
    ]
    result = executor.execute(QuerySpec(min_credibility=0.5), docs)
    assert len(result) == 1
    assert result[0].title == "B"


def test_min_credibility_none_excluded():
    docs = [
        _doc(url="https://a.com", title="A", credibility_score=None),
        _doc(url="https://b.com", title="B", credibility_score=0.9),
    ]
    result = executor.execute(QuerySpec(min_credibility=0.5), docs)
    assert len(result) == 1
    assert result[0].title == "B"


def test_min_sentiment_abs_filter():
    docs = [
        _doc(url="https://a.com", title="A", sentiment_score=0.1),
        _doc(url="https://b.com", title="B", sentiment_score=-0.8),
    ]
    result = executor.execute(QuerySpec(min_sentiment_abs=0.5), docs)
    assert len(result) == 1
    assert result[0].title == "B"


# ── Text filters ───────────────────────────────────────────────────────────────


def test_query_text_filter():
    docs = [
        _doc(url="https://a.com", title="Bitcoin rally"),
        _doc(url="https://b.com", title="Ethereum update"),
    ]
    result = executor.execute(QuerySpec(query_text="bitcoin"), docs)
    assert len(result) == 1
    assert result[0].title == "Bitcoin rally"


def test_include_terms_filter():
    docs = [
        _doc(url="https://a.com", title="BTC ETF approved"),
        _doc(url="https://b.com", title="BTC correction incoming"),
    ]
    result = executor.execute(QuerySpec(include_terms=["etf"]), docs)
    assert len(result) == 1


def test_exclude_terms_filter():
    docs = [
        _doc(url="https://a.com", title="Bitcoin is amazing"),
        _doc(url="https://b.com", title="Bitcoin is a scam"),
    ]
    result = executor.execute(QuerySpec(exclude_terms=["scam"]), docs)
    assert len(result) == 1
    assert result[0].title == "Bitcoin is amazing"


def test_any_terms_filter():
    docs = [
        _doc(url="https://a.com", title="Fed raises interest rates"),
        _doc(url="https://b.com", title="Weather is nice today"),
    ]
    result = executor.execute(QuerySpec(any_terms=["fed", "rate"]), docs)
    assert len(result) == 1


def test_title_terms_filter():
    docs = [
        _doc(url="https://a.com", title="BTC ATH 2025"),
        _doc(url="https://b.com", title="ETH update"),
    ]
    result = executor.execute(QuerySpec(title_terms=["ath"]), docs)
    assert len(result) == 1
    assert "ATH" in result[0].title


# ── Sorting ────────────────────────────────────────────────────────────────────


def test_sort_by_relevance():
    docs = [
        _doc(url="https://a.com", title="A", relevance_score=0.3),
        _doc(url="https://b.com", title="B", relevance_score=0.9),
        _doc(url="https://c.com", title="C", relevance_score=0.6),
    ]
    result = executor.execute(QuerySpec(sort_by=SortBy.RELEVANCE, limit=10), docs)
    assert result[0].title == "B"
    assert result[-1].title == "A"


def test_sort_by_impact():
    docs = [
        _doc(url="https://a.com", title="A", impact_score=0.1),
        _doc(url="https://b.com", title="B", impact_score=0.8),
    ]
    result = executor.execute(QuerySpec(sort_by=SortBy.IMPACT, limit=10), docs)
    assert result[0].title == "B"


def test_sort_by_published_at():
    t1 = datetime(2024, 1, 1, tzinfo=UTC)
    t2 = datetime(2024, 6, 1, tzinfo=UTC)
    docs = [
        _doc(url="https://a.com", title="Old", published_at=t1),
        _doc(url="https://b.com", title="New", published_at=t2),
    ]
    result = executor.execute(QuerySpec(sort_by=SortBy.PUBLISHED_AT, limit=10), docs)
    assert result[0].title == "New"


# ── Pagination ────────────────────────────────────────────────────────────────


def test_limit_and_offset():
    docs = [_doc(url=f"https://x{i}.com", title=f"Doc {i}") for i in range(10)]
    result = executor.execute(QuerySpec(limit=3, offset=2), docs)
    assert len(result) == 3


def test_limit_exceeds_available():
    docs = [_doc(url=f"https://x{i}.com", title=f"Doc {i}") for i in range(3)]
    result = executor.execute(QuerySpec(limit=100), docs)
    assert len(result) == 3


def test_empty_input():
    result = executor.execute(QuerySpec(), [])
    assert result == []
