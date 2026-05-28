"""Asset universe — the watchlist enriched with trading-decision dimensions.

The watchlist (``monitor/watchlists.yml``) only carries symbol/name/aliases/
tags/category.  It does NOT know a coin's trade *horizon*, sector, narrative,
or its risk/liquidity/volatility tiers, tradability or data quality.  Those are
exactly the dimensions an asset-selection layer needs to break out of the
BTC/ETH default loop and spread risk across uncorrelated names.

This module merges the watchlist with an operator-curated overlay
(``config/asset_universe.yaml``) into a typed :class:`AssetUniverse`.

Honesty contract (KAI rule "fehlende Daten = nicht bewertbar, niemals
schätzen"):
    * A dimension that is neither in the overlay nor inferable from tags stays
      ``"unknown"``.
    * ``score`` is a *structural suitability* score (liquidity, risk, vol fit,
      data quality, tradability) — NOT a price/return prediction.  When too few
      dimensions are known the asset is marked ``evaluable=False`` and ``score``
      is ``None``.  We never fabricate a number for an asset nobody curated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.analysis.keywords.watchlist import WatchlistEntry, load_watchlist

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OVERLAY_PATH = _REPO_ROOT / "config" / "asset_universe.yaml"
_DEFAULT_WATCHLIST_PATH = _REPO_ROOT / "monitor" / "watchlists.yml"

UNKNOWN = "unknown"

HORIZONS = frozenset({"short_term", "mid_term", "long_term_reserve", UNKNOWN})

# Only tradable/research asset categories belong in the universe. The watchlist
# also carries persons/topics (for news matching) — those are not assets.
_ASSET_CATEGORIES = frozenset({"crypto", "equity", "etf", "macro"})

# Tier → structural value maps. Higher = structurally better for *diversified
# short-term trading*. These are deterministic and documented, not learned.
_LIQUIDITY_VALUE = {
    "very_high": 1.0,
    "high": 0.8,
    "medium": 0.55,
    "low": 0.3,
    "very_low": 0.15,
}
# Lower risk scores higher (risk-adjusted preference).
_RISK_VALUE = {
    "low": 1.0,
    "medium": 0.7,
    "high": 0.4,
    "extreme": 0.15,
}
# Short-term trading wants *some* volatility, but extremes are penalised.
_VOLATILITY_VALUE = {
    "very_low": 0.3,
    "low": 0.5,
    "medium": 1.0,
    "high": 0.85,
    "very_high": 0.55,
}
_DATA_QUALITY_VALUE = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.3,
}

# Weights for the structural score. Only dimensions whose tier is *known*
# contribute; weights of unknown dimensions are dropped and the rest are
# renormalised. Tradability is a hard gate handled separately, not a weight.
_SCORE_WEIGHTS = {
    "liquidity": 0.30,
    "risk": 0.30,
    "volatility": 0.20,
    "data_quality": 0.20,
}

# An asset must have at least this many known scoring dimensions to be
# considered evaluable. Below it we refuse to emit a score.
_MIN_KNOWN_DIMENSIONS = 2

_STABLE_TAGS = frozenset({"stablecoin"})


@dataclass(frozen=True)
class AssetMeta:
    """A single asset enriched with trading-decision dimensions."""

    symbol: str  # canonical base, e.g. "BTC"
    name: str
    category: str  # crypto | equity | etf | macro | unknown
    horizon: str  # short_term | mid_term | long_term_reserve | unknown
    sector: str
    narrative: str
    risk_tier: str
    liquidity_tier: str
    volatility_tier: str
    data_quality: str
    tradable: str  # "true" | "false" | "unknown" (tri-state)
    correlation_group: str
    tags: tuple[str, ...]
    is_stablecoin: bool
    is_reserve: bool
    evaluable: bool
    score: float | None
    score_breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def is_tradable(self) -> bool:
        """True only when explicitly marked tradable. ``unknown`` is NOT tradable."""
        return self.tradable == "true"

    @property
    def is_short_term(self) -> bool:
        return self.horizon == "short_term"

    def to_json_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "category": self.category,
            "horizon": self.horizon,
            "sector": self.sector,
            "narrative": self.narrative,
            "risk_tier": self.risk_tier,
            "liquidity_tier": self.liquidity_tier,
            "volatility_tier": self.volatility_tier,
            "data_quality": self.data_quality,
            "tradable": self.tradable,
            "correlation_group": self.correlation_group,
            "tags": list(self.tags),
            "is_stablecoin": self.is_stablecoin,
            "is_reserve": self.is_reserve,
            "evaluable": self.evaluable,
            "score": self.score,
            "score_breakdown": dict(self.score_breakdown),
        }


@dataclass(frozen=True)
class UniverseLimits:
    """Concentration limits for the short-term sleeve (percent of short-term gross)."""

    max_single_asset_pct: float = 25.0
    max_btc_eth_short_term_pct: float = 40.0
    max_sector_pct: float = 45.0
    max_narrative_pct: float = 45.0
    max_correlation_group_pct: float = 50.0
    max_exchange_pct: float = 100.0
    max_stablecoin_quote_pct: float = 100.0

    @classmethod
    def from_mapping(cls, raw: object) -> UniverseLimits:
        if not isinstance(raw, dict):
            return cls()
        kwargs: dict[str, float] = {}
        for fname in (
            "max_single_asset_pct",
            "max_btc_eth_short_term_pct",
            "max_sector_pct",
            "max_narrative_pct",
            "max_correlation_group_pct",
            "max_exchange_pct",
            "max_stablecoin_quote_pct",
        ):
            value = raw.get(fname)
            if isinstance(value, (int, float)) and value > 0:
                kwargs[fname] = float(value)
        return cls(**kwargs)


def base_symbol(symbol: str) -> str:
    """Normalise ``BTC/USDT``, ``BTC-USDT``, ``BTCUSDT`` → ``BTC`` (best-effort)."""
    s = symbol.strip().upper()
    if not s:
        return s
    for sep in ("/", "-", ":"):
        if sep in s:
            return s.split(sep, 1)[0]
    # Strip a known quote suffix only if a clear remainder exists.
    for quote in ("USDT", "USDC", "BUSD", "USD", "EUR"):
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


def _norm(value: object, *, allowed: frozenset[str] | None = None) -> str:
    if value is None:
        return UNKNOWN
    text = str(value).strip().lower()
    if not text:
        return UNKNOWN
    if allowed is not None and text not in allowed:
        return UNKNOWN
    return text


def _norm_tradable(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower() if value is not None else ""
    if text in {"true", "yes", "1"}:
        return "true"
    if text in {"false", "no", "0"}:
        return "false"
    return UNKNOWN


def _compute_score(
    *,
    liquidity_tier: str,
    risk_tier: str,
    volatility_tier: str,
    data_quality: str,
) -> tuple[bool, float | None, dict[str, float]]:
    """Structural suitability score from KNOWN dimensions only.

    Returns (evaluable, score, breakdown). Unknown dimensions are dropped and
    the remaining weights renormalised. Below ``_MIN_KNOWN_DIMENSIONS`` known
    dimensions we refuse to score (evaluable=False, score=None).
    """
    contributions: dict[str, float] = {}
    weights: dict[str, float] = {}
    lookups = {
        "liquidity": (liquidity_tier, _LIQUIDITY_VALUE),
        "risk": (risk_tier, _RISK_VALUE),
        "volatility": (volatility_tier, _VOLATILITY_VALUE),
        "data_quality": (data_quality, _DATA_QUALITY_VALUE),
    }
    for dim, (tier, table) in lookups.items():
        if tier in table:
            contributions[dim] = table[tier]
            weights[dim] = _SCORE_WEIGHTS[dim]

    if len(contributions) < _MIN_KNOWN_DIMENSIONS:
        return False, None, {}

    total_weight = sum(weights.values())
    if total_weight <= 0:
        return False, None, {}

    score = sum(contributions[dim] * weights[dim] for dim in contributions) / total_weight
    breakdown = {dim: round(contributions[dim], 4) for dim in contributions}
    return True, round(score, 4), breakdown


class AssetUniverse:
    """Read-only registry of :class:`AssetMeta`, keyed by canonical base symbol."""

    def __init__(self, assets: dict[str, AssetMeta], limits: UniverseLimits) -> None:
        self._assets = assets
        self._limits = limits

    @property
    def limits(self) -> UniverseLimits:
        return self._limits

    @classmethod
    def load(
        cls,
        *,
        watchlist_path: str | Path | None = None,
        overlay_path: str | Path | None = None,
    ) -> AssetUniverse:
        """Build the universe by merging the watchlist with the overlay.

        Missing files degrade gracefully: no watchlist → empty universe; no
        overlay → every asset uses ``defaults`` (mostly ``unknown``). Never
        raises on absent/malformed config.
        """
        wl_path = Path(watchlist_path) if watchlist_path else _DEFAULT_WATCHLIST_PATH
        ov_path = Path(overlay_path) if overlay_path else _DEFAULT_OVERLAY_PATH

        try:
            entries = load_watchlist(wl_path)
        except Exception as exc:  # noqa: BLE001 — config must never crash callers
            logger.warning("[UNIVERSE] watchlist load failed (%s); empty universe", exc)
            entries = []

        overlay_doc = _load_overlay(ov_path)
        defaults = overlay_doc.get("defaults", {}) if isinstance(overlay_doc, dict) else {}
        asset_overlay = overlay_doc.get("assets", {}) if isinstance(overlay_doc, dict) else {}
        limits = UniverseLimits.from_mapping(
            overlay_doc.get("limits") if isinstance(overlay_doc, dict) else None
        )
        if not isinstance(defaults, dict):
            defaults = {}
        if not isinstance(asset_overlay, dict):
            asset_overlay = {}

        assets: dict[str, AssetMeta] = {}
        for entry in entries:
            if entry.category not in _ASSET_CATEGORIES:
                continue  # persons/topics live in the watchlist but are not assets
            meta = _build_meta(entry, defaults=defaults, overlay=asset_overlay)
            assets[meta.symbol] = meta

        # Overlay-only symbols (curated but not on the watchlist) are still
        # surfaced so research-only context is not lost.
        for raw_symbol in asset_overlay:
            sym = base_symbol(str(raw_symbol))
            if sym and sym not in assets:
                synthetic = WatchlistEntry(
                    symbol=sym,
                    name=sym,
                    aliases=frozenset(),
                    tags=(),
                    category=UNKNOWN,
                )
                assets[sym] = _build_meta(synthetic, defaults=defaults, overlay=asset_overlay)

        return cls(assets, limits)

    def get(self, symbol: str) -> AssetMeta | None:
        return self._assets.get(base_symbol(symbol))

    def get_or_unknown(self, symbol: str) -> AssetMeta:
        """Always return a meta — an unknown symbol yields an all-``unknown`` stub."""
        existing = self.get(symbol)
        if existing is not None:
            return existing
        sym = base_symbol(symbol)
        return AssetMeta(
            symbol=sym,
            name=sym,
            category=UNKNOWN,
            horizon=UNKNOWN,
            sector=UNKNOWN,
            narrative=UNKNOWN,
            risk_tier=UNKNOWN,
            liquidity_tier=UNKNOWN,
            volatility_tier=UNKNOWN,
            data_quality=UNKNOWN,
            tradable=UNKNOWN,
            correlation_group=UNKNOWN,
            tags=(),
            is_stablecoin=False,
            is_reserve=False,
            evaluable=False,
            score=None,
        )

    def all(self) -> list[AssetMeta]:
        return list(self._assets.values())

    def tradable_short_term(self) -> list[AssetMeta]:
        """Tradable, short-term, evaluable, non-stablecoin candidates, best score first."""
        out = [
            m
            for m in self._assets.values()
            if m.is_tradable and m.is_short_term and m.evaluable and not m.is_stablecoin
        ]
        out.sort(key=lambda m: (m.score or 0.0), reverse=True)
        return out


def _load_overlay(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.info("[UNIVERSE] overlay %s missing; using watchlist defaults", path)
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            doc = yaml.safe_load(handle) or {}
        return doc if isinstance(doc, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[UNIVERSE] overlay %s unreadable (%s); using defaults", path, exc)
        return {}


def _merged_field(
    field_name: str,
    *,
    overlay_entry: dict[str, Any],
    defaults: dict[str, Any],
) -> object:
    if field_name in overlay_entry and overlay_entry[field_name] is not None:
        return overlay_entry[field_name]
    return defaults.get(field_name)


def _build_meta(
    entry: WatchlistEntry,
    *,
    defaults: dict[str, Any],
    overlay: dict[str, Any],
) -> AssetMeta:
    overlay_entry = overlay.get(entry.symbol)
    if not isinstance(overlay_entry, dict):
        overlay_entry = overlay.get(entry.symbol.upper())
    if not isinstance(overlay_entry, dict):
        overlay_entry = {}

    horizon = _norm(
        _merged_field("horizon", overlay_entry=overlay_entry, defaults=defaults),
        allowed=HORIZONS,
    )
    sector = _norm(_merged_field("sector", overlay_entry=overlay_entry, defaults=defaults))
    narrative = _norm(_merged_field("narrative", overlay_entry=overlay_entry, defaults=defaults))
    risk_tier = _norm(_merged_field("risk_tier", overlay_entry=overlay_entry, defaults=defaults))
    liquidity_tier = _norm(
        _merged_field("liquidity_tier", overlay_entry=overlay_entry, defaults=defaults)
    )
    volatility_tier = _norm(
        _merged_field("volatility_tier", overlay_entry=overlay_entry, defaults=defaults)
    )
    data_quality = _norm(
        _merged_field("data_quality", overlay_entry=overlay_entry, defaults=defaults)
    )
    correlation_group = _norm(
        _merged_field("correlation_group", overlay_entry=overlay_entry, defaults=defaults)
    )
    tradable = _norm_tradable(
        _merged_field("tradable", overlay_entry=overlay_entry, defaults=defaults)
    )

    is_stablecoin = sector == "stablecoin" or bool(_STABLE_TAGS.intersection(entry.tags))
    is_reserve = horizon == "long_term_reserve"

    evaluable, score, breakdown = _compute_score(
        liquidity_tier=liquidity_tier,
        risk_tier=risk_tier,
        volatility_tier=volatility_tier,
        data_quality=data_quality,
    )

    return AssetMeta(
        symbol=entry.symbol,
        name=entry.name or entry.symbol,
        category=_norm(entry.category) or UNKNOWN,
        horizon=horizon,
        sector=sector,
        narrative=narrative,
        risk_tier=risk_tier,
        liquidity_tier=liquidity_tier,
        volatility_tier=volatility_tier,
        data_quality=data_quality,
        tradable=tradable,
        correlation_group=correlation_group,
        tags=tuple(entry.tags),
        is_stablecoin=is_stablecoin,
        is_reserve=is_reserve,
        evaluable=evaluable,
        score=score,
        score_breakdown=breakdown,
    )


_CACHED_UNIVERSE: AssetUniverse | None = None


def get_asset_universe(*, reload: bool = False) -> AssetUniverse:
    """Process-cached default universe. Pass ``reload=True`` to rebuild."""
    global _CACHED_UNIVERSE
    if _CACHED_UNIVERSE is None or reload:
        _CACHED_UNIVERSE = AssetUniverse.load()
    return _CACHED_UNIVERSE


__all__ = [
    "AssetMeta",
    "AssetUniverse",
    "UniverseLimits",
    "base_symbol",
    "get_asset_universe",
]
