"""Regime layer — multi-state market classification for KAI.

R1 design (2026-05-09): deterministic threshold-based classification for
six regime classes plus a three-stage volatility class. Read-only — no
TradingLoop integration; observation phase first to validate against
operator judgment over 14 days. HMM, panic/euphoria classes, funding/
orderbook/stablecoin signals come in R3-R5+ once the foundation is
operator-validated.
"""

from app.regime.models import (
    RegimeClass,
    RegimeSnapshot,
)

__all__ = [
    "RegimeClass",
    "RegimeSnapshot",
]
