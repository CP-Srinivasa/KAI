"""Tests for novelty, credibility, and priority scoring modules."""
from __future__ import annotations

import pytest

from app.analysis.scoring.novelty import NoveltyScorer, _title_tokens, _jaccard
from app.analysis.scoring.credibility import CredibilityScorer, _spam_penalty, _title_quality
from app.analysis.scoring.priority import PriorityComposer, ScoreBundle
from app.core.enums import DocumentPriority


# ──────────────────────────────────────────────
# Novelty Scorer
# ──────────────────────────────────────────────

class TestTitleTokens:
    def test_splits_words(self) -> None:
        tokens = _title_tokens("Bitcoin Rises Today")
        assert "bitcoin" in tokens
        assert "rises" in tokens

    def test_removes_punctuation(self) -> None:
        tokens = _title_tokens("Bitcoin: The Future!")
        assert "bitcoin" in tokens
        assert ":" not in tokens

    def test_empty(self) -> None:
        assert _title_tokens("") == frozenset()


class TestJaccard:
    def test_identical_sets(self) -> None:
        s = frozenset(["a", "b", "c"])
        assert _jaccard(s, s) == 1.0

    def test_empty_sets(self) -> None:
        assert _jaccard(frozenset(), frozenset()) == 0.0

    def test_no_overlap(self) -> None:
        a = frozenset(["a", "b"])
        b = frozenset(["c", "d"])
        assert _jaccard(a, b) == 0.0

    def test_partial_overlap(self) -> None:
        a = frozenset(["a", "b", "c"])
        b = frozenset(["b", "c", "d"])
        result = _jaccard(a, b)
        assert 0.0 < result < 1.0


class TestNoveltyScorer:
    def test_first_doc_is_novel(self) -> None:
        scorer = NoveltyScorer()
        score = scorer.score("hash1", "Bitcoin ETF Approved")
        assert score == 1.0

    def test_duplicate_hash_scores_zero(self) -> None:
        scorer = NoveltyScorer()
        scorer.register("hash1", "Some title")
        assert scorer.score("hash1", "Different title") == 0.0

    def test_similar_title_reduces_score(self) -> None:
        scorer = NoveltyScorer(similarity_threshold=0.7)
        scorer.register("hash1", "Bitcoin ETF Approved by SEC")
        # Nearly identical title
        score = scorer.score("hash2", "Bitcoin ETF Approved by SEC Today")
        assert score < 1.0

    def test_different_title_stays_novel(self) -> None:
        scorer = NoveltyScorer()
        scorer.register("hash1", "Bitcoin ETF Approved")
        score = scorer.score("hash2", "Ethereum Layer 2 Launch")
        assert score > 0.8

    def test_score_and_register_atomically(self) -> None:
        scorer = NoveltyScorer()
        s1 = scorer.score_and_register("hash1", "Some news")
        s2 = scorer.score("hash1", "Some news")
        assert s1 == 1.0  # Novel on first call
        assert s2 == 0.0  # Already registered

    def test_reset_clears_state(self) -> None:
        scorer = NoveltyScorer()
        scorer.register("hash1", "Bitcoin news")
        scorer.reset()
        assert scorer.score("hash1", "Bitcoin news") == 1.0

    def test_empty_hash_and_title(self) -> None:
        scorer = NoveltyScorer()
        score = scorer.score("", "")
        assert score == 1.0  # No data to compare against


# ──────────────────────────────────────────────
# Credibility Scorer
# ──────────────────────────────────────────────

class TestSpamPenalty:
    def test_clean_content_no_penalty(self) -> None:
        penalty = _spam_penalty("Bitcoin price analysis", "The market showed...")
        assert penalty == 0.0

    def test_excessive_exclamation(self) -> None:
        penalty = _spam_penalty("Bitcoin CRASHES!!!", "")
        assert penalty > 0.0

    def test_all_caps_word(self) -> None:
        penalty = _spam_penalty("BREAKING: Bitcoin dumps", "")
        assert penalty > 0.0

    def test_clickbait_phrase(self) -> None:
        penalty = _spam_penalty("You won't believe what happened", "")
        assert penalty > 0.0

    def test_penalty_capped(self) -> None:
        # Worst case: multiple spam signals
        title = "SHOCKING!!! You won't BELIEVE this AMAZING BREAKING news!!!"
        penalty = _spam_penalty(title, "")
        assert penalty <= 0.30


class TestTitleQuality:
    def test_good_title(self) -> None:
        score = _title_quality("Bitcoin ETF approved by SEC in landmark decision")
        assert score == 1.0

    def test_empty_title(self) -> None:
        assert _title_quality("") == 0.5

    def test_very_short_title(self) -> None:
        assert _title_quality("Hi") == 0.5

    def test_all_caps_title(self) -> None:
        score = _title_quality("BITCOIN RISES")
        assert score < 1.0

    def test_very_long_title(self) -> None:
        long = "A" * 400
        score = _title_quality(long)
        assert score < 1.0


class TestCredibilityScorer:
    def test_high_credibility_source_good_content(self) -> None:
        scorer = CredibilityScorer()
        score = scorer.score(
            source_credibility=0.9,
            title="Bitcoin ETF approved by SEC",
            body="The Securities and Exchange Commission has approved..." * 10,
        )
        assert score > 0.7

    def test_low_credibility_source(self) -> None:
        scorer = CredibilityScorer()
        score = scorer.score(
            source_credibility=0.1,
            title="Some news",
            body="Short content",
        )
        assert score < 0.5

    def test_spam_lowers_score(self) -> None:
        scorer = CredibilityScorer()
        clean = scorer.score(0.7, "Bitcoin analysis", "Detailed analysis...")
        spammy = scorer.score(0.7, "BREAKING!!! Bitcoin CRASHES!!!", "")
        assert clean > spammy

    def test_score_in_range(self) -> None:
        scorer = CredibilityScorer()
        for source_cred in [0.0, 0.3, 0.5, 0.8, 1.0]:
            score = scorer.score(source_cred, "Test title", "Some body content here")
            assert 0.0 <= score <= 1.0


# ──────────────────────────────────────────────
# Priority Composer
# ──────────────────────────────────────────────

class TestScoreBundle:
    def test_defaults(self) -> None:
        bundle = ScoreBundle()
        assert bundle.keyword_score == 0.0
        assert bundle.recency_score == 1.0
        assert bundle.novelty_score == 1.0


class TestPriorityComposer:
    def test_high_all_scores_critical(self) -> None:
        composer = PriorityComposer()
        bundle = ScoreBundle(
            keyword_score=1.0,
            relevance_score=1.0,
            impact_score=1.0,
            recency_score=1.0,
            credibility_score=1.0,
            novelty_score=1.0,
        )
        priority = composer.classify(bundle)
        assert priority == DocumentPriority.CRITICAL

    def test_zero_scores_noise(self) -> None:
        composer = PriorityComposer()
        bundle = ScoreBundle(
            keyword_score=0.0,
            relevance_score=0.0,
            impact_score=0.0,
            recency_score=0.0,
            credibility_score=0.0,
            novelty_score=0.0,
        )
        priority = composer.classify(bundle)
        assert priority == DocumentPriority.NOISE

    def test_medium_scores_medium_priority(self) -> None:
        composer = PriorityComposer()
        bundle = ScoreBundle(
            keyword_score=0.5,
            relevance_score=0.5,
            impact_score=0.3,
            recency_score=0.7,
            credibility_score=0.5,
            novelty_score=0.5,
        )
        priority = composer.classify(bundle)
        assert priority in (DocumentPriority.MEDIUM, DocumentPriority.LOW, DocumentPriority.HIGH)

    def test_composite_score_in_range(self) -> None:
        composer = PriorityComposer()
        for vals in [(0.0,) * 6, (1.0,) * 6, (0.5,) * 6]:
            bundle = ScoreBundle(*vals)
            score = composer.compute_score(bundle)
            assert 0.0 <= score <= 1.0

    def test_classify_with_score_returns_tuple(self) -> None:
        composer = PriorityComposer()
        bundle = ScoreBundle(keyword_score=0.8, impact_score=0.9)
        priority, score = composer.classify_with_score(bundle)
        assert isinstance(priority, DocumentPriority)
        assert 0.0 <= score <= 1.0

    def test_priority_thresholds(self) -> None:
        composer = PriorityComposer(
            w_keyword=1.0,
            w_relevance=0.0,
            w_impact=0.0,
            w_recency=0.0,
            w_credibility=0.0,
            w_novelty=0.0,
        )
        # With only keyword weight, score == keyword_score directly
        assert composer.classify(ScoreBundle(keyword_score=0.85)) == DocumentPriority.CRITICAL
        assert composer.classify(ScoreBundle(keyword_score=0.65)) == DocumentPriority.HIGH
        assert composer.classify(ScoreBundle(keyword_score=0.45)) == DocumentPriority.MEDIUM
        assert composer.classify(ScoreBundle(keyword_score=0.25)) == DocumentPriority.LOW
        assert composer.classify(ScoreBundle(keyword_score=0.05)) == DocumentPriority.NOISE
