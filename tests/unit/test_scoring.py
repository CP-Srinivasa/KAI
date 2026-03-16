"""Tests for Document Scoring."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.analysis.scoring.ranker import (
    DocumentScorer, engagement_score, keyword_match_score, recency_score,
)
from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceType


def make_doc(title: str = "Test", minutes_ago: int = 60) -> CanonicalDocument:
    return CanonicalDocument(
        source_id="test", source_name="Test", source_type=SourceType.RSS_FEED,
        url="https://example.com/test", title=title,
        published_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
    )


class TestRecencyScore:
    def test_very_recent(self) -> None:
        assert recency_score(datetime.utcnow() - timedelta(minutes=5)) > 0.95

    def test_one_day_old(self) -> None:
        score = recency_score(datetime.utcnow() - timedelta(hours=24), half_life_hours=24.0)
        assert 0.35 < score < 0.40

    def test_none_returns_low(self) -> None:
        assert recency_score(None) < 0.01


class TestKeywordMatchScore:
    def test_all_match(self) -> None:
        assert keyword_match_score("bitcoin ethereum defi", ["bitcoin", "ethereum", "defi"]) == 1.0

    def test_no_match(self) -> None:
        assert keyword_match_score("dogecoin news", ["bitcoin", "ethereum"]) == 0.0

    def test_empty_keywords(self) -> None:
        assert keyword_match_score("some text", []) == 0.0


class TestEngagementScore:
    def test_zero(self) -> None:
        assert engagement_score(0, 0) == 0.0

    def test_high(self) -> None:
        assert engagement_score(100000, 0) == 1.0


class TestDocumentScorer:
    def test_score_range(self) -> None:
        scorer = DocumentScorer(watched_keywords=["bitcoin"])
        score = scorer.score(make_doc("Bitcoin update", minutes_ago=30), source_credibility=0.8)
        assert 0.0 <= score <= 1.0

    def test_rank_orders_by_score(self) -> None:
        scorer = DocumentScorer(watched_keywords=["bitcoin", "defi"])
        recent = make_doc("Bitcoin DeFi news", minutes_ago=5)
        old = make_doc("Old unrelated story", minutes_ago=10000)
        ranked = scorer.rank([old, recent])
        assert ranked[0][0].title == recent.title
