"""Basic content cleaning and normalization helpers."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(text: str | None) -> str | None:
    """Strip HTML tags and collapse whitespace."""
    if not text:
        return None
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication comparison.

    Lowercases scheme and host, strips trailing slash from path,
    removes fragment. Query params are kept (some feeds use them as IDs).
    """
    try:
        parsed = urlparse(url.strip())
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            path=parsed.path.rstrip("/"),
            fragment="",
        )
        return urlunparse(normalized)
    except Exception:
        return url.strip().lower()


def normalize_title(title: str) -> str:
    """Normalize a title for comparison (lowercase, collapsed whitespace)."""
    return _WHITESPACE_RE.sub(" ", title.lower().strip())


def content_hash(url: str, title: str, text: str | None = None) -> str:
    """Compute a stable SHA-256 hash over url + title + text."""
    raw = f"{normalize_url(url)}|{normalize_title(title)}|{text or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()
