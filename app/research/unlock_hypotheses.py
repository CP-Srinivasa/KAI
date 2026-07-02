"""Token-unlock pressure hypotheses for the edge-discovery harness.

These deciders read the causal unlock-pressure feature (``unlock_frac_fwd_z`` —
see ``unlock_align``) and return +1/-1/0. They encode the documented "unlock
short" edge (large scheduled unlocks dilute/anticipate selling → price tends to
fall into them) and its mirror, so the engine — not the author — decides:

  * ``unlock_imminent_short`` — a large unlock is approaching → short.
  * ``unlock_quiet_long``     — unusually little upcoming unlock supply → long.

Kept OUT of the production ``runner.default_hypotheses`` (the daily runner has no
unlock data wired). The Phase-1 research entry appends them to the SAME BH-FDR
batch as the TA/funding set (honest multiple-testing bar) and records them in the
shared hypothesis ledger (cumulative trial count → harder DSR bar at promotion).
A None feature (warm-up) maps to 0 (no trade), never a fabricated side.

This is the doctrine's strongest documented capital-free edge candidate, so unlike
the whale-transfer gate a survivor here is plausible — but still must clear the
full BH-FDR + bucket-consistency bar.
"""

from __future__ import annotations

from app.analysis.features.feature_matrix import FeatureRow
from app.research.samples import Decider

# Z-score extremity at which upcoming unlock pressure is "unusual" enough to act
# on. 1.0 sigma is permissive (more trades → a fairer test); BH-FDR + bucket
# consistency filter noise, not this threshold.
UNLOCK_Z_TRIGGER = 1.0


def unlock_hypotheses() -> list[tuple[str, Decider]]:
    """The token-unlock pressure decider set (None-safe; direction-paired)."""

    def unlock_imminent_short(r: FeatureRow) -> int:
        # A large scheduled unlock is approaching → dilution / anticipated selling.
        return (
            -1
            if (r.unlock_frac_fwd_z is not None and r.unlock_frac_fwd_z > UNLOCK_Z_TRIGGER)
            else 0
        )

    def unlock_quiet_long(r: FeatureRow) -> int:
        # Unusually little upcoming unlock supply → reduced overhang → long.
        return (
            1
            if (r.unlock_frac_fwd_z is not None and r.unlock_frac_fwd_z < -UNLOCK_Z_TRIGGER)
            else 0
        )

    return [
        ("unlock_imminent_short", unlock_imminent_short),
        ("unlock_quiet_long", unlock_quiet_long),
    ]
