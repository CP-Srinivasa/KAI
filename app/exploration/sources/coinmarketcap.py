"""CoinMarketCap exploration probes (api + scrape) — P3 (largely redundant w/ CoinGecko).

API needs the ``X-CMC_PRO_API_KEY`` header (free tier available). The scrape probe
targets the coin page, which ships data inline as ``__NEXT_DATA__`` — a useful
contrast to JS-shell sources where static scraping yields nothing.
"""

from __future__ import annotations

from typing import Any

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.http import fetch
from app.exploration.scrape_util import parse_html_signals
from app.exploration.settings import ExplorationSettings

_BASE = "https://pro-api.coinmarketcap.com/v1"

_SYMBOL_TO_SLUG = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "xrp",
    "ADA": "cardano",
}


class CoinMarketCapApiProbe(ExplorationProbe):
    source_name = "coinmarketcap"
    access_mode = "api"
    requires_key = True

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings
        self._key = settings.coinmarketcap_api_key or None

    async def probe(self) -> ExplorationResult:
        if not self._key:
            return self.fail("disabled_no_api_key")
        symbol = self._s.sample_symbol.strip().upper()
        url = f"{_BASE}/cryptocurrency/quotes/latest"
        resp = await fetch(
            url,
            params={"symbol": symbol, "convert": "USD"},
            headers={"X-CMC_PRO_API_KEY": self._key, "accept": "application/json"},
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        meta = ProbeMeta(
            http_status=resp.status,
            latency_ms=resp.latency_ms,
            bytes=resp.bytes,
            rate_limit_remaining=resp.rate_limit_remaining,
        )
        if not resp.ok:
            return self.fail(resp.error or "request_failed", meta=meta)

        records = _flatten_quotes(resp.json, symbol)
        if not records:
            return self.fail("no_records_parsed", meta=meta)
        return self.ok(records, raw=resp.json, meta=meta)


class CoinMarketCapScrapeProbe(ExplorationProbe):
    source_name = "coinmarketcap"
    access_mode = "scrape"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings

    async def probe(self) -> ExplorationResult:
        slug = _SYMBOL_TO_SLUG.get(
            self._s.sample_symbol.strip().upper(), self._s.sample_symbol.strip().lower()
        )
        url = f"https://coinmarketcap.com/currencies/{slug}/"
        resp = await fetch(
            url,
            expect="text",
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        meta = ProbeMeta(http_status=resp.status, latency_ms=resp.latency_ms, bytes=resp.bytes)
        if not resp.ok or not resp.text:
            return self.fail(resp.error or "empty_html", meta=meta)
        record = {"_url": url, **parse_html_signals(resp.text)}
        return self.ok([record], raw={"html_bytes": len(resp.text)}, meta=meta)


def _flatten_quotes(payload: Any, symbol: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or {}
    entry = data.get(symbol)
    entries = entry if isinstance(entry, list) else [entry] if isinstance(entry, dict) else []
    out: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        usd = ((item.get("quote") or {}).get("USD")) or {}
        out.append(
            {
                "symbol": item.get("symbol") or symbol,
                "name": item.get("name"),
                "cmc_rank": item.get("cmc_rank"),
                "price_usd": usd.get("price"),
                "volume_24h": usd.get("volume_24h"),
                "percent_change_24h": usd.get("percent_change_24h"),
                "market_cap": usd.get("market_cap"),
            }
        )
    return out
