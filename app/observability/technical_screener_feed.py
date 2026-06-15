"""WP-D part 2 (2026-06-15): live technical-screener feed — SHADOW-ONLY, gated.

Wires the pure screener core (``app/signals/technical_screener.py``) to the
provider-open market-data service and the shadow-candidate ledger. It fetches
OHLCV for an operator-configurable liquid universe, ranks by asset-agnostic
strength (relative-strength vs BTC), evaluates each candidate on the WP-B
``signal_path="technical"`` eligibility path, and records the result as a SHADOW
candidate (no execution, no order, no position). Default OFF
(``ALERT_TECHNICAL_SCREENER_ENABLED``) — until calibrated, it only measures.

Universe sourcing is an operator-tunable static list by default; with
``ALERT_TECHNICAL_SCREENER_DYNAMIC_UNIVERSE`` (WP-F) it pulls the most-liquid
pairs by 24h volume from the sanctioned exchange adapter (``top_symbols_by_volume``)
instead — broad coverage without third-party scraping. An explicit static list
always wins; the dynamic fetch is fail-soft (falls back to static on any error).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.alerts.eligibility import (
    SIGNAL_PATH_TECHNICAL,
    evaluate_directional_eligibility,
)
from app.core.logging import get_logger
from app.market_data.models import OHLCV
from app.observability.shadow_candidate_ledger import (
    LEDGER_PATH,
    ShadowCandidate,
    record_candidate,
)
from app.signals.technical_screener import DEFAULT_LOOKBACK, screen_universe

logger = get_logger(__name__)

_BTC = "BTC/USDT"
DEFAULT_TIMEFRAME = "1h"

# Broad liquid universe — the anti-monoculture lever. Deliberately wider than the
# ~7 majors the narrative engine touches; the operator widens it via
# ``ALERT_TECHNICAL_SCREENER_SYMBOLS``. BTC is included so it competes on
# relative strength (and, having zero self-relative-strength, ranks below any
# outperforming alt).
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "MATIC/USDT",
    "DOT/USDT",
    "ATOM/USDT",
    "UNI/USDT",
    "LTC/USDT",
    "ETC/USDT",
    "XLM/USDT",
    "NEAR/USDT",
    "APT/USDT",
    "ARB/USDT",
    "OP/USDT",
    "INJ/USDT",
    "SUI/USDT",
    "SEI/USDT",
    "TIA/USDT",
    "RNDR/USDT",
    "FIL/USDT",
    "AAVE/USDT",
    "MKR/USDT",
    "LDO/USDT",
    "ALGO/USDT",
    "HBAR/USDT",
    "IMX/USDT",
    "GRT/USDT",
    "FET/USDT",
    "DOGE/USDT",
)


class _OhlcvSource(Protocol):
    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]: ...


def _last_close(candles: list[OHLCV]) -> float | None:
    if not candles:
        return None
    return sorted(candles, key=lambda c: c.timestamp_utc)[-1].close


async def run_technical_screen(
    adapter: _OhlcvSource,
    *,
    symbols: list[str],
    timeframe: str = DEFAULT_TIMEFRAME,
    lookback: int = DEFAULT_LOOKBACK,
    top_n: int = 20,
    min_strength: float = 0.0,
    allow_short: bool = False,
    write: bool = True,
    ledger_path: Path = LEDGER_PATH,
    now_utc: str | None = None,
) -> dict[str, object]:
    """Fetch → screen → eligibility → SHADOW-record. Never executes anything.

    Returns a summary dict. Fail-soft per symbol: a fetch error drops that symbol
    rather than aborting the run. ``allow_short`` (WP-E) opens bearish on the
    technical path for shadow measurement only — every admitted short is audit-
    logged; the execution path stays gated by entry_mode.
    """
    ts = now_utc or datetime.now(UTC).isoformat()
    limit = lookback + 5

    btc_candles = await _safe_ohlcv(adapter, _BTC, timeframe, limit)
    candles_by_symbol: dict[str, list[OHLCV]] = {}
    for symbol in symbols:
        candles = await _safe_ohlcv(adapter, symbol, timeframe, limit)
        if candles:
            candles_by_symbol[symbol] = candles

    signals = screen_universe(
        candles_by_symbol,
        btc_candles,
        lookback=lookback,
        min_strength=min_strength,
        top_n=top_n,
    )

    written = 0
    non_btc = 0
    eligible = 0
    shorts_admitted = 0
    for sig in signals:
        entry = _last_close(candles_by_symbol.get(sig.symbol, []))
        if entry is None or entry <= 0:
            continue
        decision = evaluate_directional_eligibility(
            sentiment_label=sig.direction,
            affected_assets=[sig.symbol],
            signal_path=SIGNAL_PATH_TECHNICAL,
            technical_strength=sig.strength,
            allow_short=allow_short,
        )
        rejected = decision.directional_eligible is False
        side = "long" if sig.direction == "bullish" else "short"
        if not rejected:
            eligible += 1
            # WP-E risk-review anchor: every short admitted via the flag is
            # audit-logged so the operator can review the new loss-direction.
            # Shadow-only — no execution results from this.
            if side == "short":
                shorts_admitted += 1
                logger.warning(
                    "technical_screener.short_admitted_shadow",
                    symbol=sig.symbol,
                    strength=sig.strength,
                    relative_strength=sig.relative_strength,
                    note="bearish technical eligible via ALERT_ALLOW_SHORT_TECHNICAL "
                    "— SHADOW only, execution still gated by entry_mode",
                )
        if not sig.symbol.upper().startswith("BTC/"):
            non_btc += 1
        candidate = ShadowCandidate.from_geometry(
            candidate_id=f"tech-{sig.symbol.replace('/', '')}-{ts}",
            ts_utc=ts,
            symbol=sig.symbol,
            side=side,
            entry_price=entry,
            stop_price=None,
            take_price=None,
            source="technical_screener",
            candidate_kind="technical",
            signal_confidence=sig.strength,
            directional_state=sig.direction,
            sentiment=sig.direction,
            gate_would_reject=rejected,
            gate_reason_codes=(
                [decision.directional_block_reason] if decision.directional_block_reason else []
            ),
        )
        if write and record_candidate(candidate, path=ledger_path):
            written += 1

    summary: dict[str, object] = {
        "enabled": True,
        "scanned": len(candles_by_symbol),
        "signals": len(signals),
        "written": written,
        "non_btc_signals": non_btc,
        "eligible_on_technical_path": eligible,
        "shorts_admitted_shadow": shorts_admitted,
        "btc_candles": len(btc_candles),
    }
    logger.info("technical_screener.run", **summary)
    return summary


async def _safe_ohlcv(
    adapter: _OhlcvSource, symbol: str, timeframe: str, limit: int
) -> list[OHLCV]:
    try:
        return await adapter.get_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception as exc:  # noqa: BLE001 — one bad symbol must not abort the run
        logger.warning("technical_screener.fetch_failed", symbol=symbol, error=str(exc)[:200])
        return []


def _configured_symbols(raw: str) -> list[str]:
    parsed = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return parsed or list(DEFAULT_UNIVERSE)


async def run_from_settings(adapter: _OhlcvSource | None = None) -> dict[str, object]:
    """Gated entrypoint for CLI / timer. No-op summary when the flag is OFF."""
    from app.core.settings import get_settings

    settings = get_settings()
    alerts = settings.alerts
    if not alerts.technical_screener_enabled:
        return {"enabled": False, "reason": "ALERT_TECHNICAL_SCREENER_ENABLED is false"}

    if adapter is None:
        from app.market_data.service import create_market_data_adapter

        adapter = create_market_data_adapter(provider=settings.market_data_provider)

    # WP-F: dynamic universe. An explicitly-set static list always wins; otherwise
    # (and only with the flag on) pull the most-liquid pairs by 24h volume from the
    # sanctioned exchange adapter. Fail-soft → static fallback on any error.
    symbols = _configured_symbols(alerts.technical_screener_symbols)
    if alerts.technical_screener_dynamic_universe and not alerts.technical_screener_symbols.strip():
        fetch = getattr(adapter, "top_symbols_by_volume", None)
        if fetch is not None:
            try:
                dynamic = await fetch(min(alerts.technical_screener_top_n * 5, 200))
                if dynamic:
                    symbols = dynamic
            except Exception as exc:  # noqa: BLE001 — never abort on a universe fetch
                logger.warning("technical_screener.dynamic_universe_failed", error=str(exc)[:200])

    return await run_technical_screen(
        adapter,
        symbols=symbols,
        top_n=alerts.technical_screener_top_n,
        min_strength=alerts.min_technical_strength,
        allow_short=alerts.allow_short_technical,
    )
