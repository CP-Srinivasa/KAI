"""Regime service — orchestrates OHLCV fetch → indicators → classify → persist.

Single-asset run path:
    1. Pull recent 1h OHLCV from market_data.
    2. Derive ATR / ADX (+DI / -DI) / RV / ATR z-score / vol class.
    3. Classify the latest bar with hysteresis vs the previously persisted
       snapshot.
    4. Append the new snapshot to the asset's JSONL.

Failures (no data, indicator NaN, persistence error) yield an ``unknown``
snapshot rather than skipping a tick — operator sees an explicit gap-marker
in the JSONL instead of a silent missing-hour mystery.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.analysis.indicators.adx import ADX_DEFAULT_PERIOD, compute_adx_di
from app.analysis.indicators.atr import ATR_DEFAULT_PERIOD, compute_atr
from app.analysis.indicators.realized_volatility import (
    RV_DEFAULT_WINDOW,
    VolClass,
    classify_vol_quantile,
    compute_atr_zscore,
    compute_realized_volatility,
)
from app.market_data.models import OHLCV
from app.regime.classifier import ClassifierInputs, classify_with_hysteresis
from app.regime.models import RegimeClass, RegimeSnapshot
from app.regime.storage import (
    DEFAULT_REGIME_DIR,
    append_regime_snapshot,
    latest_regime_snapshot,
)

logger = logging.getLogger(__name__)

DEFAULT_OHLCV_LIMIT = 200  # ~8 days @ 1h — covers ADX 2x-warmup + RV reference
DEFAULT_TIMEFRAME = "1h"
ATR_Z_WINDOW = 30


class MarketDataProvider(Protocol):
    """Structural type for the market_data dependency.

    Only ``get_ohlcv`` is needed. Real callers pass
    ``app.market_data.service.MarketDataService``; tests pass mocks.
    """

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]: ...


def _truncate_to_hour(iso_ts: str) -> str:
    """Best-effort hour truncation of an ISO-8601 timestamp.

    Falls back to current UTC hour on parse failure (shouldn't happen for
    market_data outputs, but keeps the service safe under unexpected input).
    """
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    truncated = dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return truncated.strftime("%Y-%m-%dT%H:%M:%SZ")


def _unknown_snapshot(asset: str, reason: str) -> RegimeSnapshot:
    """Produce an unknown-regime snapshot for failure modes.

    Reason is logged for forensic context but not persisted (keeps the JSONL
    schema lean). Callers can grep journalctl for "[regime] unknown".
    """
    logger.warning("[regime] unknown snapshot for %s: %s", asset, reason)
    return RegimeSnapshot(
        asset=asset,
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        regime=RegimeClass.UNKNOWN,
        vol_class="vol_normal",
        confidence=0.0,
    )


class RegimeService:
    """Coordinates one regime classification cycle per asset."""

    def __init__(
        self,
        market_data: MarketDataProvider,
        storage_dir: str | Path = DEFAULT_REGIME_DIR,
        ohlcv_limit: int = DEFAULT_OHLCV_LIMIT,
        timeframe: str = DEFAULT_TIMEFRAME,
    ) -> None:
        self._market_data = market_data
        self._storage_dir = Path(storage_dir)
        self._ohlcv_limit = ohlcv_limit
        self._timeframe = timeframe

    async def classify_once(
        self,
        asset: str,
        *,
        market_data_symbol: str | None = None,
    ) -> RegimeSnapshot:
        """Run a single classification cycle for ``asset`` and persist.

        ``asset`` is the storage label (e.g. ``"BTC"``). ``market_data_symbol``
        is the symbol passed to the provider (e.g. ``"BTCUSDT"`` for Bybit-V5);
        defaults to ``asset`` if not provided. Decoupling the two lets us
        keep clean asset names in JSONL while still feeding venue-specific
        symbols to adapters.

        Always persists exactly one snapshot per call. On any failure path
        the snapshot is ``unknown`` so the JSONL has no missing rows and the
        next hysteresis evaluation has a previous to chain from.
        """
        fetch_symbol = market_data_symbol or asset
        try:
            candles = await self._market_data.get_ohlcv(
                fetch_symbol, timeframe=self._timeframe, limit=self._ohlcv_limit
            )
        except Exception as exc:  # noqa: BLE001 — wrapped to keep cron alive
            snap = _unknown_snapshot(asset, f"get_ohlcv error: {exc}")
            append_regime_snapshot(snap, self._storage_dir)
            return snap

        min_bars = max(2 * ADX_DEFAULT_PERIOD, RV_DEFAULT_WINDOW + 1)
        if len(candles) < min_bars:
            snap = _unknown_snapshot(asset, f"insufficient OHLCV: {len(candles)} < {min_bars}")
            append_regime_snapshot(snap, self._storage_dir)
            return snap

        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        closes = [c.close for c in candles]
        latest_ts = _truncate_to_hour(candles[-1].timestamp_utc)

        atr_series = compute_atr(highs, lows, closes, period=ATR_DEFAULT_PERIOD)
        adx_result = compute_adx_di(highs, lows, closes, period=ADX_DEFAULT_PERIOD)
        rv_series = compute_realized_volatility(closes, window=RV_DEFAULT_WINDOW)
        atr_z_series = compute_atr_zscore(atr_series, window=ATR_Z_WINDOW)

        latest_adx = adx_result.adx[-1]
        latest_plus_di = adx_result.plus_di[-1]
        latest_minus_di = adx_result.minus_di[-1]
        latest_rv = rv_series[-1]
        latest_atr_z = atr_z_series[-1]

        # Vol-class against trailing RV reference (excluding the bar we're
        # classifying, so percentile is "what's normal up to now").
        rv_reference = [v for v in rv_series[:-1] if v is not None]
        vol_class: VolClass = (
            classify_vol_quantile(latest_rv, rv_reference)
            if latest_rv is not None
            else "vol_normal"
        )

        inputs = ClassifierInputs(
            adx=latest_adx,
            plus_di=latest_plus_di,
            minus_di=latest_minus_di,
            rv_24h=latest_rv,
            atr_zscore=latest_atr_z,
            vol_class=vol_class,
        )

        previous = latest_regime_snapshot(asset, self._storage_dir)
        snap = classify_with_hysteresis(asset, latest_ts, inputs, previous)
        append_regime_snapshot(snap, self._storage_dir)
        logger.info(
            "[regime] %s @ %s → %s (vol=%s, adx=%s, +DI=%s, -DI=%s, atr_z=%s)",
            asset,
            latest_ts,
            snap.regime,
            snap.vol_class,
            _fmt(latest_adx),
            _fmt(latest_plus_di),
            _fmt(latest_minus_di),
            _fmt(latest_atr_z),
        )
        return snap


def _fmt(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "-"
