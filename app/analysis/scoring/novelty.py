"""
Novelty Scorer
==============
Estimates how novel a document is relative to recently seen content.

Approach:
- Content-hash exact match → 0.0 (seen before)
- Title Jaccard similarity against seen titles → decaying score
- No seen content → 1.0 (fully novel)

This is rule-based and operates purely in memory per session.
For cross-session novelty, integrate with DocumentRepository.
"""

from __future__ import annotations

import re


def _title_tokens(title: str) -> frozenset[str]:
    """Tokenize title into a set of lowercase alphanum words."""
    return frozenset(re.sub(r"[^a-z0-9 ]", "", title.lower()).split())


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class NoveltyScorer:
    """
    In-session novelty estimator.

    Lower score = more similar to previously seen content.
    Score 1.0 = no overlap with anything seen.

    Args:
        similarity_threshold: Jaccard threshold above which content is "seen" (default: 0.70)
    """

    def __init__(self, similarity_threshold: float = 0.70) -> None:
        self._threshold = similarity_threshold
        self._seen_hashes: set[str] = set()
        self._seen_title_tokens: list[frozenset[str]] = []

    def score(self, content_hash: str, title: str) -> float:
        """
        Compute novelty score [0.0, 1.0] for a document.
        1.0 = fully novel, 0.0 = exact duplicate.
        Does NOT register the document — call register() to track it.
        """
        if content_hash and content_hash in self._seen_hashes:
            return 0.0

        if title:
            tokens = _title_tokens(title)
            max_sim = max(
                (_jaccard(tokens, seen) for seen in self._seen_title_tokens),
                default=0.0,
            )
            if max_sim >= self._threshold:
                return max(0.0, 1.0 - max_sim)
            return round(1.0 - max_sim * 0.5, 4)

        return 1.0

    def register(self, content_hash: str, title: str) -> None:
        """Register a document as seen."""
        if content_hash:
            self._seen_hashes.add(content_hash)
        if title:
            self._seen_title_tokens.append(_title_tokens(title))

    def score_and_register(self, content_hash: str, title: str) -> float:
        """Score then register atomically."""
        s = self.score(content_hash, title)
        self.register(content_hash, title)
        return s

    def reset(self) -> None:
        self._seen_hashes.clear()
        self._seen_title_tokens.clear()
