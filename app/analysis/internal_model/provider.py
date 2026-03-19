"""InternalModelProvider — self-contained Tier 2 analyst.

Uses deterministic heuristics backed by the keyword engine.
No external API calls. No API key. Always available.

Intentionally conservative:
- sentiment always NEUTRAL (rules cannot reliably determine direction)
- actionable always False (human review gate)
- priority ceiling of 5 (I-13)
- confidence reflects keyword density, not semantic understanding

Upgrade path: replace the _compute_* methods with a fine-tuned model
call without changing the interface or the callers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.keywords.engine import KeywordEngine, KeywordHit
from app.analysis.rules.rule_analyzer import compute_spam_probability
from app.core.domain.document import EntityMention
from app.core.enums import MarketScope, SentimentLabel
from app.enrichment.entities.matcher import hits_to_entity_mentions

_ASSET_CATEGORIES = frozenset({"crypto", "equity", "etf"})
_MAX_TAGS = 20


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        k = v.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(v.strip())
    return out


def _relevance(keyword_hits: list[KeywordHit], entity_mentions: list[EntityMention]) -> float:
    keyword_signal = sum(min(h.occurrences, 3) for h in keyword_hits) * 0.12
    entity_signal = min(0.18, len(entity_mentions) * 0.05)
    return round(min(1.0, keyword_signal + entity_signal), 4)


def _impact(affected_assets: list[str]) -> float:
    return round(min(0.30, len(affected_assets) * 0.08), 4)


def _novelty(context: dict[str, Any] | None) -> float:
    published_at = (context or {}).get("published_at")
    if not isinstance(published_at, datetime):
        return 0.35
    age_hours = max(0.0, (datetime.now(UTC) - published_at).total_seconds() / 3600)
    if age_hours <= 24:
        return 0.55
    if age_hours <= 24 * 7:
        return 0.40
    return 0.25


def _confidence(keyword_hits: list[KeywordHit], entity_mentions: list[EntityMention]) -> float:
    return round(min(0.65, 0.30 + len(keyword_hits) * 0.05 + len(entity_mentions) * 0.03), 4)


def _market_scope(keyword_hits: list[KeywordHit]) -> MarketScope:
    scores: dict[MarketScope, int] = {
        MarketScope.CRYPTO: 0,
        MarketScope.EQUITIES: 0,
        MarketScope.MACRO: 0,
    }
    for hit in keyword_hits:
        if hit.category == "crypto":
            scores[MarketScope.CRYPTO] += hit.occurrences
        elif hit.category in {"equity", "etf"}:
            scores[MarketScope.EQUITIES] += hit.occurrences
        elif hit.category == "macro":
            scores[MarketScope.MACRO] += hit.occurrences

    top = max(scores.values())
    if top == 0:
        return MarketScope.UNKNOWN
    ordered = sorted(scores.values(), reverse=True)
    if ordered[1] and ordered[1] >= top * 0.75:
        return MarketScope.MIXED
    return max(scores, key=scores.__getitem__)


class InternalModelProvider(BaseAnalysisProvider):
    """Tier 2 analyst: deterministic heuristics, always available, no API key.

    Implements BaseAnalysisProvider so it slots into AnalysisPipeline
    or EnsembleProvider without any special handling.

    Priority ceiling: ≤ 5 (I-13). Use external providers to exceed this.
    """

    def __init__(self, keyword_engine: KeywordEngine) -> None:
        self._keyword_engine = keyword_engine

    @property
    def provider_name(self) -> str:
        return "internal"

    @property
    def model(self) -> str | None:
        return "rule-heuristic-v1"

    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        """Produce a conservative analysis using keyword/entity heuristics."""
        full_text = f"{title} {text}".strip()

        keyword_hits = self._keyword_engine.match(full_text)
        entity_mentions = hits_to_entity_mentions(keyword_hits)

        affected_assets = _unique(
            [h.canonical for h in keyword_hits if h.category in _ASSET_CATEGORIES]
            + list((context or {}).get("tickers", []))
        )

        tags = _unique(
            [h.canonical for h in keyword_hits]
            + [m.name for m in entity_mentions if m.entity_type in {"topic", "person"}]
        )[:_MAX_TAGS]

        spam_prob = compute_spam_probability(title, text)
        relevance = _relevance(keyword_hits, entity_mentions)
        impact = _impact(affected_assets)
        novelty = _novelty(context)
        confidence = _confidence(keyword_hits, entity_mentions)
        scope = _market_scope(keyword_hits)

        keyword_summary = ", ".join(h.canonical for h in keyword_hits[:5])
        short_reasoning = (
            f"Internal rule-based analysis. "
            f"Keywords: {keyword_summary or 'none'}. "
            f"Assets: {', '.join(affected_assets[:3]) or 'none'}."
        )

        return LLMAnalysisOutput(
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=relevance,
            impact_score=impact,
            confidence_score=confidence,
            novelty_score=novelty,
            spam_probability=spam_prob,
            market_scope=scope,
            affected_assets=affected_assets,
            affected_sectors=[],
            actionable=False,
            tags=tags,
            short_reasoning=short_reasoning,
            recommended_priority=5,
        )
