"""Live-Beschaffung fuer den Universe Integrity Preflight.

Getrennt von ``universe_integrity`` gehalten: dort liegt die reine, in CI
pruefbare Bewertung, hier die Netzzugriffe. Damit bleibt die Entscheidungslogik
testbar, ohne dass ein Test je das Netz braucht.

Aufruf::

    python -m app.research.universe_preflight
    python -m app.research.universe_preflight --out artifacts/research/universe.json

Was hier NICHT passiert: keine Forward-Returns, keine Hypothesen-Auswertung,
keine p-Werte. Der Preflight darf vor T0 laufen, und das ist nur wahr, solange er
ausschliesslich Verfuegbarkeit misst.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.market_data.history_loader import load_ohlcv_history
from app.research.universe_integrity import (
    DataFacts,
    ProviderSymbol,
    UniverseIntegrityReport,
    evaluate_universe,
    report_to_json,
    to_provider_pair,
)

_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
_MS_PER_DAY = 86_400_000


async def fetch_provider_symbols(
    url: str = _EXCHANGE_INFO_URL,
    *,
    timeout: float = 30.0,
) -> dict[str, ProviderSymbol]:
    """Handelsstatus aller Paare, live aus exchangeInfo.

    Bewusst live und nicht aus einer gepflegten Liste: welcher Pair-Name heute
    handelbar ist, ist eine Tatsache des Providers, keine Erinnerung.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()

    out: dict[str, ProviderSymbol] = {}
    for raw in payload.get("symbols", []):
        pair = str(raw.get("symbol", "")).upper()
        if not pair:
            continue
        out[pair] = ProviderSymbol(
            pair=pair,
            status=str(raw.get("status", "")),
            base_asset=str(raw.get("baseAsset", "")),
            quote_asset=str(raw.get("quoteAsset", "")),
        )
    return out


async def collect_data_facts(
    symbols: list[str],
    *,
    timeframe: str,
    lookback_days: int,
    now_ms: int | None = None,
) -> dict[str, DataFacts]:
    """Backfill je Symbol, aber NUR Zaehlungen behalten.

    Die Kerzen werden geladen und sofort auf drei Zahlen reduziert. Preise
    verlassen diese Funktion nicht — das ist die Grenze, die den Preflight vor
    T0 zulaessig macht.
    """
    from app.market_data.binance_adapter import BinanceAdapter
    from app.research.runner import build_fetch

    fetch = build_fetch(BinanceAdapter().get_ohlcv)
    end_ms = now_ms if now_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - lookback_days * _MS_PER_DAY

    facts: dict[str, DataFacts] = {}
    for symbol in symbols:
        try:
            history = await load_ohlcv_history(symbol, timeframe, start_ms, end_ms, fetch)
        except Exception as exc:  # noqa: BLE001 — ein toter Ticker ist ein Befund
            print(f"  {symbol:12s} Backfill fehlgeschlagen: {exc}", file=sys.stderr)
            facts[symbol] = DataFacts()
            continue
        facts[symbol] = DataFacts(
            bars=len(history.candles),
            gap_bars=history.gap_bars,
            positive_volume_bars=sum(1 for c in history.candles if c.volume > 0.0),
        )
    return facts


async def run_preflight(
    research_symbols: list[str],
    *,
    timeframe: str,
    lookback_days: int,
    min_bars: int,
) -> UniverseIntegrityReport:
    """Zweistufig: erst kanonisieren, dann NUR die kanonischen Namen backfillen.

    Andersherum wuerde man einen umbenannten Ticker (MATIC) backfillen und seine
    Legacy-Daten fuer bare Muenze nehmen — genau der Fehler, den dieser Preflight
    verhindern soll.
    """
    provider = await fetch_provider_symbols()
    print(f"exchangeInfo: {len(provider)} Paare")

    staged = evaluate_universe(research_symbols, provider)
    facts = await collect_data_facts(
        list(staged.canonical_universe),
        timeframe=timeframe,
        lookback_days=lookback_days,
    )
    return evaluate_universe(research_symbols, provider, facts, min_bars=min_bars)


async def main() -> int:
    from app.observability.technical_screener_feed import DEFAULT_UNIVERSE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--min-bars", type=int, default=4000)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    report = await run_preflight(
        list(DEFAULT_UNIVERSE),
        timeframe=args.timeframe,
        lookback_days=args.lookback_days,
        min_bars=args.min_bars,
    )

    for symbol in report.symbols:
        flag = "ok  " if symbol.ok else "FAIL"
        note = ",".join(symbol.issues) if symbol.issues else "-"
        print(
            f"  {flag} {symbol.research_symbol:12s} -> {symbol.canonical_symbol:12s} "
            f"{str(symbol.status):8s} bars={symbol.facts.bars:5d} "
            f"vol>0={symbol.facts.positive_volume_bars:5d} {note}"
        )

    print()
    print(f"kanonisches Universum : {len(report.canonical_universe)} Symbole")
    print(f"UNIVERSE_SHA256       : {report.universe_sha256}")
    print(f"versiegelbar          : {report.ok}")
    for line in report.blocking:
        print(f"  BLOCKIEREND: {line}")

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report_to_json(report), encoding="utf-8")
        print(f"geschrieben: {path}")

    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - Einstiegspunkt
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "collect_data_facts",
    "fetch_provider_symbols",
    "run_preflight",
    "to_provider_pair",
]
