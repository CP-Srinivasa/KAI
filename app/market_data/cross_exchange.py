"""Cross-exchange weighted-median price validation (market-data gate delta).

WHY (Satoshi / microstructure):
    A single exchange tick must never drive KAI into wrong stops, false signals
    or wrong risk assessments. The existing ``FallbackMarketDataAdapter``
    (``app/market_data/service.py``, DS-20260529-V1) already guards the live
    read-path with a *binary* two-provider disagreement check (flat 10% → tag
    stale). That check is preserved untouched.

    This module is the **delta**: a self-contained, execution-grade validation
    layer that takes a *rich* per-provider quote (price/bid/ask/volume/
    orderbook_depth/timestamp/trust/latency) for one asset across several
    venues and returns a robust, weight-aware ``validated_price`` plus a desync
    classification. A flash spike on one venue, a single stale feed, or a
    delisted-instrument phantom price gets down-weighted out of the median
    instead of poisoning it.

WHAT THIS IS NOT:
    It does not silently re-wire the live trading loop. The current
    ``MarketDataPoint`` carries none of the microstructure fields this layer
    needs (bid/ask/depth/trust/latency); plumbing those through every adapter
    is a separate sprint. Until then this is a pure, fully-tested function the
    bridge/loop can call once richer per-venue quotes are available. No
    execution behaviour changes by importing this module.

CONTRACT:
    weight_i = trust_i * freshness_i * liquidity_i * spread_i * (1 - anomaly_i)
    validated_price = weighted_median(price_i, weight_i)

    The desync threshold is dynamic: it widens with realized volatility and in
    genuinely high-dispersion regimes (PANIC / EUPHORIC_BLOWOFF / LOW_LIQUIDITY)
    so a *consistent* volatility jump is not falsely rejected, and it stays
    tight under HIGH_MANIPULATION so manipulation is caught, not excused.
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field

DATA_QUALITY_VERSION = "cross_exchange_median.v1"


# ─── Tunables ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CrossExchangeConfig:
    """Validation tunables. Defaults are conservative, execution-grade floors."""

    # Freshness — effective age = (now - quote_ts) + latency. Full credit below
    # ``freshness_full_ms``; linear ramp to zero at ``max_staleness_ms``; a quote
    # at/over ``max_staleness_ms`` is unusable (weight 0).
    freshness_full_ms: float = 2_000.0
    max_staleness_ms: float = 60_000.0

    # Spread — tight spread = trustworthy quote. Ramp from full credit at
    # ``spread_full_bps`` down to ``spread_score_floor`` at ``max_spread_bps``.
    # Floor stays > 0 so an illiquid wide-spread coin still contributes (it must
    # not be silently rejected — only down-weighted).
    spread_full_bps: float = 5.0
    max_spread_bps: float = 120.0
    spread_score_floor: float = 0.05

    # Liquidity — log-scaled volume × orderbook depth (quote currency).
    liquidity_ref_volume: float = 5_000_000.0
    liquidity_ref_depth: float = 250_000.0

    # Reliability (diagnostic only — NOT part of the weight formula by spec).
    max_latency_ms: float = 5_000.0

    # Desync / anomaly thresholds (fractions of price).
    base_desync_threshold_pct: float = 0.005  # 0.5% baseline agreement band
    volatility_coeff: float = 1.0  # threshold += coeff * volatility_fraction
    max_desync_threshold_pct: float = 0.08  # hard cap so manipulation can't widen forever
    anomaly_k: float = 3.0  # anomaly_score hits 1.0 at anomaly_k × dyn_threshold

    # Consensus.
    min_providers: int = 2  # usable, non-desynced providers required to validate
    target_providers_for_full_confidence: float = 3.0


# Regime → desync-threshold multiplier. Widen where wide dispersion is *real*;
# stay tight where wide dispersion is itself the red flag.
_REGIME_MULTIPLIER: dict[str, float] = {
    "panic": 3.0,
    "euphoric_blowoff": 2.5,
    "low_liquidity": 2.0,
    "bull": 1.0,
    "bear": 1.0,
    "accumulation": 0.8,
    "distribution": 0.8,
    "high_manipulation_probability": 0.6,
}
_DEFAULT_REGIME_MULTIPLIER = 1.0


# ─── Input / output models ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderQuote:
    """One venue's microstructure snapshot for a single asset."""

    provider_id: str
    price: float
    bid: float
    ask: float
    volume: float  # 24h traded volume, quote currency
    orderbook_depth: float  # aggregated near-touch depth, quote currency
    timestamp_ms: float  # epoch milliseconds of the quote
    exchange_trust_score: float  # venue reputation, clamped to [0, 1]
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ProviderAssessment:
    """Per-provider scored view after validation."""

    provider_id: str
    price: float
    age_ms: float
    spread_bps: float
    freshness_score: float
    spread_score: float
    liquidity_score: float
    reliability_score: float
    anomaly_score: float
    weight: float
    desynced: bool
    excluded_reason: str | None  # "stale" | "desynced" | "zero_weight" | None


@dataclass(frozen=True)
class CrossExchangeValidation:
    """Full result; ``to_output_dict`` emits the canonical gate schema."""

    asset_id: str
    validated_price: float | None
    raw_provider_prices: dict[str, float]
    weighted_median_confidence: float
    provider_desyncs: list[str]
    freshness_ms_max: float
    spread_bps_median: float
    liquidity_score: float
    reject_reason: str | None
    dynamic_desync_threshold_pct: float
    assessments: list[ProviderAssessment] = field(default_factory=list)
    data_quality_version: str = DATA_QUALITY_VERSION

    def to_output_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "validated_price": self.validated_price,
            "raw_provider_prices": dict(self.raw_provider_prices),
            "weighted_median_confidence": self.weighted_median_confidence,
            "provider_desyncs": list(self.provider_desyncs),
            "freshness_ms_max": self.freshness_ms_max,
            "spread_bps_median": self.spread_bps_median,
            "liquidity_score": self.liquidity_score,
            "reject_reason": self.reject_reason,
            "data_quality_version": self.data_quality_version,
        }

    @property
    def is_execution_safe(self) -> bool:
        """A price is execution-relevant only when nothing rejected it."""
        return self.reject_reason is None and self.validated_price is not None


@dataclass
class _Row:
    """Mutable per-provider scratchpad used during a single validation pass."""

    provider_id: str
    price: float
    age_ms: float
    spread_bps: float
    freshness: float
    spread: float
    liquidity: float
    reliability: float
    trust: float
    usable: bool
    anomaly: float = 1.0
    weight: float = 0.0
    desynced: bool = False


# ─── Scoring primitives ───────────────────────────────────────────────────────


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _log_ratio(value: float, reference: float) -> float:
    """log10(1+value)/log10(1+reference), clamped to [0, 1]."""
    if reference <= 0:
        return 0.0
    return _clamp(math.log10(1.0 + max(value, 0.0)) / math.log10(1.0 + reference))


def freshness_score(age_ms: float, latency_ms: float, cfg: CrossExchangeConfig) -> float:
    """1.0 fresh, linear ramp to 0.0 at ``max_staleness_ms``. Latency counts as
    staleness because the quote is that much older by the time it is usable."""
    eff = max(age_ms, 0.0) + max(latency_ms, 0.0)
    if eff >= cfg.max_staleness_ms:
        return 0.0
    if eff <= cfg.freshness_full_ms:
        return 1.0
    span = cfg.max_staleness_ms - cfg.freshness_full_ms
    if span <= 0:
        return 0.0
    return _clamp(1.0 - (eff - cfg.freshness_full_ms) / span)


def spread_bps(bid: float, ask: float) -> float:
    """Bid/ask spread in basis points; ``inf`` when the quote is malformed."""
    if bid <= 0 or ask <= 0 or ask < bid:
        return math.inf
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return math.inf
    return (ask - bid) / mid * 10_000.0


def spread_score(bps: float, cfg: CrossExchangeConfig) -> float:
    """Full credit at/under ``spread_full_bps``, floor at/over ``max_spread_bps``."""
    if not math.isfinite(bps):
        return cfg.spread_score_floor
    if bps <= cfg.spread_full_bps:
        return 1.0
    if bps >= cfg.max_spread_bps:
        return cfg.spread_score_floor
    span = cfg.max_spread_bps - cfg.spread_full_bps
    ramp = 1.0 - (bps - cfg.spread_full_bps) / span
    return _clamp(ramp, lo=cfg.spread_score_floor)


def liquidity_score(volume: float, depth: float, cfg: CrossExchangeConfig) -> float:
    """Geometric mean of log-scaled volume and orderbook depth, [0, 1]."""
    lv = _log_ratio(volume, cfg.liquidity_ref_volume)
    ld = _log_ratio(depth, cfg.liquidity_ref_depth)
    return math.sqrt(lv * ld)


def reliability_score(trust: float, latency_ms: float, cfg: CrossExchangeConfig) -> float:
    """Diagnostic blend of venue trust and transport latency. Not used in the
    weight formula (which the spec fixes exactly) — surfaced for observability."""
    lat = _clamp(1.0 - max(latency_ms, 0.0) / cfg.max_latency_ms)
    return _clamp(trust) * lat


def weighted_median(pairs: list[tuple[float, float]]) -> float:
    """Weighted median of (value, weight) pairs. Weights must be >= 0 with a
    positive total. Averages the straddling values on an exact half-weight
    boundary so two equal-weight quotes return their midpoint."""
    usable = [(v, w) for v, w in pairs if w > 0]
    if not usable:
        raise ValueError("weighted_median requires at least one positive weight")
    usable.sort(key=lambda p: p[0])
    total = math.fsum(w for _, w in usable)
    half = total / 2.0
    acc = 0.0
    for i, (value, weight) in enumerate(usable):
        acc += weight
        if acc > half:
            return value
        if acc == half:
            if i + 1 < len(usable):
                return (value + usable[i + 1][0]) / 2.0
            return value
    return usable[-1][0]


def dynamic_desync_threshold(volatility: float, regime: object, cfg: CrossExchangeConfig) -> float:
    """Agreement band as a price fraction, widened by volatility and regime,
    hard-capped at ``max_desync_threshold_pct``."""
    base = cfg.base_desync_threshold_pct + cfg.volatility_coeff * max(volatility, 0.0)
    mult = _DEFAULT_REGIME_MULTIPLIER
    if regime is not None:
        key = getattr(regime, "value", regime)
        mult = _REGIME_MULTIPLIER.get(str(key).lower(), _DEFAULT_REGIME_MULTIPLIER)
    return min(base * mult, cfg.max_desync_threshold_pct)


# ─── Validator ──────────────────────────────────────────────────────────────


def validate_cross_exchange(
    asset_id: str,
    quotes: list[ProviderQuote],
    *,
    volatility: float = 0.0,
    regime: object = None,
    now_ms: float | None = None,
    config: CrossExchangeConfig | None = None,
) -> CrossExchangeValidation:
    """Validate one asset's price across venues via weighted median.

    ``volatility`` is a realized-volatility *fraction* (e.g. 0.02 = 2%) used to
    widen the desync band. ``regime`` is any object whose ``.value`` (or string)
    matches a ``MarketRegime`` label; unknown/None → neutral multiplier.
    """
    cfg = config or CrossExchangeConfig()
    now = now_ms if now_ms is not None else time.time() * 1000.0
    raw_prices = {q.provider_id: q.price for q in quotes}
    dyn_threshold = dynamic_desync_threshold(volatility, regime, cfg)

    if not quotes:
        return _rejected(asset_id, raw_prices, "no_providers", dyn_threshold)

    # 1) per-provider primitive scores (anomaly filled in after we have a centre)
    rows: list[_Row] = []
    for q in quotes:
        age_ms = max(now - q.timestamp_ms, 0.0)
        bps = spread_bps(q.bid, q.ask)
        fresh = freshness_score(age_ms, q.latency_ms, cfg)
        rows.append(
            _Row(
                provider_id=q.provider_id,
                price=q.price,
                age_ms=age_ms,
                spread_bps=bps,
                freshness=fresh,
                spread=spread_score(bps, cfg),
                liquidity=liquidity_score(q.volume, q.orderbook_depth, cfg),
                reliability=reliability_score(q.exchange_trust_score, q.latency_ms, cfg),
                trust=_clamp(q.exchange_trust_score),
                usable=fresh > 0.0 and q.price > 0.0,
            )
        )

    freshness_ms_max = max(r.age_ms for r in rows)
    finite_bps = [r.spread_bps for r in rows if math.isfinite(r.spread_bps)]
    spread_bps_median = statistics.median(finite_bps) if finite_bps else math.inf

    usable_rows = [r for r in rows if r.usable]
    stale_count = sum(1 for r in rows if r.freshness <= 0.0)
    if not usable_rows:
        reason = "all_providers_stale" if stale_count == len(rows) else "no_usable_providers"
        return _rejected(
            asset_id,
            raw_prices,
            reason,
            dyn_threshold,
            freshness_ms_max=freshness_ms_max,
            spread_bps_median=spread_bps_median,
            assessments=_assess(rows, excluded_all="stale"),
        )

    # 2) preliminary robust centre (unweighted median of usable prices) → anomaly
    prelim_center = statistics.median(r.price for r in usable_rows)
    anomaly_full_dev = max(cfg.anomaly_k * dyn_threshold, 1e-9)
    for r in rows:
        if not r.usable or prelim_center <= 0:
            r.anomaly = 1.0
            continue
        dev = abs(r.price - prelim_center) / prelim_center
        r.anomaly = _clamp(dev / anomaly_full_dev)

    # 3) weights per the fixed contract
    for r in rows:
        r.weight = r.trust * r.freshness * r.liquidity * r.spread * (1.0 - r.anomaly)

    weighted = [(r.price, r.weight) for r in rows if r.weight > 0]
    if not weighted:
        return _rejected(
            asset_id,
            raw_prices,
            "no_weighted_consensus",
            dyn_threshold,
            freshness_ms_max=freshness_ms_max,
            spread_bps_median=spread_bps_median,
            assessments=_assess(rows),
        )

    validated_price = weighted_median(weighted)

    # 4) desync detection vs the validated price
    desynced: set[str] = set()
    for r in rows:
        if not r.usable:
            continue
        dev = abs(r.price - validated_price) / validated_price
        if dev > dyn_threshold:
            r.desynced = True
            desynced.add(r.provider_id)

    contributing = [r for r in rows if r.usable and not r.desynced and r.weight > 0]

    reject_reason: str | None = None
    if len(contributing) < cfg.min_providers:
        # Not enough independent venues agree → not execution-safe. Distinguish a
        # genuine standoff (≥2 usable but mutually contradictory) from a thin
        # single-source read.
        if len(usable_rows) >= cfg.min_providers:
            reject_reason = "providers_disagree_no_consensus"
        else:
            reject_reason = "insufficient_cross_validation"

    confidence = _confidence(contributing, validated_price, dyn_threshold, cfg)
    agg_liquidity = _aggregate_liquidity(contributing or usable_rows)

    return CrossExchangeValidation(
        asset_id=asset_id,
        validated_price=validated_price,
        raw_provider_prices=raw_prices,
        weighted_median_confidence=round(confidence, 4),
        provider_desyncs=sorted(desynced),
        freshness_ms_max=freshness_ms_max,
        spread_bps_median=spread_bps_median,
        liquidity_score=round(agg_liquidity, 4),
        reject_reason=reject_reason,
        dynamic_desync_threshold_pct=dyn_threshold,
        assessments=_assess(rows),
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _confidence(
    contributing: list[_Row],
    validated_price: float,
    dyn_threshold: float,
    cfg: CrossExchangeConfig,
) -> float:
    if not contributing or validated_price <= 0:
        return 0.0
    weights = [r.weight for r in contributing]
    total_w = math.fsum(weights)
    if total_w <= 0:
        return 0.0
    # Kish effective sample size — penalises one venue dominating the median.
    n_eff = total_w * total_w / math.fsum(w * w for w in weights)
    provider_factor = _clamp(n_eff / cfg.target_providers_for_full_confidence)
    # Weighted dispersion of contributors around the validated price.
    disp = (
        math.fsum(r.weight * abs(r.price - validated_price) / validated_price for r in contributing)
        / total_w
    )
    agreement = _clamp(1.0 - disp / dyn_threshold) if dyn_threshold > 0 else 0.0
    return _clamp(provider_factor * agreement)


def _aggregate_liquidity(rows: list[_Row]) -> float:
    vals = [r.liquidity for r in rows]
    return statistics.median(vals) if vals else 0.0


def _assess(rows: list[_Row], *, excluded_all: str | None = None) -> list[ProviderAssessment]:
    out: list[ProviderAssessment] = []
    for r in rows:
        if excluded_all is not None:
            excluded: str | None = excluded_all
        elif not r.usable:
            excluded = "stale"
        elif r.desynced:
            excluded = "desynced"
        elif r.weight <= 0:
            excluded = "zero_weight"
        else:
            excluded = None
        out.append(
            ProviderAssessment(
                provider_id=r.provider_id,
                price=r.price,
                age_ms=r.age_ms,
                spread_bps=r.spread_bps,
                freshness_score=r.freshness,
                spread_score=r.spread,
                liquidity_score=r.liquidity,
                reliability_score=r.reliability,
                anomaly_score=r.anomaly,
                weight=r.weight,
                desynced=r.desynced,
                excluded_reason=excluded,
            )
        )
    return out


def _rejected(
    asset_id: str,
    raw_prices: dict[str, float],
    reason: str,
    dyn_threshold: float,
    *,
    freshness_ms_max: float = math.inf,
    spread_bps_median: float = math.inf,
    assessments: list[ProviderAssessment] | None = None,
) -> CrossExchangeValidation:
    return CrossExchangeValidation(
        asset_id=asset_id,
        validated_price=None,
        raw_provider_prices=raw_prices,
        weighted_median_confidence=0.0,
        provider_desyncs=[],
        freshness_ms_max=freshness_ms_max,
        spread_bps_median=spread_bps_median,
        liquidity_score=0.0,
        reject_reason=reason,
        dynamic_desync_threshold_pct=dyn_threshold,
        assessments=assessments or [],
    )
