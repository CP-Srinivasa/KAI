"""OpenAI analysis provider.

Uses the OpenAI Chat Completions beta structured-outputs API to produce
validated LLMAnalysisOutput directly — no manual JSON parsing.

Design:
- Inject the OpenAI client (testable, replaceable)
- Hard text limit to avoid token blowout (first 4000 chars)
- Structured output via beta.chat.completions.parse(response_format=LLMAnalysisOutput)
- No manual JSON decoding — Pydantic validates directly from parsed output
- All exceptions wrapped into ProviderError
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.core.errors import ProviderError

PROMPT_VERSION = "v2"
_MAX_TEXT_CHARS = 4000
_DEFAULT_MODEL = "gpt-4o"

_SYSTEM_PROMPT = """\
You are a professional financial news analyst specialized in crypto and traditional markets.
Analyze the provided article and return a structured analysis with these guidelines:

- sentiment_label: overall market sentiment direction
- sentiment_score: -1.0 (very bearish) to 1.0 (very bullish)
- relevance_score: 0.0–1.0, how relevant to crypto/financial markets
- impact_score: 0.0–1.0, potential market-moving impact
- confidence_score: 0.0–1.0, your analytical confidence
- novelty_score: 0.0–1.0, how new/surprising this information is
- spam_probability: 0.0–1.0, likelihood of spam/clickbait
- market_scope: primary market affected
- affected_assets: list of specific asset names or tickers
- affected_sectors: list of market sectors (e.g. DeFi, Mining, Layer1)
- event_type: category such as regulatory, hack, partnership, macro, earnings, or null
- short_reasoning: one concise sentence summarizing the key signal
- bull_case: one sentence bullish scenario or null
- bear_case: one sentence bearish scenario or null
- recommended_priority: 1–10 urgency for a trader (10 = act now)
- actionable: true if this warrants immediate attention
- tags: up to 10 relevant topic tags
"""


def _build_user_content(title: str, text: str, context: dict[str, Any] | None) -> str:
    truncated = text[:_MAX_TEXT_CHARS] if text else ""
    parts = [f"Title: {title}"]
    if truncated:
        parts.append(f"\nText:\n{truncated}")
    else:
        parts.append("\n[Title only — no body text provided]")
    if context:
        import json as _json

        parts.append(f"\nContext:\n{_json.dumps(context, ensure_ascii=False)}")
    return "\n".join(parts)


class OpenAIAnalysisProvider(BaseAnalysisProvider):
    """OpenAI-backed analysis provider using structured outputs (beta.parse)."""

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

    @property
    def model(self) -> str:
        return self._model

    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        user_content = _build_user_content(title, text, context)

        try:
            response = await self._client.beta.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format=LLMAnalysisOutput,
                temperature=0.1,
                max_tokens=1024,
            )
        except Exception as exc:
            raise ProviderError(f"OpenAI API call failed: {exc}") from exc

        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ProviderError("OpenAI returned null parsed output")

        return parsed
