"""Praezision je Signalpfad neben ihrer naiven Basisrate (S2, 2026-09-16).

Messung S1 am 16.09.: die Episoden-Praezision lag exakt auf der naiven Basisrate
"immer bullish zu jeder vollen Stunde" (TradingView 55,6 % gegen 56,0 %,
Technical 60,8 % gegen 60,5 %). Eine Praezisionszahl ohne diese Basisrate daneben
ist nicht als Qualitaet lesbar — dieser Bericht stellt beide dauerhaft
nebeneinander, im selben Fenster und nach derselben Regel.

Aufbau:
* Population: je Pfad die REIFEN bullischen Episoden der letzten 30 Tage — Anker
  zwischen ``now - 30 d`` und ``now - 168 h``, damit alle Bewertungsstufen des
  Annotators abgelaufen sind. Episoden-Regel wie ``outcome_dedupe_report``.
* Basisrate: ``app.research.naive_baseline`` (Regel aus dem Annotator importiert),
  je Basiswert stundenweise, gewichtet nach den Episoden des Pfads.
* Urteil: vorab festgelegtes S1-Kriterium — Timing-Wert nur, wenn die
  Wilson-95-Untergrenze ueber der Basisrate liegt.

Preise werden ueber ``fetch_series`` injiziert; der Standard holt Binance-1-h-
Kerzen (oeffentlich, dieselbe Preisbasis wie der Annotator). Berechnet wird im
taeglichen Daily-Strategy-Lauf; das Briefing liest nur das abgelegte Ergebnis,
solange es frisch ist — kein Netzaufruf im Briefing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.observability.outcome_dedupe_report import (
    SIGNAL_PATH_ORDER,
    build_episode_reports_by,
    episode_direction,
    signal_path_of,
)
from app.research.decomposition import assess_group_table
from app.research.naive_baseline import (
    STAGE_AGES_HOURS,
    PriceSeries,
    compare_to_baseline,
    hourly_baseline,
    weighted_rate,
)

REPORT_FILENAME = "precision_baseline_30d.json"
WINDOW_DAYS = 30
MAX_AGE_HOURS = 36.0
_BTC = "BTC/USDT"
# Vorlauf fuer die 24-h-Volatilitaet und das Naechster-Preis-Fenster.
_PRICE_LEAD = timedelta(days=2)

_LABEL_DE = {
    "tradingview": "TradingView",
    "news": "Nachrichten",
    "technical": "Technical",
    "other": "sonstige",
}
_LABEL_EN = {
    "tradingview": "TradingView",
    "news": "News",
    "technical": "Technical",
    "other": "Other",
}

FetchSeries = Callable[[str, datetime, datetime], PriceSeries | None]


def _norm_asset(asset: object) -> str | None:
    if not isinstance(asset, str) or not asset.strip():
        return None
    base = asset.strip().upper().split("/")[0]
    return f"{base}/USDT"


def build_precision_baseline_report(
    *,
    now: datetime,
    fetch_series: FetchSeries,
    audit_path: str | Path = Path("artifacts/alert_outcomes.jsonl"),
    alert_audit_path: str | Path = Path("artifacts/alert_audit.jsonl"),
) -> dict[str, Any]:
    window_start = now - timedelta(days=WINDOW_DAYS)
    mature_end = now - timedelta(hours=STAGE_AGES_HOURS[-1])

    def key_of(
        doc_id: str, rec: dict[str, object], anchor: datetime | None, sentiment: str | None
    ) -> str | None:
        if anchor is None or not (window_start <= anchor <= mature_end):
            return None
        if episode_direction(rec, sentiment) != "bullish":
            return None
        asset = _norm_asset(rec.get("asset"))
        if asset is None:
            return None
        return f"{signal_path_of(doc_id)}|{asset}"

    grouped = build_episode_reports_by(
        key_of=key_of, audit_path=audit_path, alert_audit_path=alert_audit_path
    )

    report: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "window_start": window_start.isoformat(),
        "mature_end": mature_end.isoformat(),
        "paths": {},
        "assets_without_prices": [],
    }
    if not grouped:
        return report

    cache: dict[str, PriceSeries | None] = {}

    def series_for(asset: str) -> PriceSeries | None:
        if asset not in cache:
            cache[asset] = fetch_series(asset, window_start - _PRICE_LEAD, now)
        return cache[asset]

    btc = series_for(_BTC)
    rates: dict[str, float] = {}
    missing: set[str] = set()
    per_path: dict[str, dict[str, tuple[int, int]]] = {}
    for key, rep in grouped.items():
        path, asset = key.split("|", 1)
        per_path.setdefault(path, {})[asset] = (rep.episode_total, rep.episode_hit)
        if asset in rates or asset in missing:
            continue
        series = series_for(asset)
        counts = (
            hourly_baseline(series, btc, start=window_start, end=mature_end)
            if series and btc
            else None
        )
        if counts is None or counts.rate is None:
            missing.add(asset)
        else:
            rates[asset] = counts.rate

    for path in SIGNAL_PATH_ORDER:
        assets = per_path.get(path)
        if not assets:
            continue
        priced = {a: v for a, v in assets.items() if a in rates}
        n = sum(total for total, _ in priced.values())
        if n == 0:
            continue
        hits = sum(hit for _, hit in priced.values())
        base = weighted_rate({a: total for a, (total, _) in priced.items()}, rates)
        result = compare_to_baseline(hits=hits, n=n, baseline_rate=base)
        # Direktive 2026-08-08, kein Aggregat ohne Zerlegung: traegt ein einzelner
        # Basiswert das Pfad-Urteil (TradingView: BTC 61 % der Episoden am 16.09.),
        # muss das neben der Zahl stehen.
        result.update(
            {
                "per_asset": {
                    a: {"episodes": total, "hits": hit, "baseline_rate": rates[a]}
                    for a, (total, hit) in sorted(priced.items())
                },
                "decomposition": assess_group_table(
                    {a: {"n": total, "positives": hit} for a, (total, hit) in priced.items()}
                ),
            }
        )
        report["paths"][path] = result

    report["assets_without_prices"] = sorted(missing)
    return report


def write_report(report: dict[str, Any], artifacts_dir: str | Path) -> Path:
    path = Path(artifacts_dir) / REPORT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_fresh_report(
    artifacts_dir: str | Path, *, now: datetime, max_age_hours: float = MAX_AGE_HOURS
) -> dict[str, Any] | None:
    """Der abgelegte Bericht, wenn lesbar und nicht aelter als ``max_age_hours``."""
    try:
        data = json.loads((Path(artifacts_dir) / REPORT_FILENAME).read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(str(data["generated_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    if now - generated > timedelta(hours=max_age_hours):
        return None
    return data if isinstance(data, dict) else None


def _verdict_de(value: object) -> str:
    if value is True:
        return "Timing-Wert belegt"
    if value is False:
        return "kein Timing-Wert belegt"
    return "nicht beurteilbar"


def _verdict_en(value: object) -> str:
    if value is True:
        return "timing value shown"
    if value is False:
        return "no timing value shown"
    return "not assessable"


def _rows(report: dict[str, Any] | None) -> Iterator[tuple[str, dict[str, Any]]]:
    if not report:
        return
    paths = report.get("paths") or {}
    for path in SIGNAL_PATH_ORDER:
        r = paths.get(path)
        if not r or not r.get("n") or r.get("baseline_rate") is None:
            continue
        yield path, r


def format_baseline_de(report: dict[str, Any] | None) -> str:
    parts = [
        f"{_LABEL_DE[path]} {100 * r['precision']:.1f} % "
        f"vs. Basis {100 * r['baseline_rate']:.1f} % "
        f"(n={r['n']}, Wilson95 {100 * r['wilson_low']:.1f}–{100 * r['wilson_high']:.1f} %) "
        f"→ {_verdict_de(r.get('timing_value'))}"
        for path, r in _rows(report)
    ]
    return " · ".join(parts) if parts else "—"


def format_baseline_en(report: dict[str, Any] | None) -> list[str]:
    rows = [
        f"    {(_LABEL_EN[path] + ':').ljust(13)}{100 * r['precision']:.1f}% vs base "
        f"{100 * r['baseline_rate']:.1f}% (n={r['n']}, CI {100 * r['wilson_low']:.1f}–"
        f"{100 * r['wilson_high']:.1f}%) - {_verdict_en(r.get('timing_value'))}"
        for path, r in _rows(report)
    ]
    return ["  30d vs naive baseline (matured, bullish):", *rows] if rows else []


def binance_series_fetcher() -> FetchSeries:
    """Standard-Preisquelle: Binance-1-h-Kerzen, seitenweise, synchron gekapselt."""
    import asyncio

    from app.market_data.binance_adapter import BinanceAdapter

    async def _fetch(asset: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        adapter = BinanceAdapter()
        out: list[tuple[datetime, float]] = []
        cursor = start
        while cursor < end:
            candles = await adapter.get_ohlcv(
                asset, timeframe="1h", limit=1000, start_time_ms=int(cursor.timestamp() * 1000)
            )
            if not candles:
                break
            last: datetime | None = None
            for candle in candles:
                ts = datetime.fromisoformat(candle.timestamp_utc.replace("Z", "+00:00"))
                ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
                out.append((ts, float(candle.close)))
                last = ts
            if last is None or last < cursor:
                break
            cursor = last + timedelta(hours=1)
        return out

    def fetch(asset: str, start: datetime, end: datetime) -> PriceSeries | None:
        try:
            series = asyncio.run(_fetch(asset, start, end))
        except Exception:  # noqa: BLE001 — eine fehlende Preisreihe nimmt nur den Basiswert heraus
            return None
        return series or None

    return fetch


__all__ = [
    "MAX_AGE_HOURS",
    "REPORT_FILENAME",
    "WINDOW_DAYS",
    "binance_series_fetcher",
    "build_precision_baseline_report",
    "format_baseline_de",
    "format_baseline_en",
    "load_fresh_report",
    "write_report",
]
