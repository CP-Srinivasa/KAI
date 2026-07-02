"""KYT engine — combine screening + behavioural flags into a decision.

Decision policy (default, configurable):
  critical -> block · high -> manual_review · medium -> warn ·
  low/unknown-only -> allow.
High and critical therefore never auto-execute (``decision.blocks_execution``).
On provider failure the engine fails *conservative*: HOLD when un-screenable
address/counterparty data is present (cannot be cleared), WARN otherwise
(exchange order with low inherent transfer risk). Never raises.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.security.kyt.behavioral import analyze_behavioral
from app.security.kyt.models import (
    KytAssessment,
    KytDecision,
    KytFlag,
    KytReasonCode,
    KytRiskLevel,
    TransactionContext,
)
from app.security.kyt.providers import KytScreeningProvider, LocalListProvider
from app.security.kyt.rules import KytRules, default_rules

logger = logging.getLogger(__name__)

_SCORE_BY_LEVEL = {
    KytRiskLevel.UNKNOWN: 0,
    KytRiskLevel.LOW: 15,
    KytRiskLevel.MEDIUM: 40,
    KytRiskLevel.HIGH: 75,
    KytRiskLevel.CRITICAL: 100,
}

_NEXT_STEP = {
    KytDecision.ALLOW: "Execute normally. No transaction risk above threshold.",
    KytDecision.WARN: "Execute but record + monitor; review if the pattern repeats.",
    KytDecision.HOLD: "Do NOT auto-execute. Pause until screening data is restored/clarified.",
    KytDecision.MANUAL_REVIEW: "Do NOT auto-execute. Route to operator/SENTR for manual review.",
    KytDecision.BLOCK: "Refuse execution. Escalate to SENTR; record reason codes in audit.",
}

# Fields that are *expected* for an exchange order — used for data_completeness.
_EXCHANGE_FIELDS = ("symbol", "venue", "side")


class KytEngine:
    """Stateless evaluator. Construct once, call ``assess`` per transaction."""

    def __init__(
        self,
        providers: Sequence[KytScreeningProvider] | None = None,
        *,
        rules: KytRules | None = None,
        behavioral_enabled: bool = True,
        fail_mode: str = "conservative",
    ) -> None:
        self._rules = rules or default_rules()
        self._providers: tuple[KytScreeningProvider, ...] = tuple(
            providers if providers is not None else (LocalListProvider(self._rules),)
        )
        self._behavioral_enabled = behavioral_enabled
        self._fail_mode = fail_mode

    def assess(
        self,
        context: TransactionContext,
        *,
        history: Sequence[dict[str, object]] | None = None,
    ) -> KytAssessment:
        flags: list[KytFlag] = []
        provider_sources: list[str] = []
        provider_failed = False

        for provider in self._providers:
            provider_sources.append(provider.name)
            try:
                flags.extend(provider.screen(context))
            except Exception as exc:  # noqa: BLE001 — provider must not crash the gate
                provider_failed = True
                logger.warning("[kyt] provider %s failed: %s", provider.name, exc)
                flags.append(
                    KytFlag(
                        code=KytReasonCode.PROVIDER_UNAVAILABLE,
                        level=KytRiskLevel.UNKNOWN,
                        detail=f"Provider {provider.name} error: {type(exc).__name__}",
                        source=provider.name,
                        data_available=False,
                    )
                )

        if self._behavioral_enabled:
            try:
                flags.extend(analyze_behavioral(context, history or [], self._rules))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[kyt] behavioural analysis failed: %s", exc)
                flags.append(
                    KytFlag(
                        code=KytReasonCode.PROVIDER_UNAVAILABLE,
                        level=KytRiskLevel.UNKNOWN,
                        detail=f"Behavioural analysis error: {type(exc).__name__}",
                        source="behavioral",
                        data_available=False,
                    )
                )

        decision, risk_level, score = self._decide(context, flags, provider_failed)
        reason_codes = tuple(dict.fromkeys(f.code for f in flags)) or (KytReasonCode.OK,)
        completeness = self._data_completeness(context)

        return KytAssessment(
            tx_id=context.tx_id,
            phase=context.phase,
            risk_level=risk_level,
            decision=decision,
            score=score,
            flags=tuple(flags),
            reason_codes=reason_codes,
            provider_sources=tuple(dict.fromkeys(provider_sources)),
            data_completeness=completeness,
            recommended_next_step=_NEXT_STEP[decision],
        )

    def _decide(
        self,
        context: TransactionContext,
        flags: Sequence[KytFlag],
        provider_failed: bool,
    ) -> tuple[KytDecision, KytRiskLevel, int]:
        actionable = [f for f in flags if f.data_available and f.level != KytRiskLevel.UNKNOWN]
        max_level = KytRiskLevel.UNKNOWN
        for f in actionable:
            if f.level.rank > max_level.rank:
                max_level = f.level

        # base decision from the worst assessable level
        if max_level == KytRiskLevel.CRITICAL:
            decision = KytDecision.BLOCK
        elif max_level == KytRiskLevel.HIGH:
            decision = KytDecision.MANUAL_REVIEW
        elif max_level == KytRiskLevel.MEDIUM:
            decision = KytDecision.WARN
        elif max_level == KytRiskLevel.LOW:
            decision = KytDecision.ALLOW
        else:
            decision = KytDecision.ALLOW  # unknown-only → allow but flagged

        # conservative fallback on screening failure
        if provider_failed and self._fail_mode == "conservative":
            has_unscreenable = bool(context.wallet_address or context.counterparty)
            fallback = KytDecision.HOLD if has_unscreenable else KytDecision.WARN
            if fallback.blocks_execution or not decision.blocks_execution:
                # escalate, but never downgrade an already-blocking decision
                if max_level.rank < KytRiskLevel.HIGH.rank:
                    decision = fallback

        # score: worst level + small bump per extra actionable flag, capped 100
        base = _SCORE_BY_LEVEL[max_level]
        bump = 5 * max(0, len(actionable) - 1)
        score = min(100, base + bump)
        return decision, max_level, score

    @staticmethod
    def _data_completeness(context: TransactionContext) -> float:
        present = sum(1 for f in _EXCHANGE_FIELDS if getattr(context, f, None))
        return present / len(_EXCHANGE_FIELDS) if _EXCHANGE_FIELDS else 1.0

    def historical_lookback(
        self,
        transactions: Sequence[TransactionContext],
        *,
        history: Sequence[dict[str, object]] | None = None,
    ) -> list[KytAssessment]:
        """Re-assess a batch (e.g. after a rule/list update). Read-only."""
        return [self.assess(tx, history=history) for tx in transactions]
