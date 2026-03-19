"""Tests for keyword_matcher, asset_detector, rule_analyzer, and scoring."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.analysis.rules.asset_detector import canonical_names, detect_assets
from app.analysis.rules.keyword_matcher import KeywordMatcher
from app.analysis.rules.rule_analyzer import RuleAnalyzer, compute_spam_probability
from app.analysis.scoring import PriorityScore, compute_priority, is_alert_worthy
from app.core.domain.document import AnalysisResult
from app.core.enums import MarketScope, SentimentLabel

# ── KeywordMatcher ────────────────────────────────────────────────────────────


@pytest.fixture
def matcher() -> KeywordMatcher:
    return KeywordMatcher(
        keywords=frozenset(
            {
                "Bitcoin",
                "BTC",
                "Ethereum",
                "Smart Contract",
                "Halving",
                "DeFi",
                "Layer 2",
                "51% Attacke",
            }
        )
    )


def test_keyword_matcher_single_word_title(matcher: KeywordMatcher) -> None:
    matches = matcher.match("Bitcoin hits new ATH today")
    assert any(m.keyword == "Bitcoin" for m in matches)
    hit = next(m for m in matches if m.keyword == "Bitcoin")
    assert hit.in_title is True
    assert hit.in_text is False


def test_keyword_matcher_text_only(matcher: KeywordMatcher) -> None:
    matches = matcher.match("Market update", "Ethereum ETH DeFi")
    eth = next((m for m in matches if m.keyword == "Ethereum"), None)
    assert eth is not None
    assert eth.in_title is False
    assert eth.in_text is True


def test_keyword_matcher_phrase(matcher: KeywordMatcher) -> None:
    matches = matcher.match("Smart Contract audit fails", None)
    assert any(m.keyword == "Smart Contract" for m in matches)


def test_keyword_matcher_no_partial_match(matcher: KeywordMatcher) -> None:
    """'BTC' must not match 'BTCusdt'."""
    matches = matcher.match("BTCusdt volume at ATH")
    assert not any(m.keyword == "BTC" for m in matches)


def test_keyword_matcher_case_insensitive(matcher: KeywordMatcher) -> None:
    matches = matcher.match("bitcoin rally")
    assert any(m.keyword == "Bitcoin" for m in matches)


def test_keyword_matcher_count(matcher: KeywordMatcher) -> None:
    matches = matcher.match("Bitcoin Bitcoin Bitcoin", "btc btc")
    btc_match = next((m for m in matches if m.keyword == "Bitcoin"), None)
    assert btc_match is not None
    assert btc_match.count >= 3


def test_keyword_matcher_title_sorted_first(matcher: KeywordMatcher) -> None:
    matches = matcher.match("Bitcoin hits ATH", "Ethereum and DeFi")
    assert matches[0].in_title is True  # title match comes first


def test_keyword_matcher_from_file(tmp_path: Path) -> None:
    kw_file = tmp_path / "keywords.txt"
    kw_file.write_text("Bitcoin\n# comment\nEthereum\n\nDeFi\n", encoding="utf-8")
    m = KeywordMatcher.from_file(kw_file)
    assert m.keyword_count == 3
    assert "Bitcoin" in m.keywords


def test_keyword_matcher_from_missing_file(tmp_path: Path) -> None:
    m = KeywordMatcher.from_file(tmp_path / "nonexistent.txt")
    assert m.keyword_count == 0


# ── AssetDetector ─────────────────────────────────────────────────────────────


def test_detect_btc_by_name() -> None:
    matches = detect_assets("Bitcoin price surges", None)
    assert any(m.canonical == "Bitcoin" for m in matches)


def test_detect_btc_by_ticker() -> None:
    matches = detect_assets("BTC rally", None)
    assert any(m.canonical == "Bitcoin" for m in matches)


def test_detect_eth() -> None:
    matches = detect_assets("Ethereum update released")
    assert any(m.canonical == "Ethereum" for m in matches)


def test_detect_no_partial_ticker() -> None:
    """'ADA' must not match inside 'Kanada'."""
    matches = detect_assets("Wir reisen nach Kanada")
    assert not any(m.canonical == "Cardano" for m in matches)


def test_detect_multiple_assets() -> None:
    matches = detect_assets("Bitcoin and Ethereum rally as DeFi grows")
    canonicals = canonical_names(matches)
    assert "Bitcoin" in canonicals
    assert "Ethereum" in canonicals
    assert "DeFi" in canonicals


def test_detect_in_title_flag() -> None:
    matches = detect_assets("BTC ETF approved", "nothing special")
    btc = next((m for m in matches if m.canonical == "Bitcoin"), None)
    assert btc is not None
    assert btc.in_title is True


def test_detect_returns_deduplicated() -> None:
    """Bitcoin mentioned as both 'Bitcoin' and 'BTC' → one match."""
    matches = detect_assets("Bitcoin BTC BTC rally", None)
    bitcoin_matches = [m for m in matches if m.canonical == "Bitcoin"]
    assert len(bitcoin_matches) == 1


def test_canonical_names_helper() -> None:
    matches = detect_assets("Bitcoin and ETH", None)
    names = canonical_names(matches)
    assert isinstance(names, list)
    assert "Bitcoin" in names


# ── RuleAnalyzer ─────────────────────────────────────────────────────────────


@pytest.fixture
def rule_analyzer() -> RuleAnalyzer:
    m = KeywordMatcher(
        keywords=frozenset(
            {"Bitcoin", "BTC", "Ethereum", "ETH", "DeFi", "Halving", "Inflation", "Aktien"}
        )
    )
    return RuleAnalyzer(m)


def _doc_id() -> object:
    return uuid4()


def test_rule_analyzer_basic(rule_analyzer: RuleAnalyzer) -> None:
    result = rule_analyzer.analyze(_doc_id(), "Bitcoin price hits ATH", "BTC surges.")
    assert isinstance(result, AnalysisResult)
    assert result.relevance_score > 0.0
    assert result.confidence_score == 1.0
    assert isinstance(result.explanation_short, str)
    assert "Bitcoin" in result.affected_assets or result.relevance_score > 0


def test_rule_analyzer_market_scope_crypto(rule_analyzer: RuleAnalyzer) -> None:
    result = rule_analyzer.analyze(_doc_id(), "Bitcoin Halving approaches", "ETH and DeFi boom.")
    assert result.market_scope in (MarketScope.CRYPTO, MarketScope.MIXED)


def test_rule_analyzer_market_scope_macro(rule_analyzer: RuleAnalyzer) -> None:
    result = rule_analyzer.analyze(_doc_id(), "Inflation fears dominate", None)
    assert result.market_scope in (MarketScope.MACRO, MarketScope.UNKNOWN, MarketScope.MIXED)


def test_rule_analyzer_irrelevant_doc(rule_analyzer: RuleAnalyzer) -> None:
    result = rule_analyzer.analyze(_doc_id(), "Cat found in local park", "No news here.")
    assert result.relevance_score == 0.0
    assert result.market_scope == MarketScope.UNKNOWN


def test_rule_analyzer_spam_all_caps() -> None:
    spam = compute_spam_probability("BITCOIN TO THE MOON!!!", None)
    assert spam > 0.0


def test_rule_analyzer_tags_from_keywords(rule_analyzer: RuleAnalyzer) -> None:
    result = rule_analyzer.analyze(_doc_id(), "Bitcoin and Ethereum update", "DeFi Halving news")
    assert len(result.tags) > 0


# ── Scoring ───────────────────────────────────────────────────────────────────


def _make_result(**overrides: object) -> AnalysisResult:
    defaults: dict = {
        "document_id": str(uuid4()),
        "sentiment_label": SentimentLabel.NEUTRAL,
        "sentiment_score": 0.0,
        "relevance_score": 0.5,
        "impact_score": 0.5,
        "confidence_score": 1.0,
        "novelty_score": 0.5,
        "actionable": False,
        "explanation_short": "Test",
        "explanation_long": "Test long",
    }
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def test_priority_is_in_range() -> None:
    s = compute_priority(_make_result())
    assert 1 <= s.priority <= 10


def test_priority_high_scores_give_high_priority() -> None:
    high = compute_priority(
        _make_result(relevance_score=1.0, impact_score=1.0, novelty_score=1.0, actionable=True)
    )
    assert high.priority >= 8


def test_priority_zero_scores_give_low_priority() -> None:
    low = compute_priority(_make_result(relevance_score=0.0, impact_score=0.0, novelty_score=0.0))
    assert low.priority <= 3


def test_priority_spam_capped_at_3() -> None:
    spammy = compute_priority(
        _make_result(relevance_score=1.0, impact_score=1.0),
        spam_probability=0.9,
    )
    assert spammy.priority <= 3
    assert spammy.is_spam_capped is True


def test_priority_actionable_bonus() -> None:
    without = compute_priority(_make_result(actionable=False, relevance_score=0.5))
    with_bonus = compute_priority(_make_result(actionable=True, relevance_score=0.5))
    assert with_bonus.priority >= without.priority


def test_priority_returns_dataclass() -> None:
    s = compute_priority(_make_result())
    assert isinstance(s, PriorityScore)
    assert isinstance(s.raw_score, float)


def test_is_alert_worthy_above_threshold() -> None:
    high = _make_result(relevance_score=1.0, impact_score=1.0, novelty_score=1.0, actionable=True)
    assert is_alert_worthy(high, min_priority=7)


def test_is_alert_worthy_below_threshold() -> None:
    low = _make_result(relevance_score=0.1, impact_score=0.1, novelty_score=0.1)
    assert not is_alert_worthy(low, min_priority=7)


def test_is_alert_worthy_rejects_spam() -> None:
    spammy = _make_result(relevance_score=1.0, impact_score=1.0, actionable=True)
    assert not is_alert_worthy(spammy, spam_probability=0.9)
