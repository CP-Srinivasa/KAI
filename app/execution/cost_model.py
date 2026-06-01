"""CostModel — the single source of trading-cost truth (Sprint B, 2026-06-01).

Why this exists
---------------
Before Sprint B there were two divergent fee paths:

1. ``app/execution/fees.py`` (NEO-P-106) reading ``config/venue_fees.yaml`` for
   per-side maker/taker bps — used by the paper engine.
2. A separate ``RISK_ROUND_TRIP_FEE_PCT`` setting (default 1.2%) consumed by the
   V1 cost-geometry gate in ``app/risk/engine.py``.

Gate-fee != engine-fee is silent drift. When the later edge gate (Sprint D)
computes ``net_edge_bps`` it MUST use the exact same cost the engine charges, or
it gates on a fiction.

Design contract
---------------
- **Per-side is the source.** ``entry_fee_bps`` / ``exit_fee_bps`` come from the
  YAML via ``fees.lookup_fee`` (one lookup path, no second fee table).
- **Round-trip is always derived** (entry + exit). It is never stored as a
  standalone number that could drift from its components.
- ``total_cost_bps`` = round_trip_fee_bps + expected_spread_bps +
  expected_slippage_bps (a round-trip-basis figure).
- **paper default = realistic 10 bp/side** (config: explicit ``paper`` venue).
  The conservative worst-case (60 bp/side) survives ONLY as the error-path hard
  fallback (corrupt/missing YAML) and as the default for genuinely unknown
  venues — distinct layers, never the normal-operation paper number.
- Provider-open: venue is a config key, no exchange is hardcoded here.

Accounting helper
-----------------
``summarize_fees`` separates open-fill fees from closed round-trip fees so the
classic accounting bug (folding open entry fees into closed PnL) is structurally
impossible. See NEO-F-302.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.execution import fees

Side = Literal["maker", "taker"]
Leg = Literal["entry", "exit"]


@dataclass(frozen=True)
class RoundTripCost:
    """Resolved cost for one round-trip on a venue.

    Per-side fees are the source; ``round_trip_fee_bps`` and ``total_cost_bps``
    are derived in ``__post_init__``-equivalent factory logic (see CostModel),
    never stored independently of their components.
    """

    venue: str
    entry_side: Side
    exit_side: Side
    entry_fee_bps: float
    exit_fee_bps: float
    expected_spread_bps: float
    expected_slippage_bps: float
    table_version: str

    @property
    def round_trip_fee_bps(self) -> float:
        """Derived: entry + exit fee. Never stored standalone."""
        return self.entry_fee_bps + self.exit_fee_bps

    @property
    def total_cost_bps(self) -> float:
        """Round-trip fee plus spread and slippage assumptions."""
        return self.round_trip_fee_bps + self.expected_spread_bps + self.expected_slippage_bps

    @property
    def round_trip_fee_pct(self) -> float:
        """Round-trip fee expressed in PERCENT (for the legacy V1 gate API)."""
        return self.round_trip_fee_bps / 100.0


class CostModel:
    """Single source for venue trading costs.

    Reads ``config/venue_fees.yaml`` through ``app.execution.fees`` (per-side
    bps) and directly for the additive spread/slippage fields. One model,
    deterministic, provider-agnostic.
    """

    def __init__(self, *, config_path: Path | None = None) -> None:
        self._config_path = config_path

    # --- per-side (the source) -------------------------------------------------

    def entry_fee_bps(self, *, venue: str, side: Side = "taker") -> float:
        return self._fee_bps(venue=venue, side=side)

    def exit_fee_bps(self, *, venue: str, side: Side = "taker") -> float:
        return self._fee_bps(venue=venue, side=side)

    def _fee_bps(self, *, venue: str, side: Side) -> float:
        record = fees.lookup_fee(venue, side, config_path=self._config_path)
        return record.bps_applied

    # --- spread / slippage (additive YAML fields) ------------------------------

    def _venue_extra(self, venue: str, key: str, default: float = 0.0) -> float:
        table = (
            fees._load_table(self._config_path)  # noqa: SLF001 — same module family
            if self._config_path
            else fees._load_table()  # noqa: SLF001
        )
        venues = table.get("venues", {}) if isinstance(table.get("venues"), dict) else {}
        entry = venues.get((venue or "").strip().lower())
        if isinstance(entry, dict) and isinstance(entry.get(key), (int, float)):
            return float(entry[key])
        return default

    def expected_spread_bps(self, *, venue: str) -> float:
        return self._venue_extra(venue, "expected_spread_bps")

    def expected_slippage_bps(self, *, venue: str) -> float:
        return self._venue_extra(venue, "expected_slippage_bps")

    # --- round-trip (derived) --------------------------------------------------

    def round_trip(
        self,
        *,
        venue: str,
        entry_side: Side = "taker",
        exit_side: Side = "taker",
    ) -> RoundTripCost:
        record_v = fees.lookup_fee(venue, entry_side, config_path=self._config_path)
        return RoundTripCost(
            venue=record_v.venue,
            entry_side=entry_side,
            exit_side=exit_side,
            entry_fee_bps=self.entry_fee_bps(venue=venue, side=entry_side),
            exit_fee_bps=self.exit_fee_bps(venue=venue, side=exit_side),
            expected_spread_bps=self.expected_spread_bps(venue=venue),
            expected_slippage_bps=self.expected_slippage_bps(venue=venue),
            table_version=record_v.table_version,
        )

    def round_trip_fee_pct(
        self,
        *,
        venue: str,
        entry_side: Side = "taker",
        exit_side: Side = "taker",
    ) -> float:
        """Round-trip fee in PERCENT — the API the V1 gate / Settings consume."""
        return self.round_trip(
            venue=venue, entry_side=entry_side, exit_side=exit_side
        ).round_trip_fee_pct

    # --- net edge (Sprint D forward-API, single-source by construction) --------

    def net_edge_bps(
        self,
        *,
        venue: str,
        side_adjusted_return_bps: float,
        entry_side: Side = "taker",
        exit_side: Side = "taker",
        safety_margin_bps: float = 0.0,
    ) -> float:
        """Edge after all known costs.

        net_edge = return - entry_fee - exit_fee - spread - slippage - margin.

        Sprint D's edge gate is expected to call exactly this so the gate and the
        engine can never charge different costs. Kept here (not in the gate) so
        there is one cost formula in one place.
        """
        rt = self.round_trip(venue=venue, entry_side=entry_side, exit_side=exit_side)
        return (
            side_adjusted_return_bps
            - rt.entry_fee_bps
            - rt.exit_fee_bps
            - rt.expected_spread_bps
            - rt.expected_slippage_bps
            - safety_margin_bps
        )


# --- fee accounting: open vs closed separation (NEO-F-302) ---------------------


@dataclass(frozen=True)
class FillRecord:
    """Minimal accounting view of a paper fill.

    ``leg`` distinguishes the entry fill from the closing (exit) fill of a
    round-trip. ``trade_pnl_usd`` is the per-trade NET PnL booked on the exit
    leg (already net of both legs' fees, per paper_engine semantics); it is 0.0
    on entry legs and on still-open positions.
    """

    symbol: str
    leg: Leg
    fee_usd: float
    trade_pnl_usd: float = 0.0


@dataclass(frozen=True)
class FeeSummary:
    """Honest fee accounting with open and closed buckets kept separate."""

    closed_net_pnl_usd: float
    fees_closed_usd: float
    fees_open_usd: float

    @property
    def total_fees_usd(self) -> float:
        """Informational only — explicitly the sum of the two buckets."""
        return self.fees_open_usd + self.fees_closed_usd


def summarize_fees(fills: Iterable[FillRecord]) -> FeeSummary:
    """Separate open-fill fees from closed round-trip fees.

    The forbidden operation this prevents: ``sum(all fee_usd) - closed_pnl``,
    which folds the entry fees of still-OPEN positions into the closed book and
    mislabels the gross result (the real bug: +433 instead of -283).

    Rules:
    - ``closed_net_pnl_usd``: sum of ``trade_pnl_usd`` over exit legs (already
      fee-net). This is the only legitimate "closed PnL" figure.
    - ``fees_closed_usd``: fees of legs belonging to a CLOSED round-trip, i.e.
      every exit leg plus the matching entry legs of closed symbols.
    - ``fees_open_usd``: entry-leg fees of positions with no matching exit yet.

    Matching is by symbol: an entry leg whose symbol later has an exit leg is
    "closed"; otherwise it is "open". This mirrors the paper engine where a
    position is closed once its exit fill is booked.
    """
    fill_list = list(fills)
    closed_symbols = {f.symbol for f in fill_list if f.leg == "exit"}

    closed_net_pnl = 0.0
    fees_closed = 0.0
    fees_open = 0.0

    for f in fill_list:
        if f.leg == "exit":
            closed_net_pnl += f.trade_pnl_usd
            fees_closed += f.fee_usd
        else:  # entry leg
            if f.symbol in closed_symbols:
                fees_closed += f.fee_usd
            else:
                fees_open += f.fee_usd

    return FeeSummary(
        closed_net_pnl_usd=closed_net_pnl,
        fees_closed_usd=fees_closed,
        fees_open_usd=fees_open,
    )


def _venue_market_type(venue: str, config_path: Path | None = None) -> str:
    """Read a venue's market_type (default 'spot'). Provider-open helper."""
    table: dict[str, Any] = fees._load_table(config_path) if config_path else fees._load_table()  # noqa: SLF001
    venues = table.get("venues", {}) if isinstance(table.get("venues"), dict) else {}
    entry = venues.get((venue or "").strip().lower())
    if isinstance(entry, dict) and isinstance(entry.get("market_type"), str):
        return str(entry["market_type"])
    return "spot"
