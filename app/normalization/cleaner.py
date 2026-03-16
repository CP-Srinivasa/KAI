"""
Text Cleaner / Normalizer
==========================
Cleans and normalizes raw text from ingested documents.

Design:
- Pure functions, no side effects
- No ML dependencies in this module (use langdetect separately)
- All functions accept str and return str
"""

from __future__ import annotations

import re
import unicodedata


# Regex patterns compiled once at module load
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")
_MULTIPLE_NEWLINES = re.compile(r"\n{3,}")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return _HTML_TAG.sub(" ", text)


def normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace chars to a single space."""
    return _WHITESPACE.sub(" ", text).strip()


def remove_control_chars(text: str) -> str:
    """Remove non-printable control characters."""
    return _CONTROL_CHARS.sub("", text)


def normalize_unicode(text: str) -> str:
    """Normalize unicode to NFC form (composed characters)."""
    return unicodedata.normalize("NFC", text)


def remove_urls(text: str) -> str:
    """Remove URLs from text (optional, for analysis purposes)."""
    return _URL_PATTERN.sub(" ", text)


def truncate(text: str, max_chars: int = 10000) -> str:
    """Truncate text to max_chars, breaking at word boundary."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    return truncated[:last_space].rstrip() + "…" if last_space > 0 else truncated + "…"


def clean_text(raw: str, strip_urls: bool = False, max_chars: int = 10000) -> str:
    """
    Full cleaning pipeline for ingested text:
    1. Normalize unicode
    2. Strip HTML tags
    3. Remove control characters
    4. Optionally remove URLs
    5. Normalize whitespace
    6. Truncate if needed
    """
    if not raw:
        return ""
    text = normalize_unicode(raw)
    text = strip_html(text)
    text = remove_control_chars(text)
    if strip_urls:
        text = remove_urls(text)
    text = normalize_whitespace(text)
    text = truncate(text, max_chars)
    return text


def extract_title_from_text(text: str, max_len: int = 200) -> str:
    """
    Extract a pseudo-title from raw text (first non-empty line).
    Fallback for sources without explicit title fields.
    """
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 10:
            return line[:max_len]
    return ""


def normalize_title(title: str) -> str:
    """
    Normalize a title for comparison purposes.
    Lowercases, removes punctuation, normalizes whitespace.
    """
    title = normalize_unicode(title.lower())
    title = re.sub(r"[^\w\s]", " ", title)
    return normalize_whitespace(title)
