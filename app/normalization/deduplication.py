"""Conservative deduplicator with explicit duplicate scoring."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.domain.document import CanonicalDocument
from app.normalization.cleaner import content_hash, normalize_url, title_hash

_SCORE_URL_MATCH: float = 1.0
_SCORE_CONTENT_HASH: float = 1.0
_SCORE_TITLE_HASH: float = 0.85


@dataclass(frozen=True)
class DuplicateScore:
    score: float
    is_duplicate: bool
    reasons: list[str] = field(default_factory=list)


class Deduplicator:
    """In-memory, session-scoped deduplicator."""

    def __init__(self, threshold: float = 1.0) -> None:
        if not (0.0 < threshold <= 1.0):
            raise ValueError(f"threshold must be in (0.0, 1.0], got {threshold}")
        self._threshold = threshold
        self._seen_urls: set[str] = set()
        self._seen_hashes: set[str] = set()
        self._seen_title_hashes: set[str] = set()

    def score(self, doc: CanonicalDocument) -> DuplicateScore:
        reasons: list[str] = []
        max_score: float = 0.0

        url = normalize_url(doc.url)
        if url in self._seen_urls:
            reasons.append("url_match")
            max_score = max(max_score, _SCORE_URL_MATCH)

        ch = content_hash(doc.url, doc.title, doc.raw_text)
        if ch in self._seen_hashes:
            reasons.append("content_hash")
            max_score = max(max_score, _SCORE_CONTENT_HASH)

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
        return self.score(doc).is_duplicate

    def register(self, doc: CanonicalDocument) -> None:
        self._seen_urls.add(normalize_url(doc.url))
        self._seen_hashes.add(content_hash(doc.url, doc.title, doc.raw_text))
        self._seen_title_hashes.add(title_hash(doc.title))

    def filter(
        self,
        documents: list[CanonicalDocument],
    ) -> list[CanonicalDocument]:
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
        results: list[tuple[CanonicalDocument, DuplicateScore]] = []
        for doc in documents:
            s = self.score(doc)
            results.append((doc, s))
            if not s.is_duplicate:
                self.register(doc)
        return results

    def reset(self) -> None:
        self._seen_urls.clear()
        self._seen_hashes.clear()
        self._seen_title_hashes.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen_urls)

    @property
    def threshold(self) -> float:
        return self._threshold

