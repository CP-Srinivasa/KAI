"""Maturity tracking for open out-of-sample pre-registrations.

"Re-evaluate when n>=300" must not live in an operator's memory. Each open
out-of-sample hypothesis gets a SPEC here (how to count its cohort from the
document store) and ``compute_maturity`` reports n vs target — the weekly timer
surfaces DUE claims via journal/artifact, read-only.

The count is a deliberate UPPER-BOUND PROXY: it counts qualifying directional
documents; the eval itself drops some events (no OHLCV series, entry-lag gaps),
so a DUE signal means "run the eval now", never "the claim passed".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Registered out-of-sample windows start at the claim's registration day — these
# constants ARE part of the doctrine (auditable against the prereg ledger).
MATURITY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "directional_news_hedged_1d_drift",
        "since_utc": "2026-07-02",
        "sources": None,  # all sources
        "exclude_first_ticker": "BTC/USDT",  # hedged construction skips BTC events
        "n_target": 300,
    },
    {
        "name": "directional_news_3d_theblock_newsbtc",
        "since_utc": "2026-07-01",
        "sources": ("theblock", "newsbtc"),
        "exclude_first_ticker": None,
        "n_target": 100,  # per source
    },
)

_COUNT_SQL = """
SELECT COALESCE(source_name, 'unknown') AS src, COUNT(*) AS n
FROM canonical_documents
WHERE sentiment_label IN ('bullish', 'bearish')
  AND tickers IS NOT NULL
  AND json_array_length(tickers) > 0
  AND published_at >= :since
  AND (:exclude_ticker IS NULL OR json_extract(tickers, '$[0]') != :exclude_ticker)
GROUP BY source_name
"""


async def compute_maturity(
    session: AsyncSession,
    *,
    specs: tuple[dict[str, Any], ...] = MATURITY_SPECS,
) -> list[dict[str, Any]]:
    """Count each spec's out-of-sample cohort; ``due`` when target is reached."""
    out: list[dict[str, Any]] = []
    for spec in specs:
        rows = (
            await session.execute(
                text(_COUNT_SQL),
                {
                    "since": spec["since_utc"],
                    "exclude_ticker": spec["exclude_first_ticker"],
                },
            )
        ).all()
        by_source = {str(r.src): int(r.n) for r in rows}
        sources = spec["sources"]
        if sources is None:
            n = sum(by_source.values())
            due = n >= int(spec["n_target"])
            detail: dict[str, int] = {"all": n}
        else:
            detail = {s: by_source.get(s, 0) for s in sources}
            n = sum(detail.values())
            due = all(v >= int(spec["n_target"]) for v in detail.values())
        out.append(
            {
                "name": spec["name"],
                "since_utc": spec["since_utc"],
                "n_target": spec["n_target"],
                "n_proxy": n,
                "per_source": detail,
                "due": due,
            }
        )
    return out


__all__ = ["MATURITY_SPECS", "compute_maturity"]
