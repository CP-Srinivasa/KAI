"""Dummy probe — durchstich + test fixture.

Returns deterministic sample records without any network access, so the
framework (runner → capture → report) can be exercised end-to-end without keys,
network, or grey-area scraping. Always eligible (independent of the global gate).
"""

from __future__ import annotations

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta


class DummyProbe(ExplorationProbe):
    source_name = "dummy"
    access_mode = "api"
    requires_key = False

    async def probe(self) -> ExplorationResult:
        records = [
            {"symbol": "BTC", "metric": "sample_value", "value": 1.0, "unit": "x"},
            {"symbol": "ETH", "metric": "sample_value", "value": 2.0, "unit": "x"},
            # second record intentionally omits "unit" to exercise coverage math
            {"symbol": "SOL", "metric": "sample_value", "value": 3.0},
        ]
        return self.ok(
            records,
            raw={"note": "dummy probe — no network", "count": len(records)},
            meta=ProbeMeta(http_status=200, latency_ms=0.0, bytes=0),
        )
