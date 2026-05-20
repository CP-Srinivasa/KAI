"""Channel-scale-factor resolution (P1 #8 — 2026-05-14).

The premium Telegram channel posts Bybit-Futures pairs in two scales:
- Direct USD (BTC 60000, ETH 3500) → factor 1.0
- Integer-tick (SWARMS 32450 → $0.0003245, 1000LUNC 10310 → $0.0001031)
  → factor 1e3 .. 1e8

Pre-2026-05-14 the bridge resolved the factor on every tick. That meant:
- Every bridge tick incurred a market_data round-trip per pending signal
- If a tick crashed between ``apply_scale`` (in-memory mutation) and the
  audit write, the next tick re-detected from the raw payload — slow but
  not broken.

2026-05-14 fix: the worker resolves the factor exactly once at receive
time, persists the resolved values into the envelope, and marks the
record. The bridge then trusts the persisted scale and skips re-detection.
When market_data is unreachable at receive time, the envelope is marked
``scale_unknown=True`` — the bridge falls back to re-detection on each
tick until market_data answers, which is the pre-2026-05-14 behaviour.

Pure helpers (`detect_scale_factor`, `apply_scale_to_payload`) are
test-friendly. The async path (`fetch_price`, `resolve_scale_for_symbol`)
wraps the market_data service; callers inject a fake fetcher for tests.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.core.settings import get_settings
from app.market_data.service import get_market_data_snapshot

logger = logging.getLogger(__name__)

# Power-of-ten bands we recognise. 1e2 is intentionally NOT included —
# a 100× drift would more likely be a parsing error than a scale ladder.
_RECOGNISED_SCALES = (1.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8)

# Strict guardrail: a candidate factor is accepted only when the ratio sits
# within ±50% of the factor. Anything looser would catch real 2× drifts.
_SCALE_TOLERANCE = 0.5


PriceFetcher = Callable[[str], Awaitable[float | None]]


def detect_scale_factor(channel_value: float, provider_price: float) -> float:
    """Return the power-of-ten factor that brings channel_value to USD scale.

    Returns 1.0 when:
    - channel_value or provider_price is non-positive (defensive)
    - no recognised scale matches within tolerance (fail-soft pass-through)

    The caller decides what 1.0 means in context: at receive-time a 1.0 from
    a healthy price-fetch is "values are already USD". A 1.0 from no fetch
    at all would mean "we have no idea" — but the caller routes that case
    through the None return of ``resolve_scale_for_symbol`` instead, so
    callers never see ambiguous 1.0s.
    """
    if channel_value <= 0 or provider_price <= 0:
        return 1.0
    ratio = channel_value / provider_price
    best_factor = 1.0
    best_dist = abs(ratio - 1.0)
    for factor in _RECOGNISED_SCALES:
        dist = abs(ratio - factor) / factor
        if dist <= _SCALE_TOLERANCE and dist < best_dist:
            best_factor = factor
            best_dist = dist
    return best_factor


def apply_scale_to_payload(payload: dict[str, object], factor: float) -> None:
    """In-place rescale of entry / SL / targets by 1 / factor.

    No-op when factor is non-positive or 1.0 — that lets callers route both
    "already-USD signals" and "couldn't resolve" through the same path
    without writing the values twice.
    """
    if factor <= 0 or factor == 1.0:
        return
    for key in ("entry_value", "entry_min", "entry_max", "stop_loss"):
        v = payload.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            payload[key] = float(v) / factor
    raw_targets = payload.get("targets")
    if isinstance(raw_targets, list):
        scaled: list[float] = []
        for t in raw_targets:
            if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
                scaled.append(float(t) / factor)
        payload["targets"] = scaled


async def fetch_price(symbol: str) -> float | None:
    """Spot price via configured fallback provider (Bybit → CoinGecko → Mock).

    Returns None when market_data is unreachable or stale. Bridge + Worker
    both go through this single seam so future provider changes (e.g.
    OKX-primary) need exactly one edit.
    """
    settings = get_settings()
    provider = getattr(settings.operator, "signal_auto_run_provider", "fallback")
    if not provider or provider == "coingecko":
        provider = "fallback"
    snap = await get_market_data_snapshot(symbol=symbol, provider=provider)
    if not snap.available or snap.is_stale:
        return None
    return snap.price


def validate_scaled_signal(
    *,
    direction: str,
    entry: float | None,
    stop_loss: float | None,
    targets: list[float] | None,
    spot: float | None = None,
    entry_spot_ratio_tolerance: float = 0.5,
) -> str | None:
    """Sanity-Check der scaled Signal-Werte. Returns reason-code oder None.

    Strukturelle Checks (immer):
    - ``entry`` muss > 0 sein, sonst "scale_collapses_to_zero"
    - long: ``stop_loss`` muss < ``entry`` (sonst "long_sl_at_or_above_entry")
    - short: ``stop_loss`` muss > ``entry`` (sonst "short_sl_at_or_below_entry")
    - long: mindestens 1 target > ``entry`` (sonst "long_targets_at_or_below_entry")
    - short: mindestens 1 target < ``entry`` (sonst "short_targets_at_or_above_entry")

    Markt-Plausibilität (nur wenn ``spot`` gesetzt):
    - long: ``stop_loss`` muss < ``spot`` (sonst "long_sl_at_or_above_spot"
            — Markt ist bereits unter dem SL → Fill würde sofort gestoppt)
    - short: ``stop_loss`` muss > ``spot`` (sonst "short_sl_at_or_below_spot")
    - ``entry/spot`` ratio innerhalb ``[1-tol, 1+tol]`` (default 0.5 →
            entry darf nicht > 50% vom spot abweichen, sonst
            "entry_far_from_spot" — fast immer ein Scale-Detection-Bug)

    Die Reason-Codes sind stabile API für Bridge-Audit + Trail-UI. Wenn
    None zurückgegeben wird, ist das Signal nach Skalierung plausibel.

    Wurzel 2026-05-12: IRYS/USDT (entry 5455 nach scale ×1e5 = 0.05455,
    spot war 0.05153, SL 0.0523). Strukturell korrekt (SL < entry), aber
    Markt war bereits durch SL gelaufen → paper_engine reject mit
    ``long_sl_at_or_above_price`` und der bridge_pending_orders.jsonl-Audit
    bekam nur den opaken Reason ``paper_engine_returned_none``. Mit dieser
    Validation greift Bridge VOR dem paper-engine-call und schreibt einen
    aussagekräftigen Reason im Trail.
    """
    norm = (direction or "").strip().lower()
    is_long = norm in {"long", "buy"}
    is_short = norm in {"short", "sell"}
    if not is_long and not is_short:
        return None  # unbekannte direction — kein Block hier, Bridge-Gate-3 catched das
    if entry is None or entry <= 0:
        return "scale_collapses_to_zero"
    if stop_loss is not None and stop_loss > 0:
        if is_long and stop_loss >= entry:
            return "long_sl_at_or_above_entry"
        if is_short and stop_loss <= entry:
            return "short_sl_at_or_below_entry"
    valid_targets = [
        float(t)
        for t in (targets or [])
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0
    ]
    if valid_targets:
        if is_long and all(t <= entry for t in valid_targets):
            return "long_targets_at_or_below_entry"
        if is_short and all(t >= entry for t in valid_targets):
            return "short_targets_at_or_above_entry"
    if spot is not None and spot > 0:
        if is_long and stop_loss is not None and stop_loss > 0 and stop_loss >= spot:
            return "long_sl_at_or_above_spot"
        if is_short and stop_loss is not None and stop_loss > 0 and stop_loss <= spot:
            return "short_sl_at_or_below_spot"
        ratio = entry / spot
        lo = 1.0 - entry_spot_ratio_tolerance
        hi = 1.0 + entry_spot_ratio_tolerance
        if ratio < lo or ratio > hi:
            return "entry_far_from_spot"
    return None


async def resolve_scale_for_symbol(
    symbol: str,
    reference_value: float,
    *,
    price_fetcher: PriceFetcher | None = None,
) -> float | None:
    """Resolve the scale factor for ``symbol`` against ``reference_value``.

    Returns:
    - float (1.0 .. 1e8): factor was resolved against a live provider price
    - None: provider price unavailable; caller marks envelope as scale_unknown

    ``price_fetcher`` is injectable for tests so no real market_data call
    has to fire.
    """
    fetcher = price_fetcher or fetch_price
    price = await fetcher(symbol)
    if price is None:
        return None
    return detect_scale_factor(reference_value, price)


__all__ = [
    "PriceFetcher",
    "apply_scale_to_payload",
    "detect_scale_factor",
    "fetch_price",
    "resolve_scale_for_symbol",
    "validate_scaled_signal",
]
