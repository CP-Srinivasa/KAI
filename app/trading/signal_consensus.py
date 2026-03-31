"""Multi-model consensus validator for trading signals.

Sends signal context to one or more independent LLMs for directional
assessment.  ALL validators must agree before a trade proceeds
(unanimous consensus, fail-closed).

Supports any OpenAI-compatible API (OpenAI, Gemini via compatibility
endpoint, local models, etc.) via configurable base_url.

Usage:
    # Single model (backward-compatible)
    validator = SignalConsensusValidator(api_key="sk-...")
    result = await validator.validate(signal, market_data)

    # Multi-model consensus
    validator = SignalConsensusValidator.multi(
        ValidatorConfig(api_key="sk-...", model="gpt-4o-mini", label="openai"),
        ValidatorConfig(api_key="AIza...", model="gemini-2.5-flash",
                        base_url=GEMINI_OPENAI_BASE_URL, label="gemini"),
    )
    result = await validator.validate(signal, market_data)
    if not result.agreed:
        # at least one validator disagreed
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from openai import AsyncOpenAI

from app.market_data.models import MarketDataPoint
from app.signals.models import SignalCandidate

log = structlog.get_logger(__name__)

GEMINI_OPENAI_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/openai/"
)

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
class ValidatorConfig:
    """Configuration for a single validator backend."""

    api_key: str
    model: str = "gpt-4o-mini"
    label: str = ""
    base_url: str | None = None
    timeout: int = 15
    max_tokens: int = 256

    @property
    def display_label(self) -> str:
        return self.label or self.model


@dataclass(frozen=True)
class SingleValidatorResult:
    """Result from one validator in the ensemble."""

    label: str
    model: str
    agreed: bool
    confidence: float
    reasoning: str
    error: str | None = None


@dataclass(frozen=True)
class ConsensusResult:
    """Aggregated result of the consensus validation."""

    agreed: bool
    confidence: float
    reasoning: str
    validator_model: str
    validated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    error: str | None = None
    validator_results: list[SingleValidatorResult] = field(
        default_factory=list,
    )

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
    """Validates signals via one or more LLMs for multi-model consensus."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: int = 15,
        max_tokens: int = 256,
        *,
        configs: list[ValidatorConfig] | None = None,
    ) -> None:
        if configs:
            self._configs = configs
        else:
            self._configs = [
                ValidatorConfig(
                    api_key=api_key,
                    model=model,
                    timeout=timeout,
                    max_tokens=max_tokens,
                ),
            ]

    @classmethod
    def multi(cls, *configs: ValidatorConfig) -> SignalConsensusValidator:
        """Create a multi-model validator from explicit configs."""
        return cls(configs=list(configs))

    @property
    def models(self) -> list[str]:
        """Return list of model identifiers for logging."""
        return [c.display_label for c in self._configs]

    async def validate(
        self,
        signal: SignalCandidate,
        market_data: MarketDataPoint,
    ) -> ConsensusResult:
        """Ask all validator LLMs — ALL must agree (unanimous)."""
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

        tasks = [
            self._validate_single(cfg, user_msg) for cfg in self._configs
        ]
        results = await asyncio.gather(*tasks)

        all_agreed = all(r.agreed for r in results)
        avg_confidence = (
            sum(r.confidence for r in results) / len(results)
            if results
            else 0.0
        )
        errors = [r.error for r in results if r.error]
        model_str = "+".join(r.model for r in results)
        reasoning_parts = [
            f"{r.label}:{r.reasoning}" for r in results
        ]

        result = ConsensusResult(
            agreed=all_agreed,
            confidence=avg_confidence,
            reasoning=" | ".join(reasoning_parts),
            validator_model=model_str,
            error="; ".join(errors) if errors else None,
            validator_results=list(results),
        )

        log.info(
            "consensus.result",
            symbol=signal.symbol,
            direction=signal.direction.value,
            agreed=all_agreed,
            confidence=avg_confidence,
            models=model_str,
            individual=[
                {"label": r.label, "agreed": r.agreed, "conf": r.confidence}
                for r in results
            ],
        )

        return result

    async def _validate_single(
        self,
        cfg: ValidatorConfig,
        user_msg: str,
    ) -> SingleValidatorResult:
        """Query one validator backend."""
        client_kwargs: dict = {
            "api_key": cfg.api_key,
            "timeout": cfg.timeout,
        }
        if cfg.base_url:
            client_kwargs["base_url"] = cfg.base_url

        try:
            client = AsyncOpenAI(**client_kwargs)
            response = await client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": _CONSENSUS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=cfg.max_tokens,
                temperature=0.2,
            )
        except Exception as exc:
            log.error(
                "consensus.llm_error",
                model=cfg.model,
                label=cfg.display_label,
                error=str(exc),
            )
            return SingleValidatorResult(
                label=cfg.display_label,
                model=cfg.model,
                agreed=False,
                confidence=0.0,
                reasoning=f"validation_error: {exc}",
                error=str(exc),
            )

        raw = (response.choices[0].message.content or "").strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(
                "consensus.parse_error",
                model=cfg.model,
                label=cfg.display_label,
                raw=raw[:200],
            )
            return SingleValidatorResult(
                label=cfg.display_label,
                model=cfg.model,
                agreed=False,
                confidence=0.0,
                reasoning="invalid_json_response",
                error="invalid_json_response",
            )

        return SingleValidatorResult(
            label=cfg.display_label,
            model=cfg.model,
            agreed=bool(data.get("agree", False)),
            confidence=float(data.get("confidence", 0.0)),
            reasoning=str(data.get("reasoning", "")),
        )
