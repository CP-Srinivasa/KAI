"""Rich per-symbol indicator snapshots (access_mode="snapshot").

The production/graduation shape: one rich record per watchlist symbol. Reuses the
ExplorationProbe/runner/capture/report infra unchanged — a snapshot is just a
probe that emits many rich records.

Coverage of reality (measured 2026-06-15):
  - coingecko: full keyless snapshot (price/mcap/ath/supply/multi-window changes
    + OHLC-derived TA: SMA/RSI/volatility).
  - messari:   keyless metadata snapshot (rank/sector/tags/flags; values key-gated).
  - coinmarketcap: keyless scrape snapshot (live price/volume from meta).
  - dune:      per-query snapshot (key + public-sample fallback).
  - coinglass/glassnode/nansen: scaffold that reports the paid-key wall honestly
    until a usable key/plan exists.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.exploration import ta
from app.exploration.base import ExplorationResult, ProbeMeta, SnapshotProbe
from app.exploration.http import fetch
from app.exploration.scrape_util import parse_html_signals
from app.exploration.settings import ExplorationSettings
from app.exploration.sources.coingecko import _FREE_BASE, _PRO_BASE, _coin_id
from app.exploration.sources.coinmarketcap import _SYMBOL_TO_SLUG
from app.exploration.sources.dune import _flatten_rows, _resolve_query_id

_MARKETS_FIELDS = (
    "current_price",
    "market_cap",
    "market_cap_rank",
    "fully_diluted_valuation",
    "total_volume",
    "high_24h",
    "low_24h",
    "price_change_percentage_1h_in_currency",
    "price_change_percentage_24h_in_currency",
    "price_change_percentage_7d_in_currency",
    "price_change_percentage_30d_in_currency",
    "price_change_percentage_1y_in_currency",
    "ath",
    "ath_change_percentage",
    "atl",
    "circulating_supply",
    "total_supply",
    "max_supply",
    "last_updated",
)


async def _polite(settings: ExplorationSettings) -> None:
    delay = min(settings.min_request_interval_seconds, 1.0)
    if delay > 0:
        await asyncio.sleep(delay)


class CoinGeckoSnapshotProbe(SnapshotProbe):
    source_name = "coingecko"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        super().__init__(settings)
        self._key = settings.coingecko_api_key or None
        self._base = _PRO_BASE if self._key else _FREE_BASE

    def _headers(self) -> dict[str, str]:
        return {"x-cg-pro-api-key": self._key} if self._key else {}

    async def probe(self) -> ExplorationResult:
        symbols = self.symbols
        ids = [_coin_id(s) for s in symbols]
        markets = await fetch(
            f"{self._base}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(ids),
                "price_change_percentage": "1h,24h,7d,30d,1y",
            },
            headers=self._headers(),
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        meta = ProbeMeta(
            http_status=markets.status,
            latency_ms=markets.latency_ms,
            bytes=markets.bytes,
            rate_limit_remaining=markets.rate_limit_remaining,
        )
        if not markets.ok or not isinstance(markets.json, list):
            return self.fail(f"markets:{markets.error or 'bad_payload'}", meta=meta)

        by_id = {c.get("id"): c for c in markets.json if isinstance(c, dict)}
        records: list[dict[str, Any]] = []
        for symbol, coin_id in zip(symbols, ids, strict=False):
            coin = by_id.get(coin_id)
            if not isinstance(coin, dict):
                records.append({"symbol": symbol, "_status": "not_found"})
                continue
            rec: dict[str, Any] = {"symbol": symbol, "id": coin_id}
            rec.update({k: coin.get(k) for k in _MARKETS_FIELDS})
            if self._s.snapshot_ta_enabled:
                await _polite(self._s)
                rec.update(await self._ta(coin_id, symbol))
            records.append(rec)

        meta.extra["tier"] = "pro" if self._key else "free"
        return self.ok(records, raw={"markets": markets.json}, meta=meta)

    async def _ta(self, coin_id: str, symbol: str) -> dict[str, Any]:
        ohlc = await fetch(
            f"{self._base}/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": "30"},
            headers=self._headers(),
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        if not ohlc.ok or not isinstance(ohlc.json, list):
            return {"ta_available": False}
        closes = [row[4] for row in ohlc.json if isinstance(row, list) and len(row) >= 5]
        closes = [float(c) for c in closes if isinstance(c, (int, float))]
        hi, lo = ta.high_low(closes)
        return {
            "ta_available": bool(closes),
            "ta_points": len(closes),
            "sma_7": ta.sma(closes, 7),
            "sma_14": ta.sma(closes, 14),
            "rsi_14": ta.rsi(closes, 14),
            "volatility_pct": ta.realized_volatility(closes),
            "period_high": hi,
            "period_low": lo,
        }


class MessariSnapshotProbe(SnapshotProbe):
    source_name = "messari"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        super().__init__(settings)
        self._key = settings.messari_api_key or None

    async def probe(self) -> ExplorationResult:
        headers = {"accept": "application/json"}
        if self._key:
            headers["x-messari-api-key"] = self._key
        resp = await fetch(
            "https://api.messari.io/metrics/v2/assets",
            params={"limit": 500},
            headers=headers,
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        meta = ProbeMeta(http_status=resp.status, latency_ms=resp.latency_ms, bytes=resp.bytes)
        if not resp.ok or not isinstance(resp.json, dict):
            return self.fail(resp.error or "bad_payload", meta=meta)
        assets = resp.json.get("data") or []
        by_symbol = {
            str(a.get("symbol")).upper(): a
            for a in assets
            if isinstance(a, dict) and a.get("symbol")
        }
        records: list[dict[str, Any]] = []
        for symbol in self.symbols:
            a = by_symbol.get(symbol)
            if not isinstance(a, dict):
                records.append({"symbol": symbol, "_status": "not_found"})
                continue
            tags = a.get("tags")
            records.append(
                {
                    "symbol": symbol,
                    "name": a.get("name"),
                    "slug": a.get("slug"),
                    "rank": a.get("rank"),
                    "sector": a.get("sector"),
                    "tags": ",".join(tags) if isinstance(tags, list) else None,
                    "has_market_data": a.get("hasMarketData"),
                    "has_news": a.get("hasNews"),
                    "has_research": a.get("hasResearch"),
                }
            )
        return self.ok(records, raw={"asset_count": len(assets)}, meta=meta)


class CoinMarketCapSnapshotProbe(SnapshotProbe):
    source_name = "coinmarketcap"
    requires_key = False

    async def probe(self) -> ExplorationResult:
        records: list[dict[str, Any]] = []
        last_meta = ProbeMeta()
        for i, symbol in enumerate(self.symbols):
            if i > 0:
                await _polite(self._s)
            slug = _SYMBOL_TO_SLUG.get(symbol, symbol.lower())
            url = f"https://coinmarketcap.com/currencies/{slug}/"
            resp = await fetch(
                url,
                expect="text",
                timeout=self._s.timeout_seconds,
                user_agent=self._s.scrape_user_agent,
            )
            last_meta = ProbeMeta(
                http_status=resp.status, latency_ms=resp.latency_ms, bytes=resp.bytes
            )
            if not resp.ok or not resp.text:
                records.append({"symbol": symbol, "_status": resp.error or "empty_html"})
                continue
            sig = parse_html_signals(resp.text)
            records.append(
                {
                    "symbol": symbol,
                    "price_usd": sig.get("meta_price_usd"),
                    "volume_usd": sig.get("meta_volume_usd"),
                    "title": sig.get("title"),
                }
            )
        if not any("price_usd" in r for r in records):
            return self.fail("no_price_extracted", meta=last_meta)
        return self.ok(records, meta=last_meta)


class DuneSnapshotProbe(SnapshotProbe):
    source_name = "dune"
    requires_key = True

    def __init__(self, settings: ExplorationSettings) -> None:
        super().__init__(settings)
        self._key = settings.dune_api_key or None
        self._query_id = settings.dune_query_id

    async def probe(self) -> ExplorationResult:
        if not self._key:
            return self.fail("disabled_no_api_key")
        query_id, used_fallback = _resolve_query_id(self._query_id)
        resp = await fetch(
            f"https://api.dune.com/api/v1/query/{query_id}/results",
            headers={"X-Dune-API-Key": self._key, "accept": "application/json"},
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        meta = ProbeMeta(http_status=resp.status, latency_ms=resp.latency_ms, bytes=resp.bytes)
        meta.extra["query_id"] = query_id
        if used_fallback:
            meta.extra["used_public_sample"] = True
        if not resp.ok:
            return self.fail(resp.error or "request_failed", meta=meta)
        records = _flatten_rows(resp.json, limit=self._s.max_records_per_probe)
        if not records:
            return self.fail("no_rows_in_result", meta=meta)
        return self.ok(records, raw=resp.json, meta=meta)


class _GatedSnapshotProbe(SnapshotProbe):
    """Scaffold for paid-key sources: reports the access wall honestly per run."""

    requires_key = True
    _key_attr = ""
    _wall = "requires_paid_key"

    def __init__(self, settings: ExplorationSettings) -> None:
        super().__init__(settings)
        self._key = getattr(settings, self._key_attr, "") or None

    async def probe(self) -> ExplorationResult:
        if not self._key:
            return self.fail("disabled_no_api_key")
        # A key is present but these sources gate their indicator data behind a
        # paid plan (measured). The scaffold is ready; it fails honestly until the
        # plan unlocks. (Per-source real fetch can be wired here once paid.)
        return self.fail(self._wall)


class CoinGlassSnapshotProbe(_GatedSnapshotProbe):
    source_name = "coinglass"
    _key_attr = "coinglass_api_key"
    _wall = "requires_paid_plan:coinglass_free_key_gated"


class GlassnodeSnapshotProbe(_GatedSnapshotProbe):
    source_name = "glassnode"
    _key_attr = "glassnode_api_key"
    _wall = "requires_paid_key:glassnode"


class NansenSnapshotProbe(_GatedSnapshotProbe):
    source_name = "nansen"
    _key_attr = "nansen_api_key"
    _wall = "requires_paid_key:nansen"


def build_snapshot_probes(settings: ExplorationSettings) -> list[SnapshotProbe]:
    """All snapshot probes for enabled sources (gated by snapshots_enabled)."""
    probes: list[SnapshotProbe] = []
    if not settings.snapshots_enabled:
        return probes
    if settings.coingecko_enabled:
        probes.append(CoinGeckoSnapshotProbe(settings))
    if settings.messari_enabled:
        probes.append(MessariSnapshotProbe(settings))
    if settings.coinmarketcap_enabled:
        probes.append(CoinMarketCapSnapshotProbe(settings))
    if settings.dune_enabled:
        probes.append(DuneSnapshotProbe(settings))
    if settings.coinglass_enabled:
        probes.append(CoinGlassSnapshotProbe(settings))
    if settings.glassnode_enabled:
        probes.append(GlassnodeSnapshotProbe(settings))
    if settings.nansen_enabled:
        probes.append(NansenSnapshotProbe(settings))
    return probes
