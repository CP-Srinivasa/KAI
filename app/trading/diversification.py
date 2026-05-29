"""Diversification + concentration guard for the short-term trading sleeve.

This is the categorical, pre-trade complement to the quantitative
``app/risk/portfolio_risk.py`` VaR/ES engine.  Where that engine needs return
histories, this guard works purely on current portfolio weights plus the
asset-universe metadata, so it is fast, deterministic, and degrades gracefully
when correlation/return data is unknown.

What it answers (the goal's acceptance criteria):
    * How broadly is the book spread (per asset / sector / narrative /
      correlation group / horizon)?
    * Where are the cluster risks?
    * If we add ``candidate`` for ``notional_usd``, does that breach a
      concentration cap — and if so, what better-diversified alternatives exist?

Safety: the guard never executes anything. It returns a *recommendation*
(``allow`` | ``limit`` | ``reject`` | ``not_evaluable``) plus an ``enforced``
flag. Callers in shadow mode log the recommendation; only enforce mode blocks.
Missing data is reported as ``not_evaluable`` — never silently treated as OK
and never estimated.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from app.trading.asset_universe import (
    UNKNOWN,
    AssetUniverse,
    UniverseLimits,
    base_symbol,
    get_asset_universe,
)

logger = logging.getLogger(__name__)

# How a position's trade horizon is decided. Today the paper engine *is* the
# short-term trading book, so an untagged position is short-term and the
# concentration caps apply to it. A position whose source marks it as a
# deliberate reserve allocation is excluded from the short-term caps — that is
# the short/long separation hook.
_RESERVE_SOURCE_MARKERS = ("reserve", "long_term", "hodl", "treasury")

_DEFAULT_ALTERNATIVES = 4


def classify_position_horizon(*, source: str, asset_horizon: str) -> str:
    """Decide a *position's* horizon (not the asset's nature).

    Order of precedence:
      1. An explicit reserve marker in the position source → long_term_reserve.
      2. Otherwise the active paper book is treated as short_term, regardless of
         the asset's default classification — a BTC scalp is a short-term trade
         even though BTC is a reserve-class asset. This is what makes the
         BTC/ETH short-term cap actually bite.
    """
    src = (source or "").strip().lower()
    if any(marker in src for marker in _RESERVE_SOURCE_MARKERS):
        return "long_term_reserve"
    return "short_term"


def _quote_currency(symbol: str) -> str:
    s = symbol.strip().upper()
    for sep in ("/", "-", ":"):
        if sep in s:
            return s.split(sep, 1)[1] or UNKNOWN
    for quote in ("USDT", "USDC", "BUSD", "USD", "EUR"):
        if s.endswith(quote) and len(s) > len(quote):
            return quote
    return UNKNOWN


@dataclass(frozen=True)
class PositionExposure:
    """One position reduced to what the guard needs: symbol + USD exposure."""

    symbol: str  # full pair, e.g. BTC/USDT
    exposure_usd: float | None  # None = could not be priced (not evaluable)
    exposure_basis: str  # mark_to_market | cost | none
    source: str = ""


@dataclass(frozen=True)
class ConcentrationBucket:
    # dimension ∈ asset | sector | narrative | focus_field | asset_class |
    #             correlation_group | horizon | exchange | stablecoin_quote
    # focus_field / asset_class are observational (no cap → never over_limit);
    # they surface thematic/class clustering without changing enforce behaviour.
    dimension: str
    key: str
    exposure_usd: float
    weight_pct: float
    limit_pct: float | None
    over_limit: bool

    def to_json_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "key": self.key,
            "exposure_usd": round(self.exposure_usd, 2),
            "weight_pct": round(self.weight_pct, 2),
            "limit_pct": self.limit_pct,
            "over_limit": self.over_limit,
        }


@dataclass(frozen=True)
class ConcentrationReport:
    generated_at_utc: str
    short_term_gross_usd: float
    reserve_gross_usd: float
    total_gross_usd: float
    priced_position_count: int
    unpriced_position_count: int
    buckets: tuple[ConcentrationBucket, ...]
    warnings: tuple[str, ...]
    btc_eth_short_term_pct: float | None
    horizon_split_pct: dict[str, float]
    evaluable: bool

    def over_limit_buckets(self) -> list[ConcentrationBucket]:
        return [b for b in self.buckets if b.over_limit]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "diversification_concentration",
            "generated_at": self.generated_at_utc,
            "short_term_gross_usd": round(self.short_term_gross_usd, 2),
            "reserve_gross_usd": round(self.reserve_gross_usd, 2),
            "total_gross_usd": round(self.total_gross_usd, 2),
            "priced_position_count": self.priced_position_count,
            "unpriced_position_count": self.unpriced_position_count,
            "btc_eth_short_term_pct": (
                round(self.btc_eth_short_term_pct, 2)
                if self.btc_eth_short_term_pct is not None
                else None
            ),
            "horizon_split_pct": {k: round(v, 2) for k, v in self.horizon_split_pct.items()},
            "buckets": [b.to_json_dict() for b in self.buckets],
            "warnings": list(self.warnings),
            "evaluable": self.evaluable,
        }


@dataclass(frozen=True)
class AlternativeCandidate:
    symbol: str
    score: float
    sector: str
    correlation_group: str
    reason: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "score": self.score,
            "sector": self.sector,
            "correlation_group": self.correlation_group,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DiversificationDecision:
    candidate_symbol: str
    action: str  # allow | limit | reject | not_evaluable
    enforced: bool  # whether this decision blocks (enforce mode + non-allow)
    mode: str  # shadow | enforce
    reasons: tuple[str, ...]
    breached: tuple[ConcentrationBucket, ...]
    alternatives: tuple[AlternativeCandidate, ...]
    projected_single_asset_pct: float | None = None
    projected_btc_eth_pct: float | None = None

    @property
    def blocks(self) -> bool:
        return self.enforced and self.action in {"reject"}

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "diversification_decision",
            "candidate_symbol": self.candidate_symbol,
            "action": self.action,
            "enforced": self.enforced,
            "blocks": self.blocks,
            "mode": self.mode,
            "reasons": list(self.reasons),
            "breached": [b.to_json_dict() for b in self.breached],
            "alternatives": [a.to_json_dict() for a in self.alternatives],
            "projected_single_asset_pct": (
                round(self.projected_single_asset_pct, 2)
                if self.projected_single_asset_pct is not None
                else None
            ),
            "projected_btc_eth_pct": (
                round(self.projected_btc_eth_pct, 2)
                if self.projected_btc_eth_pct is not None
                else None
            ),
        }


_BTC_ETH = frozenset({"BTC", "ETH"})


class DiversificationGuard:
    """Categorical concentration analysis + pre-trade candidate evaluation."""

    def __init__(
        self,
        *,
        universe: AssetUniverse | None = None,
        limits: UniverseLimits | None = None,
        mode: str = "shadow",
    ) -> None:
        self._universe = universe or get_asset_universe()
        self._limits = limits or self._universe.limits
        self._mode = mode if mode in {"shadow", "enforce"} else "shadow"

    @property
    def mode(self) -> str:
        return self._mode

    # ---------------------------------------------------------------- analysis
    def analyze_portfolio(self, positions: list[PositionExposure]) -> ConcentrationReport:
        """Build the full concentration report for the current book."""
        now = datetime.now(UTC).isoformat()
        warnings: list[str] = []

        priced = [p for p in positions if p.exposure_usd is not None and p.exposure_usd > 0]
        unpriced = [p for p in positions if p.exposure_usd is None]
        if unpriced:
            warnings.append(
                f"unpriced_positions:{len(unpriced)} excluded from concentration math "
                "(market data unavailable — not evaluable, not estimated)"
            )

        # Split into short-term sleeve vs reserve sleeve.
        short_exposure: dict[str, float] = defaultdict(float)  # dimension::key -> usd
        short_term_gross = 0.0
        reserve_gross = 0.0
        horizon_gross: dict[str, float] = defaultdict(float)
        btc_eth_short = 0.0

        for p in priced:
            exp = float(p.exposure_usd or 0.0)
            meta = self._universe.get_or_unknown(p.symbol)
            pos_horizon = classify_position_horizon(source=p.source, asset_horizon=meta.horizon)
            horizon_gross[pos_horizon] += exp
            if pos_horizon == "long_term_reserve":
                reserve_gross += exp
                continue
            # short-term sleeve — feeds the concentration caps
            short_term_gross += exp
            asset_key = meta.symbol
            short_exposure[f"asset::{asset_key}"] += exp
            short_exposure[f"sector::{meta.sector}"] += exp
            short_exposure[f"narrative::{meta.narrative}"] += exp
            short_exposure[f"focus_field::{meta.focus_field}"] += exp
            short_exposure[f"asset_class::{meta.asset_class}"] += exp
            short_exposure[f"correlation_group::{meta.correlation_group}"] += exp
            short_exposure[f"stablecoin_quote::{_quote_currency(p.symbol)}"] += exp
            short_exposure[f"exchange::{(p.source or 'paper').strip().lower() or 'paper'}"] += exp
            if asset_key in _BTC_ETH:
                btc_eth_short += exp

        total_gross = short_term_gross + reserve_gross

        buckets = self._build_buckets(short_exposure, short_term_gross)
        btc_eth_pct = (btc_eth_short / short_term_gross * 100.0) if short_term_gross > 0 else None
        if btc_eth_pct is not None and btc_eth_pct > self._limits.max_btc_eth_short_term_pct:
            warnings.append(
                f"btc_eth_short_term_overweight:{btc_eth_pct:.1f}% > "
                f"{self._limits.max_btc_eth_short_term_pct:.0f}% cap"
            )
        for b in buckets:
            if b.over_limit:
                warnings.append(
                    f"cluster:{b.dimension}={b.key} at {b.weight_pct:.1f}% "
                    f"(> {b.limit_pct:.0f}% cap)"
                )

        horizon_split = (
            {h: (g / total_gross * 100.0) for h, g in horizon_gross.items()}
            if total_gross > 0
            else {}
        )

        evaluable = bool(priced)
        if not evaluable:
            warnings.append("no_priced_positions — concentration not evaluable")

        return ConcentrationReport(
            generated_at_utc=now,
            short_term_gross_usd=short_term_gross,
            reserve_gross_usd=reserve_gross,
            total_gross_usd=total_gross,
            priced_position_count=len(priced),
            unpriced_position_count=len(unpriced),
            buckets=tuple(buckets),
            warnings=tuple(warnings),
            btc_eth_short_term_pct=btc_eth_pct,
            horizon_split_pct=dict(horizon_split),
            evaluable=evaluable,
        )

    def _limit_for_dimension(self, dimension: str) -> float | None:
        return {
            "asset": self._limits.max_single_asset_pct,
            "sector": self._limits.max_sector_pct,
            "narrative": self._limits.max_narrative_pct,
            "correlation_group": self._limits.max_correlation_group_pct,
            "exchange": self._limits.max_exchange_pct,
            "stablecoin_quote": self._limits.max_stablecoin_quote_pct,
        }.get(dimension)

    def _build_buckets(self, exposure: dict[str, float], gross: float) -> list[ConcentrationBucket]:
        buckets: list[ConcentrationBucket] = []
        for compound_key, exp in exposure.items():
            dimension, _, key = compound_key.partition("::")
            limit = self._limit_for_dimension(dimension)
            weight = (exp / gross * 100.0) if gross > 0 else 0.0
            over = bool(limit is not None and weight > limit and key != UNKNOWN)
            buckets.append(
                ConcentrationBucket(
                    dimension=dimension,
                    key=key,
                    exposure_usd=exp,
                    weight_pct=weight,
                    limit_pct=limit,
                    over_limit=over,
                )
            )
        buckets.sort(key=lambda b: (b.dimension, -b.weight_pct))
        return buckets

    # --------------------------------------------------------- candidate eval
    def evaluate_candidate(
        self,
        positions: list[PositionExposure],
        *,
        candidate_symbol: str,
        notional_usd: float | None,
        max_alternatives: int = _DEFAULT_ALTERNATIVES,
    ) -> DiversificationDecision:
        """Project adding ``candidate_symbol`` for ``notional_usd`` and decide."""
        meta = self._universe.get(candidate_symbol)
        cand_base = base_symbol(candidate_symbol)

        if notional_usd is None or notional_usd <= 0 or meta is None or not meta.evaluable:
            reason = (
                "candidate not in universe or not evaluable"
                if (meta is None or not meta.evaluable)
                else "notional unavailable"
            )
            return DiversificationDecision(
                candidate_symbol=candidate_symbol,
                action="not_evaluable",
                enforced=False,
                mode=self._mode,
                reasons=(reason + " — diversification not assessed (no estimate made)",),
                breached=(),
                alternatives=(),
            )

        report = self.analyze_portfolio(positions)
        new_short_gross = report.short_term_gross_usd + notional_usd

        def projected_pct(current_usd: float) -> float:
            if new_short_gross <= 0:
                return 0.0
            return (current_usd + notional_usd) / new_short_gross * 100.0

        def current_usd_for(dimension: str, key: str) -> float:
            for b in report.buckets:
                if b.dimension == dimension and b.key == key:
                    return b.exposure_usd
            return 0.0

        breached: list[ConcentrationBucket] = []
        reasons: list[str] = []

        checks = [
            ("asset", cand_base, self._limits.max_single_asset_pct),
            ("sector", meta.sector, self._limits.max_sector_pct),
            ("narrative", meta.narrative, self._limits.max_narrative_pct),
            ("correlation_group", meta.correlation_group, self._limits.max_correlation_group_pct),
        ]
        proj_single = None
        for dimension, key, limit in checks:
            if key == UNKNOWN:
                continue
            proj = projected_pct(current_usd_for(dimension, key))
            if dimension == "asset":
                proj_single = proj
            if proj > limit:
                breached.append(
                    ConcentrationBucket(
                        dimension=dimension,
                        key=key,
                        exposure_usd=current_usd_for(dimension, key) + notional_usd,
                        weight_pct=proj,
                        limit_pct=limit,
                        over_limit=True,
                    )
                )
                reasons.append(f"{dimension}={key} would reach {proj:.1f}% (> {limit:.0f}% cap)")

        # BTC/ETH short-term headline cap.
        proj_btc_eth = None
        if cand_base in _BTC_ETH:
            cur_btc_eth = sum(current_usd_for("asset", a) for a in _BTC_ETH)
            proj_btc_eth = (
                (cur_btc_eth + notional_usd) / new_short_gross * 100.0
                if new_short_gross > 0
                else 0.0
            )
            if proj_btc_eth > self._limits.max_btc_eth_short_term_pct:
                breached.append(
                    ConcentrationBucket(
                        dimension="btc_eth_short_term",
                        key="BTC+ETH",
                        exposure_usd=cur_btc_eth + notional_usd,
                        weight_pct=proj_btc_eth,
                        limit_pct=self._limits.max_btc_eth_short_term_pct,
                        over_limit=True,
                    )
                )
                reasons.append(
                    f"BTC/ETH short-term would reach {proj_btc_eth:.1f}% "
                    f"(> {self._limits.max_btc_eth_short_term_pct:.0f}% cap) — "
                    "prefer a diversified alternative"
                )

        action = self._decide_action(
            breached=breached, cand_base=cand_base, proj_btc_eth=proj_btc_eth
        )
        alternatives = (
            self._alternatives(report, meta, max_alternatives)
            if action in {"reject", "limit"}
            else ()
        )
        if action == "allow":
            reasons.append("within all concentration caps")

        enforced = self._mode == "enforce" and action == "reject"
        return DiversificationDecision(
            candidate_symbol=candidate_symbol,
            action=action,
            enforced=enforced,
            mode=self._mode,
            reasons=tuple(reasons),
            breached=tuple(breached),
            alternatives=alternatives,
            projected_single_asset_pct=proj_single,
            projected_btc_eth_pct=proj_btc_eth,
        )

    def _decide_action(
        self,
        *,
        breached: list[ConcentrationBucket],
        cand_base: str,
        proj_btc_eth: float | None,
    ) -> str:
        if not breached:
            return "allow"
        dims = {b.dimension for b in breached}
        # The headline fight: a fresh BTC/ETH add that pushes the BTC/ETH
        # short-term cap, or a single-asset breach, is a hard reject.
        if "btc_eth_short_term" in dims or "asset" in dims:
            return "reject"
        # Softer cluster breaches (sector/narrative/corr group) → advise smaller
        # size / alternative rather than a hard block.
        return "limit"

    def _alternatives(
        self,
        report: ConcentrationReport,
        candidate: object,
        max_alternatives: int,
    ) -> tuple[AlternativeCandidate, ...]:
        """Diversified alternatives: tradable short-term names that do NOT add to
        an already-breached cluster, ranked by structural score."""
        over_keys = {(b.dimension, b.key) for b in report.over_limit_buckets()}
        # Also avoid the candidate's own correlation group / sector if it was the
        # reason we are here.
        cand_corr = getattr(candidate, "correlation_group", UNKNOWN)
        cand_symbol = getattr(candidate, "symbol", "")

        out: list[AlternativeCandidate] = []
        for m in self._universe.tradable_short_term():
            if m.symbol == cand_symbol or m.symbol in _BTC_ETH:
                continue  # never propose the majors we are diversifying away from
            if ("correlation_group", m.correlation_group) in over_keys:
                continue
            if ("sector", m.sector) in over_keys:
                continue
            same_cluster = m.correlation_group == cand_corr and cand_corr != UNKNOWN
            reason = (
                "diversifies away from breached cluster"
                if not same_cluster
                else "same correlation group — weaker diversification"
            )
            # Prefer names in a different correlation group from the candidate.
            out.append(
                AlternativeCandidate(
                    symbol=m.symbol,
                    score=m.score or 0.0,
                    sector=m.sector,
                    correlation_group=m.correlation_group,
                    reason=reason,
                )
            )

        out.sort(
            key=lambda a: (
                a.correlation_group != cand_corr,  # different group first
                a.score,
            ),
            reverse=True,
        )
        # Keep distinct correlation groups near the top for real spread.
        return tuple(out[:max_alternatives])


# --------------------------------------------------------------------- adapters
def exposures_from_snapshot(snapshot: object) -> list[PositionExposure]:
    """Build PositionExposure list from a portfolio_read.PortfolioSnapshot.

    Uses mark-to-market value when available, else falls back to cost basis
    (quantity * avg_entry_price), else marks the position unpriced (None).
    """
    positions = getattr(snapshot, "positions", ()) or ()
    out: list[PositionExposure] = []
    for p in positions:
        mv = getattr(p, "market_value_usd", None)
        if isinstance(mv, (int, float)):
            out.append(
                PositionExposure(
                    symbol=getattr(p, "symbol", ""),
                    exposure_usd=abs(float(mv)),
                    exposure_basis="mark_to_market",
                    source=getattr(p, "source", "") or "",
                )
            )
            continue
        qty = getattr(p, "quantity", None)
        entry = getattr(p, "avg_entry_price", None)
        if isinstance(qty, (int, float)) and isinstance(entry, (int, float)):
            out.append(
                PositionExposure(
                    symbol=getattr(p, "symbol", ""),
                    exposure_usd=abs(float(qty) * float(entry)),
                    exposure_basis="cost",
                    source=getattr(p, "source", "") or "",
                )
            )
            continue
        out.append(
            PositionExposure(
                symbol=getattr(p, "symbol", ""),
                exposure_usd=None,
                exposure_basis="none",
                source=getattr(p, "source", "") or "",
            )
        )
    return out


def exposures_from_paper_portfolio(portfolio: object) -> list[PositionExposure]:
    """Build PositionExposure list from a PaperPortfolio (cost basis only).

    Used inside the trading loop hot path: no market calls, exposure is
    quantity * avg_entry_price (consistent with TradingLoop._write_db).
    """
    positions = getattr(portfolio, "positions", {}) or {}
    out: list[PositionExposure] = []
    for symbol, pos in positions.items():
        qty = getattr(pos, "quantity", None)
        entry = getattr(pos, "avg_entry_price", None)
        source = getattr(pos, "source", "") or ""
        if isinstance(qty, (int, float)) and isinstance(entry, (int, float)):
            out.append(
                PositionExposure(
                    symbol=str(symbol),
                    exposure_usd=abs(float(qty) * float(entry)),
                    exposure_basis="cost",
                    source=source,
                )
            )
        else:
            out.append(
                PositionExposure(
                    symbol=str(symbol),
                    exposure_usd=None,
                    exposure_basis="none",
                    source=source,
                )
            )
    return out


__all__ = [
    "PositionExposure",
    "ConcentrationBucket",
    "ConcentrationReport",
    "AlternativeCandidate",
    "DiversificationDecision",
    "DiversificationGuard",
    "classify_position_horizon",
    "exposures_from_snapshot",
    "exposures_from_paper_portfolio",
]
