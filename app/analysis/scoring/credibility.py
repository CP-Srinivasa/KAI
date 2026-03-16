"""
Credibility Scorer
==================
Estimates document credibility from source and content signals.

Rules (weighted combination):
- Source credibility score (from news_domains.txt / SourceEntry)
- Title quality signals (not all-caps, reasonable length)
- Spam/clickbait patterns (excessive punctuation, superlatives)
- Content length (very short ≈ lower credibility)

Returns a score in [0.0, 1.0].
"""

from __future__ import annotations

import re

# Clickbait/spam signal patterns
_CLICKBAIT_PATTERNS = [
    re.compile(r"\b(you won't believe|shocking|incredible|amazing|mind-?blowing)\b", re.I),
    re.compile(r"\b(BREAKING|URGENT|ALERT)\b"),
    re.compile(r"[!?]{2,}"),          # Multiple !!! or ???
    re.compile(r"[A-Z]{5,}"),         # ALLCAPS words of 5+ chars
]

_SUPERLATIVE_PATTERN = re.compile(
    r"\b(biggest|largest|fastest|greatest|best ever|worst ever|all.time high|all.time low)\b", re.I
)


def _spam_penalty(title: str, body: str) -> float:
    """
    Returns a penalty [0.0, 0.3] based on spam signals.
    0.0 = no spam signals detected.
    """
    text = f"{title} {body[:500]}"
    penalty = 0.0
    for pattern in _CLICKBAIT_PATTERNS:
        if pattern.search(text):
            penalty += 0.05
    if _SUPERLATIVE_PATTERN.search(text):
        penalty += 0.05
    return min(0.30, penalty)


def _title_quality(title: str) -> float:
    """
    Score title quality [0.5, 1.0].
    Penalizes very short (<10 chars) or very long (>300 chars) titles.
    """
    if not title:
        return 0.5
    length = len(title)
    if length < 10:
        return 0.5
    if length > 300:
        return 0.7
    # Check if title is all uppercase (low quality signal)
    if title == title.upper() and any(c.isalpha() for c in title):
        return 0.6
    return 1.0


def _content_length_score(body: str) -> float:
    """Score based on content length. Very short content is less credible."""
    length = len(body.strip())
    if length < 50:
        return 0.3
    if length < 150:
        return 0.6
    if length < 500:
        return 0.8
    return 1.0


class CredibilityScorer:
    """
    Combines source credibility with content quality signals.

    Args:
        weight_source:   Weight for the source's base credibility score
        weight_content:  Weight for content quality signals
    """

    def __init__(
        self,
        weight_source: float = 0.65,
        weight_content: float = 0.35,
    ) -> None:
        self._w_source = weight_source
        self._w_content = weight_content

    def score(
        self,
        source_credibility: float,
        title: str = "",
        body: str = "",
    ) -> float:
        """
        Compute credibility score [0.0, 1.0].

        Args:
            source_credibility: Base credibility of the source (0.0–1.0, from registry)
            title:              Document title
            body:               Document body text (cleaned preferred)
        """
        content_score = (
            _title_quality(title) * 0.4
            + _content_length_score(body) * 0.6
        )
        penalty = _spam_penalty(title, body)
        raw = (
            self._w_source * source_credibility
            + self._w_content * content_score
        ) - penalty
        return round(max(0.0, min(1.0, raw)), 4)
