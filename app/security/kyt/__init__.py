"""KYT — Know Your Transaction.

Transaction-level risk prevention for KAI's execution path. Protects the system
(not users) against fraud, compromised wallets, sanctioned/blacklisted exposure,
risky counterparties, reputational and operational hazards.

Honest scope: KAI executes *exchange orders* (symbol/side/quantity/venue), not
on-chain wallet-to-wallet transfers. On-chain factors (sanctioned address,
mixer, chain-hopping, counterparty) are therefore marked ``unknown`` /
not-applicable unless a future transfer flow supplies address data — never
fabricated. What IS assessable today: symbol risk (privacy-coin / delisted /
blocklisted), venue/jurisdiction risk, and behavioural patterns (structuring,
round-tripping, frequency spikes, amount anomalies, profile deviation) derived
from the order/fill history.

Default-off, shadow-first — mirrors the diversification/Bayes rollout discipline.
"""

from __future__ import annotations

from app.security.kyt.models import (
    KytAssessment,
    KytCheckPhase,
    KytDecision,
    KytFlag,
    KytReasonCode,
    KytRiskLevel,
    TransactionContext,
)

__all__ = [
    "KytAssessment",
    "KytCheckPhase",
    "KytDecision",
    "KytFlag",
    "KytReasonCode",
    "KytRiskLevel",
    "TransactionContext",
]
