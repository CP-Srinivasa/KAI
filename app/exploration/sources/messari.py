"""Messari exploration probes (api + scrape) — P1 research + metrics.

The legacy keyless ``data.messari.io/api/v1`` endpoints are gone (404). The
current ``api.messari.io/metrics/v2/assets`` endpoint works WITHOUT a key and
returns rich per-asset metadata (symbol, sector/sub-sector, tags, rank, and
capability flags hasMarketData/hasNews/hasResearch/...). A key, when present, is
sent to lift rate limits and unlock key-gated market-data endpoints.
"""

from __future__ import annotations

from typing import Any

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.http import fetch
from app.exploration.scrape_util import parse_html_signals
from app.exploration.settings import ExplorationSettings

_BASE = "https://api.messari.io"

_METADATA_FIELDS = (
    "id",
    "name",
    "slug",
    "symbol",
    "category",
    "sector",
    "rank",
    "hasMarketData",
    "hasNews",
    "hasResearch",
    "hasIntel",
)


class MessariApiProbe(ExplorationProbe):
    source_name = "messari"
    access_mode = "api"
    requires_key = False  # the assets metrics endpoint is keyless

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings
        self._key = settings.messari_api_key or None

    def _headers(self) -> dict[str, str]:
        h = {"accept": "application/json"}
        if self._key:
            h["x-messari-api-key"] = self._key
        return h

    async def probe(self) -> ExplorationResult:
        symbol = self._s.sample_symbol.strip().upper()
        limit = min(self._s.max_records_per_probe, 100)
        resp = await fetch(
            f"{_BASE}/metrics/v2/assets",
            params={"limit": limit},
            headers=self._headers(),
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
        if not isinstance(resp.json, dict):
            return self.fail("unexpected_payload", meta=meta)

        assets = resp.json.get("data")
        if not isinstance(assets, list) or not assets:
            return self.fail("no_assets_in_payload", meta=meta)

        records: list[dict[str, Any]] = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            rec: dict[str, Any] = {"_endpoint": "metrics/v2/assets"}
            rec.update({k: asset.get(k) for k in _METADATA_FIELDS})
            tags = asset.get("tags")
            rec["tags"] = ",".join(tags) if isinstance(tags, list) else None
            records.append(rec)

        # Surface the configured sample symbol first if present.
        records.sort(key=lambda r: (str(r.get("symbol")) != symbol, r.get("rank") or 1e9))
        meta.extra["tier"] = "keyed" if self._key else "keyless"
        return self.ok(records, raw=resp.json, meta=meta)


class MessariScrapeProbe(ExplorationProbe):
    source_name = "messari"
    access_mode = "scrape"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings

    async def probe(self) -> ExplorationResult:
        # The research index is bot-gated (403); the marketing root serves 200
        # server-rendered HTML, so the scrape honestly reports what static HTML
        # exposes (title/OpenGraph/JSON-LD) without any bot-evasion.
        url = "https://messari.io/"
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
