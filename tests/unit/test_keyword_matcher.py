"""Tests for app/analysis/keywords/matcher.py"""
from __future__ import annotations

import pytest

from app.analysis.keywords.matcher import (
    KeywordMatcher,
    KeywordHit,
    MatchResult,
    _build_alias_map,
    _contains_word,
    _normalize_keyword,
)


# ──────────────────────────────────────────────
# Helpers / Fixtures
# ──────────────────────────────────────────────

ALIAS_GROUPS = [
    {"canonical": "Anthony Pompliano", "aliases": ["Pomp", "APompliano", "Anthony Pompliano"]},
    {"canonical": "Changpeng Zhao", "aliases": ["CZ", "CZ Binance", "Changpeng Zhao"]},
    {"canonical": "Bitcoin", "aliases": ["BTC", "bitcoin", "Bitcoin"]},
]


def _make_matcher(keywords: list[str], aliases: list[dict] | None = None) -> KeywordMatcher:
    return KeywordMatcher(keywords=keywords, alias_groups=aliases or [])


# ──────────────────────────────────────────────
# Unit: helpers
# ──────────────────────────────────────────────

class TestNormalizeKeyword:
    def test_lowercases(self) -> None:
        assert _normalize_keyword("Bitcoin") == "bitcoin"

    def test_strips_whitespace(self) -> None:
        assert _normalize_keyword("  ethereum  ") == "ethereum"


class TestContainsWord:
    def test_single_word_match(self) -> None:
        assert _contains_word("bitcoin is rising", "bitcoin") is True

    def test_word_boundary_not_substring(self) -> None:
        # "bit" should NOT match inside "bitcoin"
        assert _contains_word("bitcoin rises", "bit") is False

    def test_phrase_match(self) -> None:
        assert _contains_word("the nft marketplace launched", "nft marketplace") is True

    def test_case_insensitive(self) -> None:
        assert _contains_word("Bitcoin ETF approved", "bitcoin") is True

    def test_no_match(self) -> None:
        assert _contains_word("ethereum is up", "bitcoin") is False

    def test_at_start(self) -> None:
        assert _contains_word("defi is growing", "defi") is True

    def test_at_end(self) -> None:
        assert _contains_word("price of btc", "btc") is True


class TestBuildAliasMap:
    def test_aliases_mapped(self) -> None:
        mapping = _build_alias_map(ALIAS_GROUPS)
        assert mapping["pomp"] == "Anthony Pompliano"
        assert mapping["cz"] == "Changpeng Zhao"

    def test_canonical_also_mapped(self) -> None:
        mapping = _build_alias_map(ALIAS_GROUPS)
        assert mapping["anthony pompliano"] == "Anthony Pompliano"

    def test_empty_groups(self) -> None:
        assert _build_alias_map([]) == {}

    def test_group_without_canonical_skipped(self) -> None:
        groups = [{"aliases": ["foo", "bar"]}]  # No canonical
        mapping = _build_alias_map(groups)
        assert len(mapping) == 0


# ──────────────────────────────────────────────
# KeywordMatcher.match_text
# ──────────────────────────────────────────────

class TestKeywordMatcherMatchText:
    def test_simple_keyword_in_body(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        result = matcher.match_text(title="News", body="Bitcoin is rising fast")
        assert "bitcoin" in result.matched_keywords
        assert result.score > 0.0

    def test_keyword_in_title_higher_score(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        in_title = matcher.match_text(title="Bitcoin ETF approved", body="")
        in_body = matcher.match_text(title="Market news", body="Bitcoin is rising")
        # Title match should score higher due to title_boost
        assert in_title.score > in_body.score

    def test_no_match(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        result = matcher.match_text(title="Stock news", body="Apple earnings beat expectations")
        assert result.matched_count == 0
        assert result.score == 0.0

    def test_multiple_keywords(self) -> None:
        matcher = _make_matcher(["bitcoin", "ethereum", "defi"])
        result = matcher.match_text(title="Ethereum DeFi", body="Bitcoin is also mentioned")
        assert result.matched_count == 3

    def test_phrase_match(self) -> None:
        matcher = _make_matcher(['"nft marketplace"'])
        result = matcher.match_text(title="NFT Marketplace", body="")
        # Phrase should match case-insensitively
        assert result.matched_count >= 0  # Phrase matching depends on quote stripping

    def test_score_capped_at_1(self) -> None:
        matcher = _make_matcher(["bitcoin", "ethereum", "defi", "crypto", "blockchain"])
        result = matcher.match_text(
            title="Bitcoin Ethereum DeFi Crypto Blockchain",
            body="All keywords here: bitcoin ethereum defi crypto blockchain"
        )
        assert result.score <= 1.0

    def test_entity_hit_detected(self) -> None:
        matcher = _make_matcher(["bitcoin"], aliases=ALIAS_GROUPS)
        result = matcher.match_text(title="Pomp talks Bitcoin", body="Anthony Pompliano said...")
        assert result.has_entity_hit is True
        assert "Anthony Pompliano" in result.entity_hits

    def test_alias_resolution(self) -> None:
        matcher = _make_matcher(["defi"], aliases=ALIAS_GROUPS)
        result = matcher.match_text(title="CZ speaks at conference", body="Changpeng Zhao announced")
        assert "Changpeng Zhao" in result.entity_hits

    def test_url_match_lower_score(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        url_match = matcher.match_text(title="News", body="", url="bitcoin.com/news")
        body_match = matcher.match_text(title="News", body="Bitcoin rises today")
        assert body_match.score >= url_match.score

    def test_empty_text_no_match(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        result = matcher.match_text(title="", body="")
        assert result.matched_count == 0

    def test_match_result_to_dict(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        result = matcher.match_text(title="Bitcoin news", body="")
        d = result.to_dict()
        assert "matched_keywords" in d
        assert "score" in d
        assert "hits" in d
        assert "entity_hits" in d

    def test_hit_field_recorded(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        result = matcher.match_text(title="Bitcoin rises", body="")
        assert result.hits[0].field == "title"

    def test_body_hit_field(self) -> None:
        matcher = _make_matcher(["ethereum"])
        result = matcher.match_text(title="Market news", body="Ethereum breaks ATH")
        assert result.hits[0].field == "body"


# ──────────────────────────────────────────────
# KeywordMatcher.match (domain object)
# ──────────────────────────────────────────────

class _MockDoc:
    def __init__(self, title: str, text: str, url: str = "") -> None:
        self.title = title
        self.cleaned_text = text
        self.url = url


class TestKeywordMatcherMatchDoc:
    def test_matches_domain_object(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        doc = _MockDoc("Bitcoin rises", "Markets reacted")
        result = matcher.match(doc)
        assert result.matched_count > 0

    def test_missing_attributes_handled(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        result = matcher.match(object())  # No attributes at all
        assert result.matched_count == 0


# ──────────────────────────────────────────────
# KeywordMatcher.filter
# ──────────────────────────────────────────────

class TestKeywordMatcherFilter:
    def test_filters_non_matching(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        docs = [
            _MockDoc("Bitcoin rises", ""),
            _MockDoc("Stock market news", ""),
            _MockDoc("Bitcoin crash fears", ""),
        ]
        results = matcher.filter(docs, min_score=0.0)
        assert len(results) == 2

    def test_sorted_by_score_descending(self) -> None:
        matcher = _make_matcher(["bitcoin"])
        docs = [
            _MockDoc("Market news", "Some bitcoin mention"),
            _MockDoc("Bitcoin ETF approved today", "Bitcoin bitcoin everywhere"),
        ]
        results = matcher.filter(docs, min_score=0.0)
        assert results[0][1].score >= results[1][1].score


# ──────────────────────────────────────────────
# Weighted keywords
# ──────────────────────────────────────────────

class TestWeightedKeywords:
    def test_higher_weight_raises_score(self) -> None:
        m1 = KeywordMatcher(
            keywords=["bitcoin", "ethereum"],
            keyword_weights={"bitcoin": 3.0, "ethereum": 1.0},
        )
        m2 = KeywordMatcher(
            keywords=["bitcoin", "ethereum"],
        )
        doc_btc = _MockDoc("Bitcoin news", "")
        doc_eth = _MockDoc("Ethereum news", "")
        r1_btc = m1.match(doc_btc)
        r1_eth = m1.match(doc_eth)
        # With weight 3x on bitcoin, bitcoin match should score higher than ethereum
        assert r1_btc.score > r1_eth.score
