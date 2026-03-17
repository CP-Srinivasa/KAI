"""Tests for the Keyword Engine Phase 3.2."""

from pathlib import Path

import pytest

from app.analysis.keywords.loader import KeywordLoader
from app.analysis.keywords.matcher import KeywordMatcher
from app.core.domain.document import CanonicalDocument


@pytest.fixture
def mock_monitor_dir(tmp_path: Path):
    monitor_dir = tmp_path / "monitor"
    monitor_dir.mkdir()

    # Create fake keywords.txt
    keywords_content = """# Comment
bitcoin
ethereum # L1
defi
"""
    (monitor_dir / "keywords.txt").write_text(keywords_content, encoding="utf-8")

    # Create fake entity_aliases.yml
    aliases_content = """entity_aliases:
  - canonical: "Vitalik Buterin"
    aliases: ["Vitalik", "VB"]
    handles:
      twitter: "@VitalikButerin"
    category: "founder"

  - canonical: "Changpeng Zhao"
    aliases: ["CZ", "CZ Binance"]
    handles:
      twitter: "@cz_binance"
    category: "exchange_ceo"
"""
    (monitor_dir / "entity_aliases.yml").write_text(aliases_content, encoding="utf-8")

    return monitor_dir


def test_keyword_loader_loads_keywords(mock_monitor_dir):
    loader = KeywordLoader(mock_monitor_dir)
    keywords = loader.load_keywords()

    assert "bitcoin" in keywords
    assert "ethereum" in keywords
    assert "defi" in keywords
    assert "Comment" not in keywords
    assert "L1" not in keywords


def test_keyword_loader_loads_aliases(mock_monitor_dir):
    loader = KeywordLoader(mock_monitor_dir)
    aliases = loader.load_aliases()

    assert aliases["vitalik buterin"] == "Vitalik Buterin"
    assert aliases["vb"] == "Vitalik Buterin"
    assert aliases["@vitalikbuterin"] == "Vitalik Buterin"

    assert aliases["cz"] == "Changpeng Zhao"
    assert aliases["@cz_binance"] == "Changpeng Zhao"


def test_matcher_exact_word_boundaries():
    matcher = KeywordMatcher(keywords={"bot", "coin"}, alias_map={})

    doc1 = CanonicalDocument(
        title="We found a bot", cleaned_text="Nothing here.", url="http://example.com/1"
    )
    result1 = matcher.match(doc1)
    assert result1.hit_count == 1
    assert "bot" in result1.matched_keywords

    doc2 = CanonicalDocument(
        title="This is a bottle", cleaned_text="I collected coins.", url="http://example.com/2"
    )
    result2 = matcher.match(doc2)
    # "bot" inside "bottle", "coin" inside "coins" — word boundary prevents match
    assert result2.hit_count == 0


def test_matcher_alias_resolution():
    alias_map = {
        "vb": "Vitalik Buterin",
        "@vitalikbuterin": "Vitalik Buterin",
        "pomp": "Anthony Pompliano",
    }
    matcher = KeywordMatcher(keywords={"ethereum"}, alias_map=alias_map)

    doc = CanonicalDocument(
        title="VB talks about Ethereum",
        cleaned_text="@vitalikbuterin says Pomp is wrong.",
        url="http://example.com/1",
    )
    result = matcher.match(doc)

    keys = result.matched_keywords
    assert "Vitalik Buterin" in keys
    assert "Anthony Pompliano" in keys
    assert "ethereum" in keys
    assert "vb" not in keys  # Resolved to canonical

    # Check frequency
    # "VB" -> 1 hit, "@vitalikbuterin" -> 1 hit => frequency for Vitalik should be 2
    vitalik_hit = next(hit for hit in result.hits if hit.canonical_name == "Vitalik Buterin")
    assert vitalik_hit.frequency == 2
    assert "title" in vitalik_hit.locations
    assert "body" in vitalik_hit.locations


def test_matcher_scoring_title_priority():
    matcher = KeywordMatcher(keywords={"bitcoin"}, alias_map={})

    doc_title = CanonicalDocument(
        title="Bitcoin is booming", cleaned_text="Market is up.", url="http://example.com/1"
    )
    res_title = matcher.match(doc_title)

    doc_body = CanonicalDocument(
        title="Market update", cleaned_text="Bitcoin is booming.", url="http://example.com/2"
    )
    res_body = matcher.match(doc_body)

    assert res_title.total_score > res_body.total_score


def test_matcher_empty_document():
    matcher = KeywordMatcher(keywords={"bitcoin"}, alias_map={})
    doc = CanonicalDocument(title="", cleaned_text="", url="http://example.com/1")
    res = matcher.match(doc)
    assert res.hit_count == 0
    assert res.total_score == 0.0
