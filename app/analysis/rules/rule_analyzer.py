"""Rule-based document analyzer — deterministic, no LLM.

Produces an AnalysisResult using only keyword matching and heuristics.
Always runs first in the pipeline — LLM is optional and layered on top.

Fills reliably:
  relevance_score     — keyword hit rate (title weighted 3x)
  market_scope        — inferred from asset and keyword presence
  affected_assets     — detected crypto/equity tickers
  confidence_score    — always 1.0 (rules are deterministic)
  tags                — top matched keywords (max 20)

Does NOT fill (needs LLM):
  sentiment_label / sentiment_score (defaults to NEUTRAL / 0.0)
  impact_score (defaults to 0.0)
  novelty_score (defaults to 0.5)
  explanation_short / explanation_long (defaults to placeholders)
  actionable (defaults to False)

Spam detection: compute_spam_probability() is available as a standalone function.
Spam probability is not stored on AnalysisResult — pass it separately to compute_priority().
"""

from __future__ import annotations

import re
from uuid import UUID

from app.analysis.rules.asset_detector import AssetMatch, detect_assets
from app.analysis.rules.keyword_matcher import KeywordMatch, KeywordMatcher
from app.core.domain.document import AnalysisResult
from app.core.enums import MarketScope, SentimentLabel

# Keywords that signal each market scope
_CRYPTO_SIGNALS = frozenset(
    {
        "bitcoin",
        "ethereum",
        "crypto",
        "blockchain",
        "defi",
        "nft",
        "web3",
        "staking",
        "halving",
        "altcoin",
        "kryptowährung",
        "cryptocurrency",
        "btc",
        "eth",
        "solana",
        "xrp",
    }
)
_MACRO_SIGNALS = frozenset(
    {
        "inflation",
        "zinssätze",
        "interest rate",
        "federal reserve",
        "fed",
        "geldpolitik",
        "cpi",
        "gdp",
        "recession",
        "central bank",
        "ecb",
        "ezb",
    }
)
_EQUITIES_SIGNALS = frozenset(
    {
        "aktien",
        "aktienmarkt",
        "nyse",
        "nasdaq",
        "dax",
        "s&p",
        "earnings",
        "ipo",
        "etf",
        "stocks",
        "equities",
    }
)

# Spam heuristics
_ALL_CAPS_RE = re.compile(r"^[A-Z0-9\s!?.,]{10,}$")
_EXCESSIVE_PUNCT_RE = re.compile(r"[!?]{3,}")


def compute_spam_probability(title: str, text: str | None) -> float:
    score = 0.0
    combined = title + (text or "")
    word_count = len(combined.split())

    if word_count < 10:
        score += 0.3  # suspiciously short
    if _ALL_CAPS_RE.match(title):
        score += 0.3  # shouting title
    if _EXCESSIVE_PUNCT_RE.search(title):
        score += 0.2  # clickbait punctuation
    if word_count > 0 and len(title) / max(word_count, 1) > 30:
        score += 0.1  # very long title relative to content

    return min(score, 1.0)


def _relevance_score(
    keyword_matches: list[KeywordMatch],
    total_keywords: int,
) -> float:
    """Compute relevance score from keyword hits.

    Formula: weighted hit count normalized to [0, 1].
    Title matches count 3x, text matches 1x.
    Cap at 1.0 — more hits don't help beyond saturation.
    """
    if total_keywords == 0 or not keyword_matches:
        return 0.0

    weighted = sum((3 if m.in_title else 0) + (1 if m.in_text else 0) for m in keyword_matches)
    # Saturation: ~10 weighted hits → relevance 1.0
    return min(weighted / 10.0, 1.0)


def _market_scope(
    keyword_matches: list[KeywordMatch],
    asset_matches: list[AssetMatch],
) -> MarketScope:
    kws_lower = {m.keyword.lower() for m in keyword_matches}
    assets = {m.canonical for m in asset_matches}

    crypto_score = len(kws_lower & _CRYPTO_SIGNALS) + (2 if assets else 0)
    macro_score = len(kws_lower & _MACRO_SIGNALS)
    equities_score = len(kws_lower & _EQUITIES_SIGNALS)

    if crypto_score == 0 and macro_score == 0 and equities_score == 0:
        return MarketScope.UNKNOWN

    scores = {
        MarketScope.CRYPTO: crypto_score,
        MarketScope.MACRO: macro_score,
        MarketScope.EQUITIES: equities_score,
    }
    top = max(scores, key=lambda k: scores[k])
    second = sorted(scores.values(), reverse=True)[1]

    # Mixed if two scopes are close
    if scores[top] > 0 and second >= scores[top] * 0.7:
        return MarketScope.MIXED
    return top


class RuleAnalyzer:
    """Deterministic rule-based document analyzer.

    Requires a KeywordMatcher with keywords loaded from monitor/keywords.txt.
    """

    def __init__(self, keyword_matcher: KeywordMatcher) -> None:
        self._matcher = keyword_matcher

    def analyze(self, document_id: UUID, title: str, text: str | None) -> AnalysisResult:
        """Analyze title + text and return a rule-based AnalysisResult."""
        keyword_matches = self._matcher.match(title, text)
        asset_matches = detect_assets(title, text)

        relevance = _relevance_score(keyword_matches, self._matcher.keyword_count)
        scope = _market_scope(keyword_matches, asset_matches)

        # Top 20 matched keyword strings → tags
        top_keywords = [m.keyword for m in keyword_matches[:20]]

        return AnalysisResult(
            document_id=str(document_id),
            # Sentiment: unknown at rule level → neutral defaults
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            # Known from rules
            relevance_score=round(relevance, 4),
            confidence_score=1.0,  # rules are deterministic
            # Unknown without LLM → conservative defaults
            impact_score=0.0,
            novelty_score=0.5,
            # Context
            market_scope=scope,
            affected_assets=[m.canonical for m in asset_matches],
            tags=top_keywords,
            explanation_short="Rule-based analysis",
            explanation_long="",
            actionable=False,
        )
