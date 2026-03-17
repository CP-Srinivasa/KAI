"""Keyword engine data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KeywordHit:
    """A single matched keyword or alias within a document."""

    match_string: str
    """The actual string that triggered the match (e.g. 'CZ')."""

    canonical_name: str | None = None
    """The canonical entity name if an alias matched (e.g. 'Changpeng Zhao')."""

    frequency: int = 1
    """How many times this exact match was found."""

    locations: set[str] = field(default_factory=set)
    """Where this match was found (e.g. 'title', 'body', 'tags')."""

    @property
    def display_name(self) -> str:
        """Returns the canonical name if available, otherwise the match string."""
        return self.canonical_name or self.match_string


@dataclass(frozen=True)
class KeywordMatchResult:
    """Result of running a document against the Keyword Engine."""

    hits: list[KeywordHit] = field(default_factory=list)
    """All distinct keyword matches found."""

    total_score: float = 0.0
    """Relevance score from hit frequencies and locations (title matches weigh more)."""

    @property
    def hit_count(self) -> int:
        return sum(hit.frequency for hit in self.hits)

    @property
    def matched_keywords(self) -> set[str]:
        """A flat set of all normalized canonical/matched keywords."""
        return {hit.display_name for hit in self.hits}
