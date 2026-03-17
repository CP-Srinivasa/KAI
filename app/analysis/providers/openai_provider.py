"""OpenAI analysis provider.

Uses the OpenAI Chat Completions API with JSON mode to produce structured
LLMAnalysisOutput. Prompt is versioned — change PROMPT_VERSION when updating.

Design:
- Inject the OpenAI client (testable, replaceable)
- Hard text limit to avoid token blowout (first 2000 chars)
- JSON schema enforced via response_format={"type": "json_object"}
- Output validated by LLMAnalysisOutput (Pydantic)
- All exceptions wrapped into provider-level errors
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.core.errors import ProviderError

PROMPT_VERSION = "v1"
_MAX_TEXT_CHARS = 2000
_DEFAULT_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = """\
You are a professional financial news analyst specialized in crypto and traditional markets.
Analyze the provided article title and text, then return a JSON object with exactly these fields:

{
  "sentiment_label": "bullish" | "bearish" | "neutral" | "mixed",
  "sentiment_score": float from -1.0 (very bearish) to 1.0 (very bullish),
  "relevance_score": float 0.0–1.0 (how relevant is this to crypto/financial markets),
  "impact_score": float 0.0–1.0 (potential market impact),
  "confidence_score": float 0.0–1.0 (your confidence in this analysis),
  "novelty_score": float 0.0–1.0 (how novel/surprising is this information),
  "spam_probability": float 0.0–1.0 (probability this is spam/clickbait),
  "market_scope": "crypto" | "equities" | "macro" | "etf" | "mixed" | "unknown",
  "affected_assets": list of asset names/tickers (e.g. ["Bitcoin", "ETH"]),
  "affected_sectors": list of sectors (e.g. ["DeFi", "Mining"]),
  "event_type": short string or null (e.g. "regulatory", "hack", "partnership"),
  "short_reasoning": one sentence summary of the key signal,
  "bull_case": one sentence bull case or null,
  "bear_case": one sentence bear case or null,
  "recommended_priority": integer 1–10 (10 = most urgent for a trader),
  "actionable": true if this warrants immediate attention, false otherwise,
  "tags": list of up to 10 relevant topic tags
}

Be concise. Do not include any text outside the JSON object.
"""


class OpenAIAnalysisProvider(BaseAnalysisProvider):
    """OpenAI-backed analysis provider using JSON mode."""

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "openai"

    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        truncated_text = text[:_MAX_TEXT_CHARS] if text else ""
        user_content = f"Title: {title}\n\nText:\n{truncated_text}"

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=800,
            )
        except Exception as exc:
            raise ProviderError(f"OpenAI API call failed: {exc}") from exc

        raw = response.choices[0].message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"OpenAI returned invalid JSON: {exc}\nRaw: {raw}") from exc

        try:
            return LLMAnalysisOutput(**data)
        except Exception as exc:
            raise ProviderError(f"OpenAI output failed validation: {exc}\nData: {data}") from exc
