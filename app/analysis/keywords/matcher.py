"""Core keyword matching engine."""

from __future__ import annotations

import re

from app.analysis.keywords.models import KeywordHit, KeywordMatchResult
from app.core.domain.document import CanonicalDocument


class KeywordMatcher:
    """Matches text against a set of keywords and aliases using word boundaries.

    Features:
    - Case-insensitive matching.
    - Word boundary adherence (so 'bot' doesn't match 'bottle').
    - Alias resolution ('CZ' -> 'Changpeng Zhao').
    - Title-priority hits (higher score for title matches vs body).
    """

    # Weights used for calculating a simple aggregate score
    WEIGHT_TITLE = 3.0
    WEIGHT_TAGS = 2.0
    WEIGHT_BODY = 1.0

    def __init__(self, keywords: set[str], alias_map: dict[str, str]):
        self.keywords = keywords
        self.alias_map = alias_map
        self._pattern: re.Pattern[str] | None = None

        # We need a unified list of all target strings (keywords + alias keys)
        self._all_targets = set(self.keywords).union(set(self.alias_map.keys()))

        # Sort targets by length descending. This prevents partial overlap matches.
        # e.g., if we look for "Bitcoin" and "Bitcoin Cash", we want "Bitcoin Cash"
        # to match first so it doesn't just trigger "Bitcoin" and leave "Cash" behind.
        self._sorted_targets = sorted(self._all_targets, key=len, reverse=True)

        # Build a massive regex chunk: \b(term1|term2|...)\b
        # Python's re engine can handle surprisingly large OR expressions.
        # Note: We must escape the targets as they might contain regex-special chars like @
        if self._sorted_targets:
            escaped_targets = [re.escape(t) for t in self._sorted_targets]
            # \b fails for @ prefixes (e.g. @cz_binance).
            # Use (?<!\w)(term)(?!\w) as a broader word boundary.
            pattern = r"(?<!\w)(" + "|".join(escaped_targets) + r")(?!\w)"
            self._pattern = re.compile(pattern, re.IGNORECASE)

    def match(self, document: CanonicalDocument) -> KeywordMatchResult:
        """Scan a CanonicalDocument for all registered keywords and aliases."""
        if not self._pattern:
            return KeywordMatchResult()

        # Temporary structured accumulator: { canonical_name_or_keyword: KeywordHit }
        hit_map: dict[str, KeywordHit] = {}

        def _add_hit(match_str: str, location: str, count: int = 1) -> None:
            match_lower = match_str.lower()

            # Resolve to canonical if available, else use original lowercase match
            canonical = self.alias_map.get(match_lower)
            key = canonical if canonical else match_lower

            if key in hit_map:
                existing = hit_map[key]
                hit_map[key] = KeywordHit(
                    match_string=existing.match_string,  # first matched string wins
                    canonical_name=existing.canonical_name,
                    frequency=existing.frequency + count,
                    locations=existing.locations | {location},
                )
            else:
                hit_map[key] = KeywordHit(
                    match_string=match_lower,
                    canonical_name=canonical,
                    frequency=count,
                    locations={location},
                )

        # 1. Search Title
        if document.title:
            for match in self._pattern.finditer(document.title):
                _add_hit(match.group(1), "title")

        # 2. Search Body
        body_text = document.cleaned_text or document.raw_text or ""
        if body_text:
            for match in self._pattern.finditer(body_text):
                _add_hit(match.group(1), "body")

        # 3. Search Tags / Categories
        tags_text = " ".join(document.tags + document.categories)
        if tags_text:
            for match in self._pattern.finditer(tags_text):
                _add_hit(match.group(1), "tags")

        # Compile final Result and calculate a total_score
        hits = list(hit_map.values())
        total_score = self._calculate_score(hits)

        return KeywordMatchResult(hits=hits, total_score=total_score)

    def _calculate_score(self, hits: list[KeywordHit]) -> float:
        """Calculates a simple arbitrary relevance score based on hit location."""
        score = 0.0
        for hit in hits:
            # Base score per distinct hit
            base = 1.0

            # Multipliers based on where it was found
            if "title" in hit.locations:
                base *= self.WEIGHT_TITLE
            if "tags" in hit.locations:
                base *= self.WEIGHT_TAGS
            if "body" in hit.locations:
                base *= self.WEIGHT_BODY

            # Logarithmic frequency scaling — prevents 100x "Bitcoin" from dominating
            import math

            freq_mult = 1.0 + math.log1p(hit.frequency)

            score += base * freq_mult

        return round(score, 2)
