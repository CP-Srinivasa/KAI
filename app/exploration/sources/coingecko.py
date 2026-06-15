"""CoinGecko exploration probes (api + scrape).

API: free tier needs no key (rate-limited); a pro key (x-cg-pro-api-key) unlocks
the pro host. This probe deliberately exercises endpoints BEYOND simple price —
markets, trending, global — to test the cost finding: "is the pro key justified
by richer data, or does the free tier / a scrape suffice for price alone?"
"""

from __future__ import annotations

from typing import Any

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.http import fetch
from app.exploration.scrape_util import parse_html_signals
from app.exploration.settings import ExplorationSettings

_FREE_BASE = "https://api.coingecko.com/api/v3"
_PRO_BASE = "https://pro-api.coingecko.com/api/v3"

_SYMBOL_TO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
}


def _coin_id(symbol: str) -> str:
    return _SYMBOL_TO_ID.get(symbol.strip().upper(), symbol.strip().lower())


class CoinGeckoApiProbe(ExplorationProbe):
    source_name = "coingecko"
    access_mode = "api"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings
        self._key = settings.coingecko_api_key or None
        self._base = _PRO_BASE if self._key else _FREE_BASE

    def _headers(self) -> dict[str, str]:
        return {"x-cg-pro-api-key": self._key} if self._key else {}

    async def probe(self) -> ExplorationResult:
        coin_id = _coin_id(self._s.sample_symbol)
        records: list[dict[str, Any]] = []
        raw: dict[str, Any] = {}
        last_meta = ProbeMeta()

        markets = await fetch(
            f"{self._base}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": coin_id,
                "price_change_percentage": "24h,7d,30d",
            },
            headers=self._headers(),
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        raw["markets"] = markets.json
        last_meta = ProbeMeta(
            http_status=markets.status,
            latency_ms=markets.latency_ms,
            bytes=markets.bytes,
            rate_limit_remaining=markets.rate_limit_remaining,
        )
        if not markets.ok:
            return self.fail(f"markets:{markets.error}", meta=last_meta)
        if isinstance(markets.json, list):
            for coin in markets.json[: self._s.max_records_per_probe]:
                if isinstance(coin, dict):
                    records.append({"_endpoint": "coins/markets", **_flatten_coin(coin)})

        trending = await fetch(
            f"{self._base}/search/trending",
            headers=self._headers(),
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        raw["trending"] = trending.json
        if trending.ok and isinstance(trending.json, dict):
            for entry in (trending.json.get("coins") or [])[:7]:
                item = entry.get("item") if isinstance(entry, dict) else None
                if isinstance(item, dict):
                    records.append(
                        {
                            "_endpoint": "search/trending",
                            "id": item.get("id"),
                            "symbol": item.get("symbol"),
                            "market_cap_rank": item.get("market_cap_rank"),
                            "score": item.get("score"),
                        }
                    )

        if not records:
            return self.fail("no_records_parsed", meta=last_meta)
        last_meta.extra["tier"] = "pro" if self._key else "free"
        return self.ok(records, raw=raw, meta=last_meta)


class CoinGeckoScrapeProbe(ExplorationProbe):
    source_name = "coingecko"
    access_mode = "scrape"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings

    async def probe(self) -> ExplorationResult:
        coin_id = _coin_id(self._s.sample_symbol)
        url = f"https://www.coingecko.com/en/coins/{coin_id}"
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


def _flatten_coin(coin: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "symbol",
        "current_price",
        "market_cap",
        "market_cap_rank",
        "total_volume",
        "price_change_percentage_24h",
        "price_change_percentage_7d_in_currency",
        "price_change_percentage_30d_in_currency",
        "circulating_supply",
        "last_updated",
    )
    return {k: coin.get(k) for k in keys}
