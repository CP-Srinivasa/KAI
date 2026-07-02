"""UC-5 — sovereign fee/mempool time series (deterministic facts, no forecast).

Aggregates KAI's OWN L1 fee-shadow stream (``artifacts/onchain_fee_shadow.jsonl``)
into a clean, verifiable series for the paid ``/oracle/fee-series`` endpoint.
DOCTRINE: facts only — raw observations + deterministic min/median/max — NEVER an
"expected fee" prediction. Read-only, no capital path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def build_fee_series(records: Sequence[dict[str, Any]], *, limit: int = 200) -> dict[str, Any]:
    """Aggregate raw fee-shadow records into a facts-only series + summary.

    Keeps the last ``limit`` records verbatim (raw facts); the summary stats exclude
    ``None`` fee estimates (best-effort) but every record stays in ``series``. An
    empty input yields honest zero/``None`` — never a fabricated statistic.
    """
    rows = [r for r in records if isinstance(r, dict)]
    if limit > 0:
        rows = rows[-limit:]
    fees = [float(r["fee_sat_vb"]) for r in rows if r.get("fee_sat_vb") is not None]
    series = [
        {
            "ts": r.get("ts"),
            "blocks": r.get("blocks"),
            "fee_sat_vb": r.get("fee_sat_vb"),
            "mempool_tx": r.get("mempool_tx"),
        }
        for r in rows
    ]
    return {
        "source": "kai_sovereign_bitcoind",
        "count": len(rows),
        "series": series,
        "fee_sat_vb_min": min(fees) if fees else None,
        "fee_sat_vb_median": _median(fees),
        "fee_sat_vb_max": max(fees) if fees else None,
        "oldest_ts": rows[0].get("ts") if rows else None,
        "newest_ts": rows[-1].get("ts") if rows else None,
    }


__all__ = ["build_fee_series"]
