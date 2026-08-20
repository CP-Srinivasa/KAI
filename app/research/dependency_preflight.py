"""Frequenz- und Abhaengigkeitsanalyse vor T0 — ohne eine einzige Rendite.

Beantwortet die Frage, die ueber ``n_min`` und ``cluster_min`` entscheidet:
**wie viel unabhaengige Information steckt in n Feuerungen?**

Das Skript sieht ausschliesslich ``symbol``, ``timestamp`` und das Haltefenster.
Es importiert die Label-Funktionen nicht einmal — deshalb darf es vor T0 laufen,
ohne die konfirmatorische Frage zu beruehren.

Aufruf::

    python -m app.research.dependency_preflight docs/research/universe_rsi_reentry_v1.json

Warum das ein Modul und kein Wegwerf-Skript ist: die daraus abgeleiteten
Schranken wandern in eine versiegelte Praeregistrierung. Eine Zahl, deren
Herkunft niemand nachrechnen kann, hat dort nichts verloren.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.analysis.features.feature_matrix import build_feature_matrix
from app.market_data.history_loader import load_ohlcv_history
from app.research.signal_clusters import ClusterStats, Signal, summarize_clusters

_MS_PER_DAY = 86_400_000
_HOUR_MS = 3_600_000


@dataclass(frozen=True)
class FrequencyReport:
    """Was gemessen wurde — bewusst ohne jede Renditegroesse."""

    lookback_days: int
    horizon: int
    raw_fires: int
    label_capable_fires: int
    per_symbol_fires: dict[str, int]
    clusters: ClusterStats

    @property
    def fires_per_day(self) -> float:
        return self.label_capable_fires / self.lookback_days if self.lookback_days else 0.0

    @property
    def clusters_per_day(self) -> float:
        return self.clusters.n_clusters / self.lookback_days if self.lookback_days else 0.0

    def project(self, days: int) -> tuple[float, float]:
        """Erwartete Signale und Cluster nach ``days`` — bei unveraenderter Rate.

        Eine Planungsgroesse, keine Zusage: gemessen wurde auf demselben Zeitraum,
        der auch der Exploration diente, und ein Regimewechsel verschiebt sie.
        Genau dafuer existiert die vorab versiegelte Verlaengerungsregel.
        """
        return self.fires_per_day * days, self.clusters_per_day * days


def timestamp_to_ms(timestamp_utc: str) -> int:
    parsed = datetime.fromisoformat(timestamp_utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def build_frequency_report(
    signals_by_symbol: dict[str, list[Signal]],
    *,
    raw_fires: int,
    lookback_days: int,
    horizon: int,
    timeframe_ms: int = _HOUR_MS,
) -> FrequencyReport:
    """Reine Aggregation — in CI pruefbar, ohne Netz."""
    flat = [signal for group in signals_by_symbol.values() for signal in group]
    return FrequencyReport(
        lookback_days=lookback_days,
        horizon=horizon,
        raw_fires=raw_fires,
        label_capable_fires=len(flat),
        per_symbol_fires={symbol: len(group) for symbol, group in signals_by_symbol.items()},
        clusters=summarize_clusters(flat, timeframe_ms=timeframe_ms, horizon=horizon),
    )


async def measure(
    universe: list[str],
    *,
    timeframe: str,
    lookback_days: int,
    horizon: int,
) -> FrequencyReport:
    from app.market_data.binance_adapter import BinanceAdapter
    from app.research.runner import build_fetch, rsi_reentry_volume_confirmed

    fetch = build_fetch(BinanceAdapter().get_ohlcv)
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - lookback_days * _MS_PER_DAY

    by_symbol: dict[str, list[Signal]] = {}
    raw_fires = 0

    for symbol in universe:
        history = await load_ohlcv_history(symbol, timeframe, start_ms, end_ms, fetch)
        rows = build_feature_matrix(history.candles)
        total = len(rows)
        found: list[Signal] = []
        for index, row in enumerate(rows):
            side = rsi_reentry_volume_confirmed(row)
            if side == 0:
                continue
            raw_fires += 1
            # Label-faehig ist eine reine INDEX-Frage: existiert eine
            # Einstiegskerze t+1 und eine Ausstiegskerze t+h? Kein Preis noetig.
            if index + horizon < total:
                found.append(Signal(symbol, timestamp_to_ms(row.timestamp_utc), side))
        by_symbol[symbol] = found
        print(f"  {symbol:12s} bars={total:5d} fires={len(found):3d}")

    return build_frequency_report(
        by_symbol,
        raw_fires=raw_fires,
        lookback_days=lookback_days,
        horizon=horizon,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("universe_json", help="Artefakt aus universe_preflight")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--project-days", type=int, default=90)
    args = parser.parse_args()

    payload = json.loads(Path(args.universe_json).read_text(encoding="utf-8"))
    universe = payload["canonical_universe"]
    print(f"Universum: {len(universe)} Symbole, sha={payload['universe_sha256'][:16]}")

    report = await measure(
        universe,
        timeframe=args.timeframe,
        lookback_days=args.lookback_days,
        horizon=args.horizon,
    )
    stats = report.clusters

    print()
    print(f"raw fires                : {report.raw_fires}")
    print(
        f"label-capable fires      : {report.label_capable_fires} ({report.fires_per_day:.3f}/Tag)"
    )
    print(f"unique signal bars       : {stats.n_unique_bars}")
    print(f"Symbole mit >=1 Feuerung : {stats.n_symbols} von {len(universe)}")
    print()
    per_day = f"{report.clusters_per_day:.3f}/Tag"
    print(f"Overlap-Cluster ({args.horizon}h)     : {stats.n_clusters} ({per_day})")
    print(f"  median Groesse         : {stats.median_cluster_size}")
    print(f"  max Groesse            : {stats.max_cluster_size}")
    print(f"  max Spanne             : {stats.max_cluster_span_bars} Kerzen")
    print(f"  Symbole je Cluster (Ø) : {stats.mean_symbols_per_cluster:.2f}")
    print(f"  effective sample ratio : {stats.effective_sample_ratio:.3f}")
    print()
    print(f"top_symbol_share         : {stats.top_symbol_share:.1%}")
    print(f"top_cluster_share        : {stats.top_cluster_share:.1%}")

    ranked = sorted(stats.per_symbol_signals.items(), key=lambda kv: -kv[1])
    print("  staerkste 5            :", ", ".join(f"{s}={n}" for s, n in ranked[:5]))
    print("  schwaechste 5          :", ", ".join(f"{s}={n}" for s, n in ranked[-5:]))
    loo = stats.leave_one_out_top_symbol
    print(f"  ohne {loo.symbol}: {loo.n_signals} Signale / {loo.n_clusters} Cluster")

    fires, clusters = report.project(args.project_days)
    print()
    print(f"Hochrechnung auf {args.project_days} Tage (unveraenderte Rate):")
    print(f"  Signale                : {fires:.0f}")
    print(f"  Cluster                : {clusters:.0f}")
    return 0


if __name__ == "__main__":  # pragma: no cover - Einstiegspunkt
    raise SystemExit(asyncio.run(main()))
