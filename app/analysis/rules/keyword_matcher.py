"""Keyword matcher for rule-based document analysis.

Loads keywords from monitor/keywords.txt and performs whole-word,
case-insensitive matching against document title and text.

Design decisions:
- Each keyword gets its own compiled pattern (compiled once at init).
- Single-word keywords use \\b word boundaries to avoid partial matches
  (e.g. "BTC" does not match "BTCusdt").
- Multi-word phrases and keywords with special chars use direct substring
  matching — the phrase itself acts as a natural boundary.
- Order: longer keywords are matched first to prefer specific phrases over
  substrings (e.g. "Smart Contract" before "Contract").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_WORD_RE = re.compile(r"^\w+$")


@dataclass(frozen=True)
class KeywordMatch:
    keyword: str
    in_title: bool
    in_text: bool
    count: int  # total occurrences across title + text


@dataclass
class KeywordMatcher:
    """Case-insensitive, whole-word keyword matcher.

    Build from a file:
        matcher = KeywordMatcher.from_file(Path("monitor/keywords.txt"))

    Or directly:
        matcher = KeywordMatcher(keywords=frozenset({"Bitcoin", "ETH"}))
    """

    keywords: frozenset[str]
    _patterns: dict[str, re.Pattern[str]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        # Compile sorted by length descending (prefer longer/more specific first)
        for kw in sorted(self.keywords, key=len, reverse=True):
            escaped = re.escape(kw)
            if _WORD_RE.match(kw):
                # Single-word: enforce word boundaries
                pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE | re.UNICODE)
            else:
                # Phrase or contains special chars: direct substring
                pattern = re.compile(escaped, re.IGNORECASE | re.UNICODE)
            self._patterns[kw] = pattern

    @classmethod
    def from_file(cls, path: Path) -> KeywordMatcher:
        """Load keywords from a .txt file (one per line, # = comment)."""
        keywords: set[str] = set()
        if not path.exists():
            return cls(keywords=frozenset())
        for line in path.read_text(encoding="utf-8").splitlines():
            kw = line.strip()
            if kw and not kw.startswith("#"):
                keywords.add(kw)
        return cls(keywords=frozenset(keywords))

    def match(self, title: str, text: str | None = None) -> list[KeywordMatch]:
        """Return all keywords found in title and/or text."""
        matches: list[KeywordMatch] = []
        text = text or ""

        for kw, pat in self._patterns.items():
            title_hits = len(pat.findall(title))
            text_hits = len(pat.findall(text))
            total = title_hits + text_hits
            if total > 0:
                matches.append(
                    KeywordMatch(
                        keyword=kw,
                        in_title=title_hits > 0,
                        in_text=text_hits > 0,
                        count=total,
                    )
                )

        # Sort: title matches first, then by count descending
        return sorted(matches, key=lambda m: (not m.in_title, -m.count))

    @property
    def keyword_count(self) -> int:
        return len(self.keywords)
