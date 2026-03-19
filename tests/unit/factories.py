"""Test factories for the AI Analyst Trading Bot."""

import uuid

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel


def make_document(**kwargs) -> CanonicalDocument:
    defaults = {
        "url": f"https://example.com/doc-{uuid.uuid4()}",
        "title": "Test Document",
    }
    defaults.update(kwargs)
    return CanonicalDocument(**defaults)

def make_llm_output(**kwargs) -> LLMAnalysisOutput:
    defaults = {
        "sentiment_label": SentimentLabel.NEUTRAL,
        "sentiment_score": 0.0,
        "relevance_score": 0.5,
        "impact_score": 0.5,
        "confidence_score": 0.8,
        "novelty_score": 0.5,
        "spam_probability": 0.0,
        "market_scope": MarketScope.UNKNOWN,
        "actionable": False,
    }
    defaults.update(kwargs)
    return LLMAnalysisOutput(**defaults)

def make_analysis_result(document_id: uuid.UUID | str, **kwargs) -> AnalysisResult:
    defaults = {
        "document_id": str(document_id),
        "sentiment_label": SentimentLabel.NEUTRAL,
        "sentiment_score": 0.0,
        "relevance_score": 0.5,
        "impact_score": 0.5,
        "confidence_score": 0.8,
        "novelty_score": 0.5,
        "market_scope": MarketScope.UNKNOWN,
        "actionable": False,
        "explanation_short": "Test analysis",
        "explanation_long": "Test analysis long description",
    }
    defaults.update(kwargs)
    return AnalysisResult(**defaults)
