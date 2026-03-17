"""Conservative deduplicator — prefers false negatives over false positives.

Two documents are considered duplicates if they share:
- the same normalized URL, OR
- the same content hash (url + title + raw_text)
"""

from __future__ import annotations

from app.core.domain.document import CanonicalDocument
from app.normalization.cleaner import content_hash, normalize_url


class Deduplicator:
    def __init__(self) -> None:
        self._seen_urls: set[str] = set()
        self._seen_hashes: set[str] = set()

    def is_duplicate(self, doc: CanonicalDocument) -> bool:
        url = normalize_url(doc.url)
        if url in self._seen_urls:
            return True
        h = content_hash(doc.url, doc.title, doc.raw_text)
        if h in self._seen_hashes:
            return True
        return False

    def register(self, doc: CanonicalDocument) -> None:
        self._seen_urls.add(normalize_url(doc.url))
        self._seen_hashes.add(content_hash(doc.url, doc.title, doc.raw_text))

    def filter(self, documents: list[CanonicalDocument]) -> list[CanonicalDocument]:
        """Return only non-duplicate documents, registering each accepted one."""
        result = []
        for doc in documents:
            if not self.is_duplicate(doc):
                self.register(doc)
                result.append(doc)
        return result

    def reset(self) -> None:
        self._seen_urls.clear()
        self._seen_hashes.clear()
