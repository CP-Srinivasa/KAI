from app.core.domain.document import CanonicalDocument
from app.enrichment.deduplication.deduplicator import Deduplicator


def _doc(url: str, title: str = "Title", text: str | None = None) -> CanonicalDocument:
    return CanonicalDocument(url=url, title=title, raw_text=text)


def test_first_document_not_duplicate() -> None:
    dedup = Deduplicator()
    doc = _doc("https://example.com/article-1")
    assert not dedup.is_duplicate(doc)


def test_same_url_is_duplicate() -> None:
    dedup = Deduplicator()
    doc = _doc("https://example.com/article-1")
    dedup.register(doc)
    assert dedup.is_duplicate(_doc("https://example.com/article-1"))


def test_trailing_slash_normalized() -> None:
    dedup = Deduplicator()
    dedup.register(_doc("https://example.com/article-1"))
    assert dedup.is_duplicate(_doc("https://example.com/article-1/"))


def test_different_url_not_duplicate() -> None:
    dedup = Deduplicator()
    dedup.register(_doc("https://example.com/article-1"))
    assert not dedup.is_duplicate(_doc("https://example.com/article-2"))


def test_same_content_hash_is_duplicate() -> None:
    dedup = Deduplicator()
    doc1 = _doc("https://example.com/a", "Bitcoin rises", "Some text")
    doc2 = _doc("https://example.com/a", "Bitcoin rises", "Some text")
    dedup.register(doc1)
    assert dedup.is_duplicate(doc2)


def test_filter_removes_duplicates() -> None:
    dedup = Deduplicator()
    docs = [
        _doc("https://example.com/1"),
        _doc("https://example.com/2"),
        _doc("https://example.com/1"),  # duplicate
        _doc("https://example.com/3"),
    ]
    result = dedup.filter(docs)
    assert len(result) == 3
    urls = [d.url for d in result]
    assert "https://example.com/1" in urls
    assert "https://example.com/2" in urls
    assert "https://example.com/3" in urls


def test_reset_clears_state() -> None:
    dedup = Deduplicator()
    doc = _doc("https://example.com/article-1")
    dedup.register(doc)
    assert dedup.is_duplicate(doc)
    dedup.reset()
    assert not dedup.is_duplicate(doc)


def test_filter_empty_list() -> None:
    dedup = Deduplicator()
    assert dedup.filter([]) == []
