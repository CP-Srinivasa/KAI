"""Glassnode exploration probes (api + scrape) — P2 on-chain metrics.

The API needs a key (``api_key`` query param). Free keys expose only Tier-1
metrics; the high-value on-chain metrics sit behind paid tiers — the probe hits a
Tier-1 metric so the coverage report shows exactly where the free tier stops. The
scrape probe tests whether the (JS-rendered) Studio yields anything statically.
"""

from __future__ import annotations

from typing import Any

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.http import fetch
from app.exploration.scrape_util import parse_html_signals
from app.exploration.settings import ExplorationSettings

_BASE = "https://api.glassnode.com/v1"


class GlassnodeApiProbe(ExplorationProbe):
    source_name = "glassnode"
    access_mode = "api"
    requires_key = True

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings
        self._key = settings.glassnode_api_key or None

    async def probe(self) -> ExplorationResult:
        if not self._key:
            return self.fail("disabled_no_api_key")
        asset = self._s.sample_symbol.strip().upper()
        url = f"{_BASE}/metrics/market/price_usd_close"
        resp = await fetch(
            url,
            params={"a": asset, "api_key": self._key, "i": "24h"},
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

        records = _flatten_series(resp.json, asset, limit=self._s.max_records_per_probe)
        if not records:
            return self.fail("no_datapoints", meta=meta)
        return self.ok(records, raw=resp.json, meta=meta)


class GlassnodeScrapeProbe(ExplorationProbe):
    source_name = "glassnode"
    access_mode = "scrape"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings

    async def probe(self) -> ExplorationResult:
        # The Studio app is a key-gated SPA with no inline metric data; the
        # marketing root serves 200 server-rendered HTML. The scrape honestly
        # reports the static surface (which for Glassnode is thin — a finding).
        url = "https://glassnode.com/"
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


def _flatten_series(payload: Any, asset: str, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    out: list[dict[str, Any]] = []
    for point in payload[-limit:]:
        if isinstance(point, dict) and "t" in point:
            out.append({"asset": asset, "t": point.get("t"), "v": point.get("v")})
    return out
