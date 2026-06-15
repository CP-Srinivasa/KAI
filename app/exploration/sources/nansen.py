"""Nansen exploration probe (api only) — P3 smart-money / wallet flows.

Nansen's API is paid and gated; the free tier is thin and most endpoints require
POST with an authorised key. This probe's job is mostly to DOCUMENT the access
wall honestly: with a key it attempts a smart-money endpoint and captures
whatever the tier returns; without a key it reports ``disabled_no_api_key``.

Hard line (DEC-SRC-EXPLORE-001): the auth wall is NOT bypassed — an unauthorised
or insufficient-tier response is recorded as a finding, never circumvented.
"""

from __future__ import annotations

from typing import Any

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.http import fetch
from app.exploration.settings import ExplorationSettings

_BASE = "https://api.nansen.ai/api/v1"


class NansenApiProbe(ExplorationProbe):
    source_name = "nansen"
    access_mode = "api"
    requires_key = True

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings
        self._key = settings.nansen_api_key or None

    async def probe(self) -> ExplorationResult:
        if not self._key:
            return self.fail("disabled_no_api_key")
        symbol = self._s.sample_symbol.strip().upper()
        url = f"{_BASE}/smart-money/inflows"
        resp = await fetch(
            url,
            method="POST",
            headers={"apiKey": self._key, "accept": "application/json"},
            json_body={"chain": "ethereum", "timeframe": "1d", "symbol": symbol},
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
            # Record the access-wall outcome as a finding (e.g. http_401/http_403).
            return self.fail(resp.error or "request_failed", meta=meta)

        records = _flatten(resp.json, limit=self._s.max_records_per_probe)
        if not records:
            return self.fail("no_records_parsed", meta=meta)
        return self.ok(records, raw=resp.json, meta=meta)


def _flatten(payload: Any, *, limit: int) -> list[dict[str, Any]]:
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        data = payload.get("data")
        rows = data if isinstance(data, list) else [payload]
    else:
        return []
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            out.append({str(k): v for k, v in row.items()})
    return out
