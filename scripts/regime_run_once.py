#!/usr/bin/env python3
"""Run one regime classification cycle per asset and persist.

Designed for cron (kai-regime-classify.timer) and manual operator triggers.
Single-process, single-pass — no in-process loop. Exit codes are cron-safe:

    0   one or more assets classified successfully
    1   all assets failed (provider/persistence error)
    2   bad CLI arguments

Usage:
    python -m scripts.regime_run_once --assets BTC ETH
    python -m scripts.regime_run_once --assets BTC --dry-run

Loaded as module (``python -m``) so the regular package imports resolve;
also runnable as a script via ``python scripts/regime_run_once.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure repo root is on sys.path when invoked as ``python scripts/...``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.market_data.service import create_market_data_adapter  # noqa: E402
from app.regime.service import RegimeService  # noqa: E402
from app.regime.storage import DEFAULT_REGIME_DIR  # noqa: E402

logger = logging.getLogger("regime_run_once")

# Asset-display-name → provider-symbol mapping. Bybit-V5-Linear convention
# (the default fallback chain probes Bybit first). Override via --symbol-map.
DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
}


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--assets",
        nargs="+",
        default=["BTC", "ETH"],
        help="Asset symbols to classify (default: BTC ETH).",
    )
    p.add_argument(
        "--storage-dir",
        default=str(DEFAULT_REGIME_DIR),
        help=f"Output JSONL directory (default: {DEFAULT_REGIME_DIR}).",
    )
    p.add_argument(
        "--ohlcv-limit",
        type=int,
        default=200,
        help="Number of recent 1h candles to fetch per asset (default: 200).",
    )
    p.add_argument(
        "--provider",
        default="fallback",
        help="market_data provider name (default: fallback — Bybit→Binance→OKX→…).",
    )
    p.add_argument(
        "--symbol-map",
        default=None,
        help="Override asset→provider-symbol mapping, comma-separated 'BTC=BTCUSDT,ETH=ETHUSDT'.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and log result, but skip JSONL persistence.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity (default: INFO).",
    )
    return p


def _parse_symbol_map(raw: str | None) -> dict[str, str]:
    if not raw:
        return dict(DEFAULT_SYMBOL_MAP)
    out: dict[str, str] = {}
    for entry in raw.split(","):
        if "=" not in entry:
            raise ValueError(f"bad --symbol-map entry: {entry!r} (expected 'ASSET=SYMBOL')")
        asset, sym = entry.split("=", 1)
        out[asset.strip().upper()] = sym.strip()
    return out


async def _run(args: argparse.Namespace) -> int:
    market_data = create_market_data_adapter(provider=args.provider)
    symbol_map = _parse_symbol_map(args.symbol_map)
    storage_dir = (
        Path(args.storage_dir) if not args.dry_run else Path("/tmp/regime_dry_run")  # noqa: S108 — dry-run only
    )
    storage_dir.mkdir(parents=True, exist_ok=True)

    svc = RegimeService(
        market_data=market_data,
        storage_dir=storage_dir,
        ohlcv_limit=args.ohlcv_limit,
    )

    successes = 0
    for asset in args.assets:
        market_symbol = symbol_map.get(asset.upper(), asset)
        try:
            snap = await svc.classify_once(asset.upper(), market_data_symbol=market_symbol)
            adx_s = f"{snap.adx:.1f}" if snap.adx is not None else "-"
            plus_s = f"{snap.plus_di:.1f}" if snap.plus_di is not None else "-"
            minus_s = f"{snap.minus_di:.1f}" if snap.minus_di is not None else "-"
            atr_z_s = f"{snap.atr_zscore:.2f}" if snap.atr_zscore is not None else "-"
            print(
                f"[regime_run_once] {asset} @ {snap.timestamp} → {snap.regime} "
                f"(vol={snap.vol_class}, adx={adx_s}, +DI={plus_s}, "
                f"-DI={minus_s}, atr_z={atr_z_s})"
            )
            successes += 1
        except Exception as exc:  # noqa: BLE001 — keep cron alive
            logger.exception("[regime_run_once] %s failed: %s", asset, exc)
            print(f"[regime_run_once] {asset} → FAILED: {exc}", file=sys.stderr)

    return 0 if successes > 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
