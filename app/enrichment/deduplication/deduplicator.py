"""
Document Deduplication
======================
Detects duplicate documents across sources using:
1. Exact content hash
2. Normalized URL matching
3. Title Jaccard similarity (conservative threshold)

Philosophy: prefer false negatives — never suppress unless confident.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from app.core.domain.document import CanonicalDocument
from app.core.logging import get_logger

logger = get_logger(__name__)


def normalize_url(url: str) -> str:
    """Normalize URL: lowercase, remove tracking params, strip fragment."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip().lower())
        query = "&".join(
            p for p in parsed.query.split("&")
            if not p.startswith(("utm_", "ref=", "source="))
        ) if parsed.query else ""
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.params, query, ""))
    except Exception:
        return url


def title_tokens(title: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", "", title.lower()).split())


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class DocumentDeduplicator:
    """In-memory dedup store for a processing session."""

    def __init__(self, title_similarity_threshold: float = 0.85) -> None:
        self.title_similarity_threshold = title_similarity_threshold
        self._seen_hashes: set[str] = set()
        self._seen_urls: set[str] = set()
        self._seen_titles: list[tuple[set[str], str]] = []

    def is_duplicate(self, document: CanonicalDocument) -> tuple[bool, str]:
        if document.content_hash and document.content_hash in self._seen_hashes:
            return True, "exact_hash_match"
        norm_url = normalize_url(document.url)
        if norm_url and norm_url in self._seen_urls:
            return True, "url_match"
        if document.title:
            tokens = title_tokens(document.title)
            for seen_tokens, _ in self._seen_titles:
                sim = jaccard_similarity(tokens, seen_tokens)
                if sim >= self.title_similarity_threshold:
                    return True, f"title_similarity:{sim:.2f}"
        return False, ""

    def register(self, document: CanonicalDocument) -> None:
        if document.content_hash:
            self._seen_hashes.add(document.content_hash)
        norm_url = normalize_url(document.url)
        if norm_url:
            self._seen_urls.add(norm_url)
        if document.title:
            self._seen_titles.append((title_tokens(document.title), str(document.id)))

    def process_batch(
        self, documents: list[CanonicalDocument]
    ) -> tuple[list[CanonicalDocument], list[CanonicalDocument]]:
        unique: list[CanonicalDocument] = []
        duplicates: list[CanonicalDocument] = []
        for doc in documents:
            is_dup, reason = self.is_duplicate(doc)
            if is_dup:
                doc.is_duplicate = True
                duplicates.append(doc)
            else:
                self.register(doc)
                unique.append(doc)
        logger.info("dedup_complete", total=len(documents), unique=len(unique), duplicates=len(duplicates))
        return unique, duplicates
