"""Diversification overview service — the shared CLI/API read surface.

Ties the read-only portfolio snapshot, the asset universe, the concentration
guard and the candidate selector into one explained payload that answers the
operator's questions: how broad is the book, where are the clusters, which
diversified alternatives exist, and how short-term vs reserve split.

Read-only. No execution, no estimation of missing data.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import get_settings
from app.execution.portfolio_read import PortfolioSnapshot, build_portfolio_snapshot
from app.trading.asset_universe import AssetUniverse, get_asset_universe
from app.trading.candidate_selector import select_short_term_candidates
from app.trading.diversification import (
    DiversificationGuard,
    PositionExposure,
    classify_position_horizon,
    exposures_from_snapshot,
)
from app.trading.stablecoin_risk import (
    StablecoinRiskRegistry,
    get_stablecoin_risk_registry,
)


@dataclass(frozen=True)
class _AssetRow:
    symbol: str
    base: str
    exposure_usd: float | None
    weight_pct: float | None
    exposure_basis: str
    horizon: str
    position_horizon: str
    sector: str
    narrative: str
    focus_field: str
    asset_class: str
    correlation_group: str
    risk_tier: str
    liquidity_tier: str
    is_reserve: bool
    evaluable: bool
    source: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "exposure_usd": round(self.exposure_usd, 2) if self.exposure_usd is not None else None,
            "weight_pct": round(self.weight_pct, 2) if self.weight_pct is not None else None,
            "exposure_basis": self.exposure_basis,
            "asset_horizon": self.horizon,
            "position_horizon": self.position_horizon,
            "sector": self.sector,
            "narrative": self.narrative,
            "focus_field": self.focus_field,
            "asset_class": self.asset_class,
            "correlation_group": self.correlation_group,
            "risk_tier": self.risk_tier,
            "liquidity_tier": self.liquidity_tier,
            "is_reserve": self.is_reserve,
            "evaluable": self.evaluable,
            "source": self.source,
        }


def _asset_rows(
    exposures: list[PositionExposure],
    universe: AssetUniverse,
) -> list[_AssetRow]:
    total = sum(
        e.exposure_usd for e in exposures if e.exposure_usd is not None and e.exposure_usd > 0
    )
    rows: list[_AssetRow] = []
    for e in exposures:
        meta = universe.get_or_unknown(e.symbol)
        weight = (
            (e.exposure_usd / total * 100.0) if (e.exposure_usd is not None and total > 0) else None
        )
        rows.append(
            _AssetRow(
                symbol=e.symbol,
                base=meta.symbol,
                exposure_usd=e.exposure_usd,
                weight_pct=weight,
                exposure_basis=e.exposure_basis,
                horizon=meta.horizon,
                position_horizon=classify_position_horizon(
                    source=e.source, asset_horizon=meta.horizon
                ),
                sector=meta.sector,
                narrative=meta.narrative,
                focus_field=meta.focus_field,
                asset_class=meta.asset_class,
                correlation_group=meta.correlation_group,
                risk_tier=meta.risk_tier,
                liquidity_tier=meta.liquidity_tier,
                is_reserve=meta.is_reserve,
                evaluable=meta.evaluable,
                source=e.source or "unknown",
            )
        )
    rows.sort(key=lambda r: r.weight_pct or -1.0, reverse=True)
    return rows


def _by_source(exposures: list[PositionExposure]) -> list[dict[str, object]]:
    agg: dict[str, float] = defaultdict(float)
    total = 0.0
    for e in exposures:
        if e.exposure_usd is None or e.exposure_usd <= 0:
            continue
        src = (e.source or "unknown").strip() or "unknown"
        agg[src] += e.exposure_usd
        total += e.exposure_usd
    out: list[dict[str, object]] = [
        {
            "source": src,
            "exposure_usd": round(usd, 2),
            "weight_pct": round(usd / total * 100.0, 2) if total > 0 else None,
        }
        for src, usd in agg.items()
    ]
    out.sort(key=lambda d: float(d["exposure_usd"] or 0.0), reverse=True)  # type: ignore[arg-type]
    return out


def build_universe_summary(
    universe: AssetUniverse,
    *,
    stablecoin_registry: StablecoinRiskRegistry | None = None,
) -> dict[str, object]:
    """Universe-level overview: focus fields, asset classes, reserve sleeve,
    watch-only research list and stablecoin reserve risk.

    Answers the operator's reporting asks independent of the current book:
    asset universe, focus fields, watchlist, reserve holdings, stablecoin
    liquidity/risk and the BTC/ETH core-reserve separation. Read-only.
    """
    registry = stablecoin_registry or get_stablecoin_risk_registry()
    assets = universe.all()

    by_class: dict[str, int] = defaultdict(int)
    by_focus: dict[str, int] = defaultdict(int)
    for m in assets:
        by_class[m.asset_class] += 1
        by_focus[m.focus_field] += 1

    reserve_core = sorted(m.symbol for m in assets if m.asset_class == "reserve_core")
    reserve_stable = sorted(m.symbol for m in assets if m.asset_class == "reserve_stable")

    # Watchlist = research + watch-only (everything that is NOT venue-orderable
    # but is curated context). watch_only (pre-IPO/etc.) is flagged distinctly.
    watchlist = [
        {
            "symbol": m.symbol,
            "name": m.name,
            "asset_class": m.asset_class,
            "focus_field": m.focus_field,
            "lifecycle": m.lifecycle,
            "category": m.category,
            "is_orderable": m.is_orderable,
        }
        for m in assets
        if m.asset_class in {"watch_only", "research"}
    ]
    watchlist.sort(key=lambda r: (r["asset_class"], str(r["focus_field"]), str(r["symbol"])))

    stablecoin_reserve = [
        registry.assess(m.symbol).to_json_dict()
        for m in assets
        if m.asset_class == "reserve_stable"
    ]

    return {
        "universe_size": len(assets),
        "asset_class_breakdown": dict(sorted(by_class.items())),
        "focus_field_breakdown": dict(sorted(by_focus.items())),
        "reserve": {
            "core": reserve_core,  # BTC/ETH strategic core reserve
            "stablecoin": reserve_stable,
        },
        "watchlist": watchlist,
        "watch_only_count": sum(1 for r in watchlist if r["asset_class"] == "watch_only"),
        "stablecoin_reserve_risk": stablecoin_reserve,
    }


def build_diversification_overview_from_snapshot(
    snapshot: PortfolioSnapshot,
    *,
    universe: AssetUniverse | None = None,
) -> dict[str, object]:
    """Pure builder — usable in tests without market/DB access."""
    uni = universe or get_asset_universe()
    div_settings = get_settings().diversification
    guard = DiversificationGuard(universe=uni, mode=div_settings.mode)

    exposures = exposures_from_snapshot(snapshot)
    report = guard.analyze_portfolio(exposures)
    rows = _asset_rows(exposures, uni)
    candidates = select_short_term_candidates(
        positions=exposures,
        universe=uni,
        limit=div_settings.universe_scan_limit,
    )

    return {
        "report_type": "diversification_overview",
        "generated_at": datetime.now(UTC).isoformat(),
        "guard_enabled": div_settings.enabled,
        "guard_mode": div_settings.mode,
        "universe_scan_enabled": div_settings.universe_scan_enabled,
        "portfolio": {
            "source": snapshot.source,
            "available": snapshot.available,
            "error": snapshot.error,
            "cash_usd": round(snapshot.cash_usd, 2),
            "total_equity_usd": round(snapshot.total_equity_usd, 2),
            "position_count": snapshot.position_count,
        },
        "concentration": report.to_json_dict(),
        "asset_distribution": [r.to_json_dict() for r in rows],
        "by_source": _by_source(exposures),
        "candidates": [c.to_json_dict() for c in candidates],
        "cluster_warnings": list(report.warnings),
        "universe_size": len(uni.all()),
        "universe_summary": build_universe_summary(uni),
    }


async def build_diversification_overview(
    *,
    audit_path: str | Path = "artifacts/paper_execution_audit.jsonl",
    provider: str | None = None,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
    universe: AssetUniverse | None = None,
) -> dict[str, object]:
    """Build the overview from a freshly read portfolio snapshot."""
    resolved_provider = provider if provider is not None else get_settings().market_data_provider
    snapshot = await build_portfolio_snapshot(
        audit_path=audit_path,
        provider=resolved_provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return build_diversification_overview_from_snapshot(snapshot, universe=universe)


__all__ = [
    "build_diversification_overview",
    "build_diversification_overview_from_snapshot",
    "build_universe_summary",
]
