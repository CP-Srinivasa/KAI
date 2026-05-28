"""Short-term candidate selection — the direct fix for the BTC/ETH-only loop.

Root cause of the BTC/ETH dominance (see analysis): the paper-trading cron
scans a hardcoded ``BTC/USDT`` + ``ETH/USDT`` only; broader coins enter the book
solely through premium Telegram signals. This module produces a *ranked,
diversified* short-term candidate list from the asset universe so the loop can
scan a spread of names instead of two majors.

Properties:
    * BTC/ETH are NOT blocked — they remain eligible, but they are reserve-class
      assets (universe horizon=long_term_reserve), are added explicitly and
      capped so they cannot dominate the short-term scan.
    * Already-concentrated clusters (from the current book) are penalised so the
      scan actively steers toward under-represented sectors / correlation
      groups.
    * Output is fully explained (per-candidate reasons) — the operator can see
      why each name was chosen or skipped.
    * Deterministic, no market calls, no estimation of missing data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from math import floor

from app.trading.asset_universe import (
    UNKNOWN,
    AssetMeta,
    AssetUniverse,
    get_asset_universe,
)
from app.trading.diversification import (
    DiversificationGuard,
    PositionExposure,
)

logger = logging.getLogger(__name__)

_BTC_ETH = ("BTC", "ETH")
# Penalty multipliers applied to a candidate's structural score.
_CONCENTRATION_PENALTY = 0.5  # cluster already over its cap
_CROWDED_PENALTY = 0.8  # cluster present but below cap
_RESERVE_CLASS_PENALTY = 0.7  # BTC/ETH in the short-term scan — nudge them down


@dataclass(frozen=True)
class CandidateRanking:
    symbol: str  # full pair, e.g. SOL/USDT
    base: str
    structural_score: float
    adjusted_score: float
    horizon: str
    sector: str
    correlation_group: str
    included: bool
    reasons: tuple[str, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "structural_score": round(self.structural_score, 4),
            "adjusted_score": round(self.adjusted_score, 4),
            "horizon": self.horizon,
            "sector": self.sector,
            "correlation_group": self.correlation_group,
            "included": self.included,
            "reasons": list(self.reasons),
        }


def select_short_term_candidates(
    *,
    positions: list[PositionExposure] | None = None,
    universe: AssetUniverse | None = None,
    limit: int = 6,
    quote: str = "USDT",
    include_btc_eth: bool = True,
    max_same_correlation_group: int = 2,
) -> list[CandidateRanking]:
    """Return up to ``limit`` ranked, diversified short-term scan candidates.

    The returned list is what the loop should scan instead of a hardcoded
    BTC/ETH pair. ``included=True`` entries are the picks; the full ranked list
    (including skipped names + reasons) is returned for transparency.
    """
    uni = universe or get_asset_universe()
    guard = DiversificationGuard(universe=uni, mode="shadow")
    report = guard.analyze_portfolio(positions or [])

    over_keys = {(b.dimension, b.key) for b in report.over_limit_buckets()}
    present_groups = {
        b.key for b in report.buckets if b.dimension == "correlation_group" and b.weight_pct > 0
    }
    present_sectors = {
        b.key for b in report.buckets if b.dimension == "sector" and b.weight_pct > 0
    }

    pool: list[AssetMeta] = list(uni.tradable_short_term())
    if include_btc_eth:
        for sym in _BTC_ETH:
            meta = uni.get(sym)
            if meta is not None and meta.evaluable and meta.is_tradable:
                pool.append(meta)

    ranked: list[CandidateRanking] = []
    for meta in pool:
        base = meta.symbol
        structural = meta.score or 0.0
        adjusted = structural
        reasons: list[str] = [f"structural_score={structural:.2f}"]

        if base in _BTC_ETH:
            adjusted *= _RESERVE_CLASS_PENALTY
            reasons.append("reserve-class major — penalised to limit short-term dominance")

        corr = meta.correlation_group
        sector = meta.sector
        if ("correlation_group", corr) in over_keys:
            adjusted *= _CONCENTRATION_PENALTY
            reasons.append(f"correlation_group {corr} already over cap — penalised")
        elif corr in present_groups and corr != UNKNOWN:
            adjusted *= _CROWDED_PENALTY
            reasons.append(f"correlation_group {corr} already represented — slight penalty")

        if ("sector", sector) in over_keys:
            adjusted *= _CONCENTRATION_PENALTY
            reasons.append(f"sector {sector} already over cap — penalised")
        elif sector in present_sectors and sector != UNKNOWN:
            adjusted *= _CROWDED_PENALTY
            reasons.append(f"sector {sector} already represented — slight penalty")

        ranked.append(
            CandidateRanking(
                symbol=f"{base}/{quote}",
                base=base,
                structural_score=structural,
                adjusted_score=round(adjusted, 4),
                horizon=meta.horizon,
                sector=sector,
                correlation_group=corr,
                included=False,
                reasons=tuple(reasons),
            )
        )

    ranked.sort(key=lambda c: c.adjusted_score, reverse=True)

    # Selection pass: enforce spread (cap per correlation group) and a hard
    # BTC/ETH cap so the scan can never collapse back to majors-only.
    max_btc_eth = max(1, floor(limit / 3)) if include_btc_eth else 0
    group_counts: dict[str, int] = {}
    btc_eth_count = 0
    selected: list[CandidateRanking] = []
    result: list[CandidateRanking] = []

    for cand in ranked:
        include = True
        skip_reason: str | None = None
        if len(selected) >= limit:
            include = False
            skip_reason = "below selection cut"
        elif cand.base in _BTC_ETH and btc_eth_count >= max_btc_eth:
            include = False
            skip_reason = f"BTC/ETH scan cap reached ({max_btc_eth})"
        else:
            grp = cand.correlation_group
            if grp != UNKNOWN and group_counts.get(grp, 0) >= max_same_correlation_group:
                include = False
                skip_reason = f"correlation_group {grp} spread cap reached"

        if include:
            if cand.base in _BTC_ETH:
                btc_eth_count += 1
            if cand.correlation_group != UNKNOWN:
                group_counts[cand.correlation_group] = (
                    group_counts.get(cand.correlation_group, 0) + 1
                )
            picked = CandidateRanking(
                symbol=cand.symbol,
                base=cand.base,
                structural_score=cand.structural_score,
                adjusted_score=cand.adjusted_score,
                horizon=cand.horizon,
                sector=cand.sector,
                correlation_group=cand.correlation_group,
                included=True,
                reasons=cand.reasons + ("selected",),
            )
            selected.append(picked)
            result.append(picked)
        else:
            result.append(
                CandidateRanking(
                    symbol=cand.symbol,
                    base=cand.base,
                    structural_score=cand.structural_score,
                    adjusted_score=cand.adjusted_score,
                    horizon=cand.horizon,
                    sector=cand.sector,
                    correlation_group=cand.correlation_group,
                    included=False,
                    reasons=cand.reasons + (f"skipped: {skip_reason}",),
                )
            )

    return result


def selected_symbols(rankings: list[CandidateRanking]) -> list[str]:
    """Convenience: the included scan symbols, best first."""
    return [c.symbol for c in rankings if c.included]


__all__ = [
    "CandidateRanking",
    "select_short_term_candidates",
    "selected_symbols",
]
