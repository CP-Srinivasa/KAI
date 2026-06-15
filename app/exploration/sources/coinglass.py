"""CoinGlass exploration probes (api + scrape) — P0 derivatives source.

Extends the live V5 funding/OI evidence schiene with a broader derivatives feed
(funding rates, open interest, liquidations across exchanges).

API v4 requires the ``CG-API-KEY`` header. Without a key the probe reports a
clear ``disabled_no_api_key`` outcome (configured-but-unusable), which the
coverage report surfaces honestly.
"""

from __future__ import annotations

from typing import Any

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.http import fetch
from app.exploration.scrape_util import parse_html_signals
from app.exploration.settings import ExplorationSettings

_BASE = "https://open-api-v4.coinglass.com"


class CoinGlassApiProbe(ExplorationProbe):
    source_name = "coinglass"
    access_mode = "api"
    requires_key = True

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings
        self._key = settings.coinglass_api_key or None

    async def probe(self) -> ExplorationResult:
        if not self._key:
            return self.fail("disabled_no_api_key")
        symbol = self._s.sample_symbol.strip().upper()
        url = f"{_BASE}/api/futures/funding-rate/exchange-list"
        resp = await fetch(
            url,
            params={"symbol": symbol},
            headers={"CG-API-KEY": self._key, "accept": "application/json"},
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

        # CoinGlass signals success with body code "0"; any other code is an API
        # error returned over HTTP 200 (e.g. {"code":"401","msg":"Upgrade plan"}
        # on free-tier plan gating). Surface it honestly instead of "no records".
        if isinstance(resp.json, dict):
            code = str(resp.json.get("code", "0"))
            if code not in ("0", "200", "success"):
                msg = resp.json.get("msg") or "unknown"
                return self.fail(f"api_error:{code}:{msg}", meta=meta)

        records = _flatten_funding(resp.json, symbol, limit=self._s.max_records_per_probe)
        if not records:
            return self.fail("no_records_parsed", meta=meta)
        return self.ok(records, raw=resp.json, meta=meta)


class CoinGlassScrapeProbe(ExplorationProbe):
    source_name = "coinglass"
    access_mode = "scrape"
    requires_key = False

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings

    async def probe(self) -> ExplorationResult:
        url = "https://www.coinglass.com/FundingRate"
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


def _flatten_funding(payload: Any, symbol: str, *, limit: int) -> list[dict[str, Any]]:
    """Flatten CoinGlass funding-rate exchange list. Tolerant of shape drift."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "symbol": row.get("symbol") or symbol,
                "exchange": row.get("exchangeName") or row.get("exchange"),
                "funding_rate": row.get("fundingRate") or row.get("rate"),
                "next_funding_time": row.get("nextFundingTime"),
                "predicted_rate": row.get("predictedRate"),
            }
        )
    return out
