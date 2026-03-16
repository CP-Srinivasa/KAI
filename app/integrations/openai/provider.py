"""
OpenAI / ChatGPT Analysis Provider
====================================
Implements BaseAnalysisProvider with structured JSON output,
token cost tracking, retry/timeout handling, and prompt versioning.

TODO: Add caching layer for identical document content hashes.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.analysis.llm.base import (
    DOCUMENT_ANALYSIS_JSON_SCHEMA,
    DOCUMENT_ANALYSIS_SYSTEM_PROMPT,
    DOCUMENT_ANALYSIS_USER_TEMPLATE,
    LLMAnalysisOutput,
    UsageRecord,
    BaseAnalysisProvider,
)
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.errors import LLMError, LLMOutputValidationError, LLMTimeoutError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Cost per 1K tokens (adjust as model pricing changes)
_COST_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o":           {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini":      {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo":      {"input": 0.01,   "output": 0.03},
    "gpt-3.5-turbo":    {"input": 0.0005, "output": 0.0015},
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    costs = _COST_TABLE.get(model, {"input": 0.01, "output": 0.03})
    return (prompt_tokens / 1000 * costs["input"]) + (completion_tokens / 1000 * costs["output"])


def _build_analysis_prompt(document: CanonicalDocument) -> str:
    content = document.cleaned_text or document.raw_text or document.summary
    content = content[:3000]
    return DOCUMENT_ANALYSIS_USER_TEMPLATE.format(
        title=document.title,
        source_name=document.source_name,
        source_type=document.source_type.value,
        published_at=document.published_at.isoformat() if document.published_at else "unknown",
        content=content,
        json_schema=json.dumps(DOCUMENT_ANALYSIS_JSON_SCHEMA, indent=2),
    )


class OpenAIProvider(BaseAnalysisProvider):
    """
    OpenAI-backed analysis provider.
    Uses response_format=json_object for structured output.
    Validates output against LLMAnalysisOutput schema before returning.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        cost_limit_usd_per_day: float = 10.0,
        max_retries: int = 3,
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            provider_name="openai", model=model,
            cost_limit_usd_per_day=cost_limit_usd_per_day,
            max_retries=max_retries, timeout_seconds=timeout_seconds,
        )
        self._client = AsyncOpenAI(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )

    async def analyze_document(self, document: CanonicalDocument) -> AnalysisResult:
        self._check_cost_limit()
        user_prompt = _build_analysis_prompt(document)
        start = time.monotonic()

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": DOCUMENT_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1500,
            )
        except openai.APITimeoutError as e:
            raise LLMTimeoutError(f"OpenAI timeout: {e}") from e
        except openai.RateLimitError as e:
            raise LLMError(f"OpenAI rate limit: {e}") from e
        except openai.APIError as e:
            raise LLMError(f"OpenAI API error: {e}") from e

        latency_ms = (time.monotonic() - start) * 1000
        usage = response.usage
        pt = usage.prompt_tokens if usage else 0
        ct = usage.completion_tokens if usage else 0
        cost = _estimate_cost(self.model, pt, ct)

        raw = response.choices[0].message.content or ""
        try:
            output = LLMAnalysisOutput.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            self._record_usage(UsageRecord(
                provider="openai", model=self.model, prompt_tokens=pt,
                completion_tokens=ct, total_tokens=pt + ct, cost_usd=cost,
                latency_ms=latency_ms, document_id=str(document.id), success=False, error=str(e),
            ))
            raise LLMOutputValidationError(f"Invalid JSON from OpenAI: {e}", raw_output=raw) from e

        self._record_usage(UsageRecord(
            provider="openai", model=self.model, prompt_tokens=pt,
            completion_tokens=ct, total_tokens=pt + ct, cost_usd=cost,
            latency_ms=latency_ms, document_id=str(document.id), success=True,
        ))

        return AnalysisResult(
            sentiment_label=output.sentiment_label,
            sentiment_score=output.sentiment_score,
            relevance_score=output.relevance_score,
            impact_score=output.impact_score,
            confidence_score=output.confidence_score,
            novelty_score=output.novelty_score,
            spam_probability=output.spam_probability,
            market_scope=output.market_scope,
            affected_assets=output.affected_assets,
            affected_sectors=output.affected_sectors,
            event_type=output.event_type,
            bull_case=output.bull_case,
            bear_case=output.bear_case,
            neutral_case=output.neutral_case,
            historical_analogs=output.historical_analogs,
            recommended_priority=output.recommended_priority,
            actionable=output.actionable,
            tags=output.tags,
            explanation_short=output.explanation_short,
            explanation_long=output.explanation_long,
            analyzed_by="openai",
            analyzed_at=datetime.utcnow(),
            analysis_model=self.model,
            token_count=pt + ct,
            cost_usd=cost,
        )

    async def summarize_document(self, document: CanonicalDocument, max_words: int = 100) -> str:
        self._check_cost_limit()
        content = (document.cleaned_text or document.raw_text)[:2000]
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Summarize financial/crypto news concisely. Be factual."},
                    {"role": "user", "content": f"Summarize in max {max_words} words:\n\nTitle: {document.title}\n\n{content}"},
                ],
                temperature=0.1, max_tokens=200,
            )
        except openai.APIError as e:
            raise LLMError(f"OpenAI summarize error: {e}") from e
        return response.choices[0].message.content or ""

    async def compare_to_historical(
        self, document: CanonicalDocument, historical_events: list[str]
    ) -> list[str]:
        self._check_cost_limit()
        events_text = "\n".join(f"- {e}" for e in historical_events[:20])
        content = document.cleaned_text[:1000] if document.cleaned_text else document.title
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": (
                        "You are a financial historian. Identify historical parallels for the event. "
                        "Return JSON: {\"analogs\": [\"description\", ...]}"
                    )},
                    {"role": "user", "content": (
                        f"Current event: {document.title}\n{content}\n\n"
                        f"Historical events:\n{events_text}\n\nReturn JSON with 'analogs' array (max 3)."
                    )},
                ],
                response_format={"type": "json_object"},
                temperature=0.1, max_tokens=300,
            )
        except openai.APIError as e:
            raise LLMError(f"OpenAI compare error: {e}") from e
        try:
            return json.loads(response.choices[0].message.content or "{}").get("analogs", [])[:3]
        except json.JSONDecodeError:
            return []

    async def healthcheck(self) -> dict[str, Any]:
        try:
            models = await self._client.models.list()
            return {
                "healthy": True, "provider": "openai", "model": self.model,
                "model_available": any(m.id == self.model for m in models.data),
                "daily_cost_usd": round(self._daily_cost_usd, 6),
                "daily_limit_usd": self.cost_limit_usd_per_day,
            }
        except Exception as e:
            return {"healthy": False, "provider": "openai", "error": str(e)}
