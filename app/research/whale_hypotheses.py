"""Whale exchange-flow hypotheses for the edge-discovery harness.

These deciders read the causal whale-flow features (``coin_netflow_z``,
``stable_netflow_z`` — see ``whale_flow_align``) and return +1/-1/0. They encode
the two textbook on-chain-flow theories, each in BOTH directions so the engine —
not the author — decides which (if any) holds:

  * Coin → exchange inflow is bearish (whales deposit coins to sell); the mirror
    outflow is bullish (withdrawing to hold).
  * Stablecoin → exchange inflow is bullish ("dry powder" arriving to buy); the
    mirror outflow is bearish (capital leaving).

Kept OUT of the production ``runner.default_hypotheses`` on purpose: the daily
runner has no whale-flow data wired, so these would contribute only dead n=0
rows there. The Phase-0 research entry (``scripts/whale_netflow_research.py``)
appends them to the SAME BH-FDR batch as the TA/funding set — so the
multiple-testing bar rises honestly — and records them in the shared hypothesis
ledger (cumulative trial count for downstream DSR deflation). A None feature
(warm-up / no flow yet) maps to 0 (no trade), never a fabricated side.

Whale data is a NEW data type, not a proven signal: zero survivors here is a
valid, expected outcome (academic evidence: large transfers predict volatility
more than direction, and are lagging/noisy).
"""

from __future__ import annotations

from app.analysis.features.feature_matrix import FeatureRow
from app.research.samples import Decider

# Z-score extremity at which a flow is "unusual" enough to act on. 1.0 sigma is
# deliberately permissive (more trades → a fairer test); the BH-FDR + bucket
# consistency gate filters noise, not this threshold.
FLOW_Z_TRIGGER = 1.0


def whale_hypotheses() -> list[tuple[str, Decider]]:
    """The whale exchange-flow decider set (None-safe; direction-paired)."""

    def coin_inflow_short(r: FeatureRow) -> int:
        # Coins flooding TO exchanges → expected selling → short.
        return -1 if (r.coin_netflow_z is not None and r.coin_netflow_z > FLOW_Z_TRIGGER) else 0

    def coin_outflow_long(r: FeatureRow) -> int:
        # Coins leaving exchanges → accumulation/hodl → long.
        return 1 if (r.coin_netflow_z is not None and r.coin_netflow_z < -FLOW_Z_TRIGGER) else 0

    def stable_inflow_long(r: FeatureRow) -> int:
        # Stablecoins flooding TO exchanges → dry powder to buy → long.
        return 1 if (r.stable_netflow_z is not None and r.stable_netflow_z > FLOW_Z_TRIGGER) else 0

    def stable_outflow_short(r: FeatureRow) -> int:
        # Stablecoins leaving exchanges → buying power withdrawn → short.
        return (
            -1 if (r.stable_netflow_z is not None and r.stable_netflow_z < -FLOW_Z_TRIGGER) else 0
        )

    return [
        ("coin_inflow_short", coin_inflow_short),
        ("coin_outflow_long", coin_outflow_long),
        ("stable_inflow_long", stable_inflow_long),
        ("stable_outflow_short", stable_outflow_short),
    ]
