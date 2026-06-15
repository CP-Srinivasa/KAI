"""Dune Analytics exploration probe (api only) — P1 on-chain queries.

Fetches the *latest cached results* of an operator-curated query via
``GET /query/{id}/results`` (cheap — does not spend execution credits). Requires
both a Dune API key and a configured ``EXPLORATION_DUNE_QUERY_ID``; otherwise the
probe reports a clear disabled outcome.
"""

from __future__ import annotations

from typing import Any

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.http import fetch
from app.exploration.settings import ExplorationSettings

_BASE = "https://api.dune.com/api/v1"

# A public Dune query whose cached results any API key can fetch (token price
# feed: contract_address/hour/price). Used as a working sample when the operator
# has not configured their own numeric query id, so Dune is demonstrable
# out-of-the-box. Override with EXPLORATION_DUNE_QUERY_ID=<your numeric id>.
DEFAULT_SAMPLE_QUERY_ID = "4132129"


def _resolve_query_id(raw: str | None) -> tuple[str, bool]:
    """Return (query_id, used_fallback).

    Numeric → use as-is. Non-numeric (e.g. a Dune username pasted by mistake) →
    fall back to the public sample so the probe still demonstrates Dune.
    """
    if raw and str(raw).strip().isdigit():
        return str(raw).strip(), False
    return DEFAULT_SAMPLE_QUERY_ID, True


class DuneApiProbe(ExplorationProbe):
    source_name = "dune"
    access_mode = "api"
    requires_key = True

    def __init__(self, settings: ExplorationSettings) -> None:
        self._s = settings
        self._key = settings.dune_api_key or None
        self._query_id = settings.dune_query_id

    async def probe(self) -> ExplorationResult:
        if not self._key:
            return self.fail("disabled_no_api_key")

        query_id, used_fallback = _resolve_query_id(self._query_id)

        url = f"{_BASE}/query/{query_id}/results"
        resp = await fetch(
            url,
            headers={"X-Dune-API-Key": self._key, "accept": "application/json"},
            timeout=self._s.timeout_seconds,
            user_agent=self._s.user_agent,
        )
        meta = ProbeMeta(
            http_status=resp.status,
            latency_ms=resp.latency_ms,
            bytes=resp.bytes,
            rate_limit_remaining=resp.rate_limit_remaining,
        )
        meta.extra["query_id"] = query_id
        if used_fallback:
            meta.extra["used_public_sample"] = True
            meta.extra["configured_value"] = self._query_id
        if not resp.ok:
            return self.fail(resp.error or "request_failed", meta=meta)

        records = _flatten_rows(resp.json, limit=self._s.max_records_per_probe)
        if not records:
            return self.fail("no_rows_in_result", meta=meta)
        return self.ok(records, raw=resp.json, meta=meta)


def _flatten_rows(payload: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result") or {}
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            out.append({str(k): v for k, v in row.items()})
    return out
