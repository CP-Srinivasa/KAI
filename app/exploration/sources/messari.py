"""Messari exploration probes (api + scrape) — P1 research + metrics.

Two layers from one source: asset metrics (market-data) and a news feed. The
classic ``data.messari.io/api/v1`` metrics endpoint works on the free tier
(rate-limited); an ``x-messari-api-key`` is sent when configured to lift limits.
"""

from __future__ import annotations

from typing import Any

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.http import fetch
from app.exploration.scrape_util import parse_html_signals
from app.exploration.settings import ExplorationSettings

_BASE = "https://data.messari.io/api"

_SYMBOL_TO_SLUG = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "xrp",
    "ADA": "cardano",
}


def _slug(symbol: str) -> str:
    return _SYMBOL_TO_SLUG.get(symbol.strip().upper(), symbol.strip().lower())


class MessariApiProbe(ExplorationProbe):
    source_name = "messari"
    access_mode = "api"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings
        self._key = settings.messari_api_key or None

    def _headers(self) -> dict[str, str]:
        h = {"accept": "application/json"}
        if self._key:
            h["x-messari-api-key"] = self._key
        return h

    async def probe(self) -> ExplorationResult:
        slug = _slug(self._s.sample_symbol)
        records: list[dict[str, Any]] = []
        raw: dict[str, Any] = {}

        md = await fetch(
            f"{_BASE}/v1/assets/{slug}/metrics/market-data",
            headers=self._headers(),
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        meta = ProbeMeta(
            http_status=md.status,
            latency_ms=md.latency_ms,
            bytes=md.bytes,
            rate_limit_remaining=md.rate_limit_remaining,
        )
        raw["market_data"] = md.json
        if not md.ok:
            return self.fail(f"market_data:{md.error}", meta=meta)
        if isinstance(md.json, dict):
            data = md.json.get("data") or {}
            market = data.get("market_data") or {}
            records.append(
                {
                    "_endpoint": "metrics/market-data",
                    "slug": slug,
                    "price_usd": market.get("price_usd"),
                    "volume_24h_usd": market.get("volume_last_24_hours"),
                    "pct_change_24h": market.get("percent_change_usd_last_24_hours"),
                }
            )

        news = await fetch(
            f"{_BASE}/v1/news",
            params={"page": 1},
            headers=self._headers(),
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        raw["news"] = news.json
        if news.ok and isinstance(news.json, dict):
            for article in (news.json.get("data") or [])[:10]:
                if isinstance(article, dict):
                    records.append(
                        {
                            "_endpoint": "news",
                            "title": article.get("title"),
                            "published_at": article.get("published_at"),
                            "author": (article.get("author") or {}).get("name"),
                            "url": article.get("url"),
                        }
                    )

        if not records:
            return self.fail("no_records_parsed", meta=meta)
        return self.ok(records, raw=raw, meta=meta)


class MessariScrapeProbe(ExplorationProbe):
    source_name = "messari"
    access_mode = "scrape"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings

    async def probe(self) -> ExplorationResult:
        url = "https://messari.io/research"
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
