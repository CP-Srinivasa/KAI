"""Tests for the Deduplicator — binary and scored duplicate detection."""

from __future__ import annotations

import pytest

from app.core.domain.document import CanonicalDocument
from app.enrichment.deduplication.deduplicator import Deduplicator


def _doc(
    url: str,
    title: str = "Default Title",
    text: str | None = None,
) -> CanonicalDocument:
    return CanonicalDocument(url=url, title=title, raw_text=text)


# ── is_duplicate / register ───────────────────────────────────────────────────


def test_first_document_not_duplicate() -> None:
    dedup = Deduplicator()
    assert not dedup.is_duplicate(_doc("https://example.com/article-1"))


def test_same_url_is_duplicate() -> None:
    dedup = Deduplicator()
    doc = _doc("https://example.com/article-1")
    dedup.register(doc)
    assert dedup.is_duplicate(_doc("https://example.com/article-1"))


def test_trailing_slash_normalized() -> None:
    dedup = Deduplicator()
    dedup.register(_doc("https://example.com/article"))
    assert dedup.is_duplicate(_doc("https://example.com/article/"))


def test_www_prefix_normalized() -> None:
    dedup = Deduplicator()
    dedup.register(_doc("https://www.coindesk.com/article"))
    assert dedup.is_duplicate(_doc("https://coindesk.com/article"))


def test_utm_params_ignored() -> None:
    dedup = Deduplicator()
    dedup.register(_doc("https://example.com/article"))
    assert dedup.is_duplicate(
        _doc("https://example.com/article?utm_source=twitter&utm_medium=social")
    )


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


# ── title-hash based dedup (threshold=0.85) ───────────────────────────────────


def test_same_title_different_url_not_duplicate_at_default_threshold() -> None:
    """Default threshold=1.0 → title match alone does not flag as duplicate."""
    dedup = Deduplicator(threshold=1.0)
    dedup.register(_doc("https://coindesk.com/article", "Bitcoin hits ATH"))
    # Same title, different URL → NOT a duplicate at threshold=1.0
    assert not dedup.is_duplicate(_doc("https://cointelegraph.com/article", "Bitcoin hits ATH"))


def test_same_title_different_url_is_duplicate_at_lower_threshold() -> None:
    """Threshold=0.85 → title hash match (score=0.85) triggers dedup."""
    dedup = Deduplicator(threshold=0.85)
    dedup.register(_doc("https://coindesk.com/article", "Bitcoin hits ATH"))
    assert dedup.is_duplicate(_doc("https://cointelegraph.com/article", "Bitcoin hits ATH"))


def test_punctuation_normalized_title_match() -> None:
    """Titles that differ only in punctuation/case should still match."""
    dedup = Deduplicator(threshold=0.85)
    dedup.register(_doc("https://a.com/1", "Bitcoin hits $100K!"))
    # Same title, stripped punctuation → same title_hash
    assert dedup.is_duplicate(_doc("https://b.com/1", "Bitcoin Hits 100K"))


# ── DuplicateScore ────────────────────────────────────────────────────────────


def test_score_unique_document() -> None:
    dedup = Deduplicator()
    s = dedup.score(_doc("https://example.com/new"))
    assert s.score == 0.0
    assert s.is_duplicate is False
    assert s.reasons == []


def test_score_url_match_returns_1() -> None:
    dedup = Deduplicator()
    doc = _doc("https://example.com/article")
    dedup.register(doc)
    s = dedup.score(_doc("https://example.com/article"))
    assert s.score == 1.0
    assert "url_match" in s.reasons
    assert s.is_duplicate is True


def test_score_content_hash_match_returns_1() -> None:
    dedup = Deduplicator()
    doc = _doc("https://example.com/a", "Bitcoin rises", "Some text")
    dedup.register(doc)
    s = dedup.score(_doc("https://example.com/a", "Bitcoin rises", "Some text"))
    assert s.score == 1.0
    assert "content_hash" in s.reasons


def test_score_title_match_returns_085() -> None:
    dedup = Deduplicator(threshold=0.85)
    dedup.register(_doc("https://a.com/x", "ETH 2.0 is Live"))
    s = dedup.score(_doc("https://b.com/y", "ETH 2.0 is Live"))
    assert s.score == 0.85
    assert "title_hash" in s.reasons


def test_score_multiple_reasons() -> None:
    """URL match + title hash both fire — score should be max (1.0)."""
    dedup = Deduplicator()
    doc = _doc("https://example.com/a", "Same title")
    dedup.register(doc)
    s = dedup.score(_doc("https://example.com/a", "Same title"))
    assert s.score == 1.0
    assert len(s.reasons) >= 1


def test_score_is_readonly() -> None:
    """score() must not change state."""
    dedup = Deduplicator()
    doc = _doc("https://example.com/a")
    dedup.score(doc)  # should NOT register
    assert dedup.seen_count == 0


# ── filter / filter_scored ────────────────────────────────────────────────────


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


def test_filter_empty_list() -> None:
    assert Deduplicator().filter([]) == []


def test_filter_scored_includes_duplicates_in_output() -> None:
    dedup = Deduplicator()
    docs = [
        _doc("https://example.com/1", "Title A"),
        _doc("https://example.com/1", "Title A"),  # duplicate
        _doc("https://example.com/2", "Title B"),
    ]
    pairs = dedup.filter_scored(docs)
    assert len(pairs) == 3
    scores = [s.score for _, s in pairs]
    assert scores[0] == 0.0  # unique
    assert scores[1] == 1.0  # duplicate
    assert scores[2] == 0.0  # unique


def test_filter_scored_only_registers_unique() -> None:
    dedup = Deduplicator()
    docs = [
        _doc("https://example.com/1"),
        _doc("https://example.com/1"),
    ]
    dedup.filter_scored(docs)
    assert dedup.seen_count == 1


# ── reset / seen_count / threshold ───────────────────────────────────────────


def test_reset_clears_state() -> None:
    dedup = Deduplicator()
    doc = _doc("https://example.com/article-1")
    dedup.register(doc)
    assert dedup.is_duplicate(doc)
    dedup.reset()
    assert not dedup.is_duplicate(doc)


def test_seen_count_increments() -> None:
    dedup = Deduplicator()
    dedup.register(_doc("https://example.com/a"))
    dedup.register(_doc("https://example.com/b"))
    assert dedup.seen_count == 2


def test_seen_count_does_not_double_count() -> None:
    dedup = Deduplicator()
    doc = _doc("https://example.com/a")
    dedup.register(doc)
    dedup.register(doc)  # same URL → set, no double count
    assert dedup.seen_count == 1


def test_invalid_threshold_raises() -> None:
    with pytest.raises(ValueError):
        Deduplicator(threshold=0.0)
    with pytest.raises(ValueError):
        Deduplicator(threshold=1.5)


def test_threshold_property() -> None:
    dedup = Deduplicator(threshold=0.9)
    assert dedup.threshold == 0.9
