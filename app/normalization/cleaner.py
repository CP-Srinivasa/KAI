"""Content cleaning and normalization helpers.

All functions are pure / side-effect-free.
normalize_url and normalize_title are the canonical inputs for deduplication.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ── HTML cleaning ─────────────────────────────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(text: str | None) -> str | None:
    """Strip HTML tags and collapse whitespace."""
    if not text:
        return None
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


# ── URL normalization ─────────────────────────────────────────────────────────

# Tracking / analytics params that carry no content identity signal.
# Stripped before hashing so the same article with different UTM links deduplicates.
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        # Google / Meta / Microsoft campaign params
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "utm_id",
        "utm_source_platform",
        "utm_creative_format",
        "utm_marketing_tactic",
        # Click-ID params from ad platforms
        "fbclid",
        "gclid",
        "msclkid",
        "twclid",
        "yclid",
        "ysclid",
        # Generic referrer helpers
        "ref",
        "referrer",
        "source",
        # Mailchimp
        "mc_cid",
        "mc_eid",
        # Google Analytics cross-domain
        "_ga",
        "_gl",
    }
)


def normalize_url(url: str) -> str:
    """Canonical URL form for deduplication comparison.

    Changes applied:
    - lowercase scheme and host
    - strip 'www.' prefix
    - strip trailing slash from path
    - remove fragment (#)
    - remove tracking/analytics query params (UTM, fbclid, etc.)
    - sort remaining query params (so ?b=2&a=1 == ?a=1&b=2)
    """
    try:
        parsed = urlparse(url.strip())
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        # Filter and sort query params
        if parsed.query:
            params = parse_qsl(parsed.query, keep_blank_values=True)
            params = [(k, v) for k, v in params if k.lower() not in _TRACKING_PARAMS]
            params.sort()
            query = urlencode(params)
        else:
            query = ""

        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=netloc,
            path=parsed.path.rstrip("/"),
            query=query,
            fragment="",
        )
        return urlunparse(normalized)
    except Exception:
        return url.strip().lower()


# ── Title normalization ───────────────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_title(title: str) -> str:
    """Normalize a title for deduplication comparison.

    Changes applied:
    - Unicode NFKD decomposition → convert accented chars to ASCII equivalents
    - Lowercase
    - Strip punctuation (keeps word characters and spaces)
    - Collapse whitespace

    Note: source suffixes like "- Reuters" or "| CoinDesk" are NOT stripped
    automatically to remain conservative (different publishers may have genuinely
    different content under the same headline).
    """
    # NFKD → drop combining characters (accents, diacritics)
    text = unicodedata.normalize("NFKD", title)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# ── Hashing ───────────────────────────────────────────────────────────────────


def title_hash(title: str) -> str:
    """SHA-256 of the normalized title — used as a dedup signal."""
    return hashlib.sha256(normalize_title(title).encode()).hexdigest()


def content_hash(url: str, title: str, text: str | None = None) -> str:
    """Stable SHA-256 over normalized url + normalized title + raw text.

    The text is not normalized here — whitespace differences in body text
    are treated as distinct content.
    """
    raw = f"{normalize_url(url)}|{normalize_title(title)}|{text or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()
