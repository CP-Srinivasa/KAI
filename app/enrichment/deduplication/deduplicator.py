"""Conservative deduplicator with explicit duplicate scoring.

Design principle: prefer false negatives over false positives.
A document must reach the configured score threshold to be flagged as duplicate.
Default threshold = 1.0 → only exact URL or content hash matches trigger dedup.
Lower the threshold (e.g. 0.85) to also catch title-only matches.

Scoring signals:
  1.0  url_match         — normalized URL already seen (strongest signal)
  1.0  content_hash      — same normalized_url + normalized_title + raw_text
  0.85 title_hash        — same normalized title, different URL
                           (same headline, possibly different source)

The score() method returns ALL matching signals so callers can decide
what to do (flag, log, merge, etc.) without re-running detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.domain.document import CanonicalDocument
from app.normalization.cleaner import content_hash, normalize_url, title_hash

# Score weights — must be in [0.0, 1.0]
_SCORE_URL_MATCH: float = 1.0
_SCORE_CONTENT_HASH: float = 1.0
_SCORE_TITLE_HASH: float = 0.85


@dataclass(frozen=True)
class DuplicateScore:
    """Result of duplicate detection for one document.

    score      — max signal score across all matched signals (0.0 = unique)
    is_duplicate — True when score >= threshold used by the Deduplicator
    reasons    — list of signal names that fired (e.g. ['url_match', 'title_hash'])
    """

    score: float
    is_duplicate: bool
    reasons: list[str] = field(default_factory=list)


class Deduplicator:
    """In-memory, session-scoped deduplicator.

    State is per-instance. Use one instance per ingestion run, or persist
    the seen sets externally for cross-session dedup.

    Args:
        threshold: minimum score to consider a document a duplicate.
                   Default 1.0 = only exact URL or content hash matches.
                   Use 0.85 to also catch title-based near-duplicates.
    """

    def __init__(self, threshold: float = 1.0) -> None:
        if not (0.0 < threshold <= 1.0):
            raise ValueError(f"threshold must be in (0.0, 1.0], got {threshold}")
        self._threshold = threshold
        self._seen_urls: set[str] = set()
        self._seen_hashes: set[str] = set()
        self._seen_title_hashes: set[str] = set()

    # ── Core API ──────────────────────────────────────────────────────────────

    def score(self, doc: CanonicalDocument) -> DuplicateScore:
        """Compute a duplicate score for a document without registering it.

        Safe to call multiple times — read-only, no state change.
        """
        reasons: list[str] = []
        max_score: float = 0.0

        # Signal 1: normalized URL match
        url = normalize_url(doc.url)
        if url in self._seen_urls:
            reasons.append("url_match")
            max_score = max(max_score, _SCORE_URL_MATCH)

        # Signal 2: content hash match (normalized url + normalized title + raw text)
        ch = content_hash(doc.url, doc.title, doc.raw_text)
        if ch in self._seen_hashes:
            reasons.append("content_hash")
            max_score = max(max_score, _SCORE_CONTENT_HASH)

        # Signal 3: title hash match (same normalized title, possibly different URL)
        th = title_hash(doc.title)
        if th in self._seen_title_hashes:
            reasons.append("title_hash")
            max_score = max(max_score, _SCORE_TITLE_HASH)

        return DuplicateScore(
            score=max_score,
            is_duplicate=max_score >= self._threshold,
            reasons=reasons,
        )

    def is_duplicate(self, doc: CanonicalDocument) -> bool:
        """Return True if doc matches any seen document above the threshold."""
        return self.score(doc).is_duplicate

    def register(self, doc: CanonicalDocument) -> None:
        """Add a document to the seen-set so future docs can be compared against it."""
        self._seen_urls.add(normalize_url(doc.url))
        self._seen_hashes.add(content_hash(doc.url, doc.title, doc.raw_text))
        self._seen_title_hashes.add(title_hash(doc.title))

    def filter(
        self,
        documents: list[CanonicalDocument],
    ) -> list[CanonicalDocument]:
        """Return only non-duplicate documents, registering each accepted one.

        Processes in order — first occurrence wins.
        """
        result: list[CanonicalDocument] = []
        for doc in documents:
            if not self.is_duplicate(doc):
                self.register(doc)
                result.append(doc)
        return result

    def filter_scored(
        self,
        documents: list[CanonicalDocument],
    ) -> list[tuple[CanonicalDocument, DuplicateScore]]:
        """Like filter(), but returns (doc, score) pairs for all input documents.

        Duplicates are included in the output but NOT registered.
        Useful for logging, auditing, or manual review.
        """
        results: list[tuple[CanonicalDocument, DuplicateScore]] = []
        for doc in documents:
            s = self.score(doc)
            results.append((doc, s))
            if not s.is_duplicate:
                self.register(doc)
        return results

    def reset(self) -> None:
        """Clear all seen state."""
        self._seen_urls.clear()
        self._seen_hashes.clear()
        self._seen_title_hashes.clear()

    # ── Inspection ────────────────────────────────────────────────────────────

    @property
    def seen_count(self) -> int:
        """Number of unique URLs currently registered."""
        return len(self._seen_urls)

    @property
    def threshold(self) -> float:
        return self._threshold
