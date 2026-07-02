"""momentum_cohort_outcomes — G3 shadow→eval bridge.

Extracts resolved ``momentum_universe``-cohort closed trades from the paper
execution audit into ``{symbol, entry_ts, net_bps}`` rows that
``scripts/evaluate_momentum_evidence.py`` point-in-time-joins against the momentum
evidence shadow log. Reuses the edge_report cost SSOT (``compute_trade_edge`` /
``CostModel``) — no second fee formula. Corrupt/quarantined closes are dropped by
``parse_closed_trades_with_exclusions`` (the same integrity guard the edge-report
uses). The close timestamp is used as ``entry_ts``: it is strictly after the
shadow measurement (written at signal time), so the PIT-join pairs each
measurement with the close it led to (no look-ahead).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

DEFAULT_COHORT = "momentum_universe"


def extract_cohort_outcomes(
    events: Iterable[dict[str, Any]],
    *,
    cohort: str = DEFAULT_COHORT,
    venue: str = "paper",
) -> list[dict[str, Any]]:
    """Return ``[{symbol, entry_ts, net_bps}, ...]`` for the cohort's resolved trades."""
    from app.execution.cost_model import CostModel
    from app.observability.edge_report import (
        compute_trade_edge,
        parse_closed_trades_with_exclusions,
    )

    closed, _excluded = parse_closed_trades_with_exclusions(list(events))
    cost_model = CostModel()
    doc_prefix = f"{cohort}_"
    out: list[dict[str, Any]] = []
    for trade in closed:
        # Match the cohort by its coarse signal_source OR — for trades closed
        # before the forward attribution fix (2026-06-29), when the cohort tag was
        # mis-bucketed as ``autonomous_generator`` — by the document_id prefix the
        # feeder always stamps (``momentum_universe_<SYM>``). Scoped to
        # ``<cohort>_`` so a different cohort's doc-id never leaks in.
        if trade.signal_source != cohort and not trade.document_id.startswith(doc_prefix):
            continue
        edge = compute_trade_edge(trade, cost_model, venue=venue)
        out.append(
            {
                "symbol": trade.symbol,
                "entry_ts": trade.timestamp_utc,
                "net_bps": round(edge.net_bps, 4),
            }
        )
    return out


__all__ = ["DEFAULT_COHORT", "extract_cohort_outcomes"]
