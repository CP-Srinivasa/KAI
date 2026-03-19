"""Companion provider module for local model inferences."""

import json
import logging
from typing import Any

import httpx

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.core.enums import MarketScope, SentimentLabel

logger = logging.getLogger(__name__)


class InternalCompanionProvider(BaseAnalysisProvider):
    """Local Tier 2 Analyst connecting to OpenAI-compatible endpoints."""

    def __init__(self, endpoint: str, model: str, timeout: int = 10) -> None:
        self.endpoint = endpoint.rstrip("/")
        self._model = model
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "companion"

    @property
    def model(self) -> str | None:
        return self._model

    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        """Call the local endpoint to perform standardized JSON analysis."""
        prompt = (
            f"Analyze this financial document and return a JSON object containing:\n"
            f"summary (str), sentiment_label (bullish/bearish/neutral), "
            f"sentiment_score (-1.0 to 1.0), relevance_score (0.0 to 1.0), "
            f"impact_score (0.0 to 1.0), priority_score (1 to 10), "
            f"market_scope (crypto/equities/macro/etf/mixed/unknown), "
            f"affected_assets (list of str), "
            f"tags (list of str).\n\n"
            f"Title: {title}\n"
            f"Content:\n{text[:6000]}"
        )

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a highly precise financial AI analyst. "
                        "Always return valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        url = f"{self.endpoint}/v1/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    err_text = resp.text
                    logger.error("Companion model HTTP error %s: %s", resp.status_code, err_text)
                    raise RuntimeError(f"Companion model error: {resp.status_code}")

                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)

            # Cap the impact score at 0.8 per conservative fallback rule (I-17)
            impact = min(0.8, float(parsed.get("impact_score", 0.0)))
            priority = int(parsed.get("priority_score", 5))
            summary = (
                parsed.get("summary")
                or parsed.get("short_reasoning")
                or parsed.get("co_thought")
                or "Local companion analysis."
            )

            return LLMAnalysisOutput(
                sentiment_label=SentimentLabel(parsed.get("sentiment_label", "neutral").lower()),
                sentiment_score=float(parsed.get("sentiment_score", 0.0)),
                relevance_score=float(parsed.get("relevance_score", 0.0)),
                impact_score=impact,
                confidence_score=0.7,
                novelty_score=0.5,
                spam_probability=0.0,
                market_scope=MarketScope(parsed.get("market_scope", "unknown").lower()),
                affected_assets=parsed.get("affected_assets", []),
                affected_sectors=[],
                actionable=(priority >= 7),
                tags=parsed.get("tags", []),
                short_reasoning=str(summary),
                recommended_priority=priority,
            )

        except Exception as e:
            logger.error("Companion model request failed: %s", e)
            raise RuntimeError("Companion model request failed") from e
