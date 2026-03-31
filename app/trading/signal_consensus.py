"""Multi-model consensus validator for trading signals.

Sends signal context to a second LLM (OpenAI) for independent
directional assessment.  The validator returns agree/disagree +
confidence so the trading loop can gate execution on consensus.

Usage:
    validator = SignalConsensusValidator(api_key="sk-...")
    result = await validator.validate(signal, market_data)
    if not result.agreed:
        # skip trade
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from openai import AsyncOpenAI

from app.market_data.models import MarketDataPoint
from app.signals.models import SignalCandidate

log = structlog.get_logger(__name__)

_CONSENSUS_SYSTEM_PROMPT = """\
You are an independent trading signal validator.  You receive a proposed \
trade and current market context.  Your job is to assess whether the \
directional thesis is reasonable.

Respond with ONLY a JSON object (no markdown, no backticks):
{
  "agree": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "one sentence"
}

Rules:
- Focus on whether the DIRECTION makes sense given the data.
- Be skeptical of reactive narratives (price already moved).
- agree=true means you would take the same directional bet.
- confidence reflects how sure you are about your assessment.
"""

_CONSENSUS_USER_TEMPLATE = """\
Proposed trade:
  Symbol: {symbol}
  Direction: {direction}
  Entry price: ${entry_price:,.2f}
  Thesis: {thesis}

Market context:
  Current price: ${current_price:,.2f}
  24h change: {change_24h:+.2f}%
  24h volume: ${volume_24h:,.0f}

Supporting factors: {supporting}
Contradicting factors: {contradicting}

Do you agree with this {direction} trade? Respond with JSON only.\
"""


@dataclass(frozen=True)
class ConsensusResult:
    """Result of the consensus validation."""

    agreed: bool
    confidence: float
    reasoning: str
    validator_model: str
    validated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    error: str | None = None

    @staticmethod
    def failed(error: str, model: str = "unknown") -> ConsensusResult:
        """Build a fail-closed result (disagree on error)."""
        return ConsensusResult(
            agreed=False,
            confidence=0.0,
            reasoning=f"validation_error: {error}",
            validator_model=model,
            error=error,
        )


class SignalConsensusValidator:
    """Validates signals via a second LLM for multi-model consensus."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout: int = 15,
        max_tokens: int = 256,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    async def validate(
        self,
        signal: SignalCandidate,
        market_data: MarketDataPoint,
    ) -> ConsensusResult:
        """Ask the validator LLM whether it agrees with the signal."""
        user_msg = _CONSENSUS_USER_TEMPLATE.format(
            symbol=signal.symbol,
            direction=signal.direction.value,
            entry_price=signal.entry_price,
            thesis=signal.thesis,
            current_price=market_data.price,
            change_24h=market_data.change_pct_24h,
            volume_24h=market_data.volume_24h,
            supporting="; ".join(signal.supporting_factors) or "none",
            contradicting="; ".join(signal.contradictory_factors) or "none",
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _CONSENSUS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=self._max_tokens,
                temperature=0.2,
            )
        except Exception as exc:
            log.error("consensus.llm_error", error=str(exc))
            return ConsensusResult.failed(str(exc), model=self._model)

        raw = (response.choices[0].message.content or "").strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("consensus.parse_error", raw=raw[:200])
            return ConsensusResult.failed(
                "invalid_json_response", model=self._model,
            )

        agreed = bool(data.get("agree", False))
        confidence = float(data.get("confidence", 0.0))
        reasoning = str(data.get("reasoning", ""))

        result = ConsensusResult(
            agreed=agreed,
            confidence=confidence,
            reasoning=reasoning,
            validator_model=self._model,
        )

        log.info(
            "consensus.result",
            symbol=signal.symbol,
            direction=signal.direction.value,
            agreed=agreed,
            confidence=confidence,
            reasoning=reasoning,
        )

        return result
