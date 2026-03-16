"""
Keyword Matcher
===============
Rule-based keyword and entity matching against document text.

Features:
- Exact and substring keyword matching (case-insensitive)
- Alias group resolution (via entity_aliases.yml)
- Per-keyword configurable weights
- Match explanation with hit positions
- Watchlist entity detection
- Composite relevance score based on hits

Usage:
    matcher = KeywordMatcher(keywords=["bitcoin", "ethereum"], aliases=alias_groups)
    result = matcher.match(document)
    print(result.score, result.matched_keywords)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KeywordHit:
    """A single keyword match found in a document."""
    keyword: str               # The matched keyword (normalized form)
    matched_text: str          # The actual text that matched
    field: str                 # "title", "body", "url"
    canonical_name: str = ""   # Resolved canonical name (from alias group)
    weight: float = 1.0        # Keyword weight


@dataclass
class MatchResult:
    """Full result of matching a document against the keyword list."""
    matched_keywords: list[str] = field(default_factory=list)
    hits: list[KeywordHit] = field(default_factory=list)
    entity_hits: list[str] = field(default_factory=list)   # Matched watchlist entities
    score: float = 0.0                                       # [0.0, 1.0] composite score
    has_entity_hit: bool = False

    @property
    def matched_count(self) -> int:
        return len(self.matched_keywords)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_keywords": self.matched_keywords,
            "entity_hits": self.entity_hits,
            "matched_count": self.matched_count,
            "score": round(self.score, 4),
            "has_entity_hit": self.has_entity_hit,
            "hits": [
                {
                    "keyword": h.keyword,
                    "matched_text": h.matched_text,
                    "field": h.field,
                    "canonical_name": h.canonical_name,
                    "weight": h.weight,
                }
                for h in self.hits
            ],
        }


def _build_alias_map(alias_groups: list[dict[str, Any]]) -> dict[str, str]:
    """
    Build a flat lookup: alias_variant → canonical_name.

    alias_groups format (from entity_aliases.yml):
        - canonical: "Anthony Pompliano"
          aliases: ["Pomp", "APompliano", "Anthony Pompliano"]
    """
    alias_map: dict[str, str] = {}
    for group in alias_groups:
        canonical = group.get("canonical", "")
        if not canonical:
            continue
        aliases = group.get("aliases", [])
        for alias in aliases:
            alias_map[alias.lower()] = canonical
        alias_map[canonical.lower()] = canonical
    return alias_map


def _normalize_keyword(kw: str) -> str:
    return kw.strip().lower()


def _contains_word(text: str, keyword: str) -> bool:
    """
    Check if keyword appears in text (case-insensitive).
    For single words: requires word boundary.
    For phrases (multi-word): substring match.
    Both text and keyword are expected to be pre-lowercased by callers,
    but this function lowercases text defensively for correctness.
    """
    text = text.lower()
    if " " in keyword:
        return keyword in text
    # Word boundary check for single tokens
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


class KeywordMatcher:
    """
    Matches documents against a keyword list with alias resolution and weighted scoring.

    Args:
        keywords:       List of keywords/phrases to watch (case-insensitive)
        alias_groups:   Entity alias groups from entity_aliases.yml
        keyword_weights: Optional {keyword: weight} override (default weight=1.0)
        title_boost:    Score multiplier when keyword found in title (default: 2.0)
        max_score:      Clamp composite score to this maximum (default: 1.0)
    """

    def __init__(
        self,
        keywords: list[str] | None = None,
        alias_groups: list[dict[str, Any]] | None = None,
        keyword_weights: dict[str, float] | None = None,
        title_boost: float = 2.0,
        max_score: float = 1.0,
    ) -> None:
        self._keywords: list[str] = [_normalize_keyword(k) for k in (keywords or [])]
        self._alias_map = _build_alias_map(alias_groups or [])
        self._weights = {_normalize_keyword(k): v for k, v in (keyword_weights or {}).items()}
        self._title_boost = title_boost
        self._max_score = max_score

    def _get_weight(self, keyword: str) -> float:
        return self._weights.get(keyword, 1.0)

    def _resolve_alias(self, keyword: str) -> str:
        return self._alias_map.get(keyword, "")

    def match_text(
        self, title: str, body: str, url: str = ""
    ) -> MatchResult:
        """
        Match keywords against raw title + body + url strings.
        Returns a MatchResult with hits, score, and entity info.
        """
        title_lower = title.lower()
        body_lower = body.lower()
        url_lower = url.lower()

        hits: list[KeywordHit] = []
        matched_set: set[str] = set()
        entity_hits: set[str] = set()
        total_weighted_score = 0.0

        for kw in self._keywords:
            weight = self._get_weight(kw)
            canonical = self._resolve_alias(kw)
            hit_found = False
            hit_field = ""

            if _contains_word(title_lower, kw):
                hit_found = True
                hit_field = "title"
                # Title hits score with boost
                total_weighted_score += weight * self._title_boost
            elif _contains_word(body_lower, kw):
                hit_found = True
                hit_field = "body"
                total_weighted_score += weight
            elif url_lower and kw in url_lower:
                hit_found = True
                hit_field = "url"
                total_weighted_score += weight * 0.5

            if hit_found:
                matched_set.add(kw)
                hits.append(KeywordHit(
                    keyword=kw,
                    matched_text=kw,
                    field=hit_field,
                    canonical_name=canonical,
                    weight=weight,
                ))
                if canonical:
                    entity_hits.add(canonical)

        # Also check alias variants not in keyword list
        for alias_variant, canonical in self._alias_map.items():
            if alias_variant in matched_set:
                continue  # Already matched as keyword
            if _contains_word(title_lower, alias_variant) or _contains_word(body_lower, alias_variant):
                entity_hits.add(canonical)
                matched_set.add(alias_variant)
                hits.append(KeywordHit(
                    keyword=alias_variant,
                    matched_text=alias_variant,
                    field="title" if _contains_word(title_lower, alias_variant) else "body",
                    canonical_name=canonical,
                    weight=1.0,
                ))
                total_weighted_score += 1.0 * (
                    self._title_boost if _contains_word(title_lower, alias_variant) else 1.0
                )

        # Normalize score: divide by sum of all possible weights
        max_possible = sum(self._get_weight(k) * self._title_boost for k in self._keywords) or 1.0
        score = min(self._max_score, total_weighted_score / max_possible)

        return MatchResult(
            matched_keywords=sorted(matched_set),
            hits=hits,
            entity_hits=sorted(entity_hits),
            score=round(score, 4),
            has_entity_hit=bool(entity_hits),
        )

    def match(self, document: "Any") -> MatchResult:
        """
        Match against a CanonicalDocument.
        Accepts any object with .title, .cleaned_text/.raw_text, .url attributes.
        """
        title = getattr(document, "title", "") or ""
        body = getattr(document, "cleaned_text", "") or getattr(document, "raw_text", "") or ""
        url = getattr(document, "url", "") or ""
        return self.match_text(title=title, body=body, url=url)

    def filter(self, documents: list["Any"], min_score: float = 0.0) -> list[tuple["Any", MatchResult]]:
        """
        Filter documents by keyword match. Returns (document, result) pairs.
        Set min_score > 0 to only return documents with at least one hit.
        """
        results = []
        for doc in documents:
            result = self.match(doc)
            if result.matched_count > 0 and result.score >= min_score:
                results.append((doc, result))
        return sorted(results, key=lambda x: x[1].score, reverse=True)
