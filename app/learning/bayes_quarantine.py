"""Outcome quarantine for the Bayes posterior recalc.

Some ``position_closed`` events in ``paper_execution_audit.jsonl`` are
*corrupt* and must not feed the Bayes hit-rate posterior, even though they
are valid audit rows that we deliberately keep (append-only audit integrity:
we quarantine, never delete — memory ``paper_audit_pnl_field_semantics`` and
the DS-20260529-V1 forensic).

Incident DS-20260529-V1 (MATIC stale-exit runaway, 2026-05-28 17:42–20:43Z):
the close path repeatedly closed MATIC against a *frozen* exit price of
``0.408545625`` while the position kept growing (qty 1.7k → 104k), booking
+73.5k of fake profit across 9 closes (later root-caused + fixed by #98
cross-provider-sanity + close-circuit-breaker). Those 9 closes show up in the
posterior as ``tradingloop::MATIC/USDT::long`` with 10/10 hits, posterior
0.857 — a phantom that would make MATIC look like the best long in the book
and poison any SHADOW_ONLY flip decision.

We quarantine on the *deterministic corruption signature* (symbol + the exact
frozen stale exit price), not on transcribed fill_ids:
  - it is exact: only those 9 records match (verified: MATIC is the only
    symbol with a repeated identical exit price, and it repeats exactly 9×),
  - it preserves the *legitimate* earlier MATIC close (2026-05-06, exit
    ~0.0989) which is NOT matched,
  - it carries no risk of mis-transcribing 9 opaque ids.

A quarantined close is skipped entirely (it does not even count as
``inconclusive``) — it is treated as if the corrupt outcome was never
observed, which is the correct Bayesian handling of a known-bad measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

# Float tolerance for matching the frozen exit price. The stale price is a
# fixed float constant; 1e-9 is far tighter than any legitimate price spacing
# yet absorbs binary round-trip noise.
_EXIT_PRICE_TOL: float = 1e-9


@dataclass(frozen=True)
class _QuarantineSignature:
    """A (symbol, exit_price) corruption signature with provenance."""

    symbol: str
    exit_price: float
    reason: str
    incident_ref: str


# Quarantined outcome signatures. Extend this list (with an incident_ref) when
# a new corruption class is forensically confirmed — never silently.
QUARANTINE_SIGNATURES: tuple[_QuarantineSignature, ...] = (
    _QuarantineSignature(
        symbol="MATIC/USDT",
        exit_price=0.408545625,
        reason="matic_stale_exit_runaway",
        incident_ref="DS-20260529-V1",
    ),
)


def _isfinite_float(x: object) -> float | None:
    try:
        f = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def quarantine_reason(close_row: dict) -> str | None:
    """Return the quarantine reason if a ``position_closed`` row is corrupt.

    Matches the row's ``symbol`` + ``exit_price`` against the known corruption
    signatures. Returns the ``reason`` string when quarantined, else ``None``.
    Rows without a usable exit price are never quarantined (no signature to
    match) — they fall through to normal classification.
    """
    symbol = str(close_row.get("symbol", "")).strip()
    if not symbol:
        return None
    exit_price = _isfinite_float(close_row.get("exit_price"))
    if exit_price is None:
        return None
    for sig in QUARANTINE_SIGNATURES:
        if sig.symbol == symbol and abs(exit_price - sig.exit_price) <= _EXIT_PRICE_TOL:
            return sig.reason
    return None


def is_quarantined(close_row: dict) -> bool:
    """True when the ``position_closed`` row matches a corruption signature."""
    return quarantine_reason(close_row) is not None


__all__ = [
    "QUARANTINE_SIGNATURES",
    "is_quarantined",
    "quarantine_reason",
]
