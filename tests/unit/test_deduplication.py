"""Tests for Document Deduplication."""

from __future__ import annotations

import pytest

from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceType
from app.enrichment.deduplication.deduplicator import (
    DocumentDeduplicator, jaccard_similarity, normalize_url, title_tokens,
)


def make_doc(title: str, url: str = "https://example.com/article", content_hash: str = "") -> CanonicalDocument:
    doc = CanonicalDocument(
        source_id="src1", source_name="Test", source_type=SourceType.RSS_FEED, url=url, title=title,
    )
    if content_hash:
        doc.content_hash = content_hash
    return doc


class TestURLNormalization:
    def test_removes_utm_params(self) -> None:
        url = "https://example.com/article?utm_source=twitter&utm_medium=social"
        assert "utm_source" not in normalize_url(url)

    def test_removes_fragment(self) -> None:
        assert "#" not in normalize_url("https://example.com/article#section1")

    def test_trailing_slash(self) -> None:
        assert normalize_url("https://example.com/article/") == normalize_url("https://example.com/article")

    def test_empty_url(self) -> None:
        assert normalize_url("") == ""


class TestJaccardSimilarity:
    def test_identical(self) -> None:
        tokens = {"bitcoin", "hits", "ath"}
        assert jaccard_similarity(tokens, tokens) == 1.0

    def test_no_overlap(self) -> None:
        assert jaccard_similarity({"bitcoin", "price"}, {"ethereum", "defi"}) == 0.0

    def test_empty_sets(self) -> None:
        assert jaccard_similarity(set(), {"bitcoin"}) == 0.0


class TestDocumentDeduplicator:
    def test_exact_hash_duplicate(self) -> None:
        dedup = DocumentDeduplicator()
        doc1 = make_doc("Bitcoin News", content_hash="abc123")
        doc2 = make_doc("Bitcoin News Updated", content_hash="abc123")
        assert not dedup.is_duplicate(doc1)[0]
        dedup.register(doc1)
        is_dup, reason = dedup.is_duplicate(doc2)
        assert is_dup
        assert reason == "exact_hash_match"

    def test_url_duplicate(self) -> None:
        dedup = DocumentDeduplicator()
        doc1 = make_doc("Article A", url="https://example.com/article?utm_source=x")
        doc2 = make_doc("Article B", url="https://example.com/article")
        dedup.register(doc1)
        is_dup, reason = dedup.is_duplicate(doc2)
        assert is_dup
        assert reason == "url_match"

    def test_title_similarity_duplicate(self) -> None:
        dedup = DocumentDeduplicator(title_similarity_threshold=0.8)
        doc1 = make_doc("Bitcoin Reaches New All-Time High in 2024", url="https://src1.com/btc")
        doc2 = make_doc("Bitcoin Reaches New All-Time High in 2024", url="https://src2.com/btc")
        dedup.register(doc1)
        is_dup, reason = dedup.is_duplicate(doc2)
        assert is_dup
        assert "title_similarity" in reason

    def test_process_batch(self) -> None:
        dedup = DocumentDeduplicator()
        docs = [
            make_doc("Bitcoin News", url="https://ex.com/1", content_hash="h1"),
            make_doc("Ethereum News", url="https://ex.com/2", content_hash="h2"),
            make_doc("Bitcoin Duplicate", url="https://ex.com/3", content_hash="h1"),
        ]
        unique, dupes = dedup.process_batch(docs)
        assert len(unique) == 2
        assert len(dupes) == 1
