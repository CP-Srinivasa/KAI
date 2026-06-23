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

from app.execution.phantom_filter import is_phantom_close

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
    # 2026-06-01 forensics: single off-market close. ETH long entry ~$2100 closed
    # at exit $3259.9692 (+55%) as "take" on 2026-05-26 20:41, while real ETH in
    # that window traded $1960-$2100 (473 fills). Singleton (the price appears 2x:
    # the sell-fill + its position_closed), NOT a repeating runaway like MATIC.
    # Predates the #98 close-circuit-breaker (live 2026-05-31), which now sanity-
    # rejects this class prospectively. Recorded here for deterministic exclusion
    # incl. the Bayes path; the generic edge_report implausibility guard
    # (|exit/entry-1| > threshold) is the primary class-level defence — this
    # signature is the forensic record.
    _QuarantineSignature(
        symbol="ETH/USDT",
        exit_price=3259.9692,
        reason="eth_off_market_close",
        incident_ref="DS-20260601-EDGE-OUTLIER",
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


def quarantine_reason(close_row: dict[str, object]) -> str | None:
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


def is_quarantined(close_row: dict[str, object]) -> bool:
    """True when the ``position_closed`` row matches a corruption signature."""
    return quarantine_reason(close_row) is not None


def corruption_reason(close_row: dict[str, object]) -> str | None:
    """Unified corruption verdict for read-side edge/PnL aggregators.

    Layers the two defences so EVERY edge path excludes the SAME set of corrupt
    closes (2026-06-23 edge-epoch forensic — read aggregators that used only the
    generic phantom guard leaked the ETH off-market signature into realized PnL):

      1. the exact forensic ``quarantine_reason`` signatures (deterministic;
         catches known incidents that sit *under* the generic cap, e.g. the ETH
         off-market close at +55%);
      2. the generic ``is_phantom_close`` return-magnitude guard (catches *new*,
         not-yet-signatured price-source disagreements, e.g. MATIC at +364%).

    Returns the reason string (signature reason, else ``"phantom_implied_return"``)
    or ``None`` when the close is trustworthy. Conservative: a row with no usable
    prices and no signature is never dropped. Does NOT change the Bayes path,
    which intentionally uses only the exact signatures via ``is_quarantined``.
    """
    sig = quarantine_reason(close_row)
    if sig is not None:
        return sig
    if is_phantom_close(
        close_row.get("entry_price"),
        close_row.get("exit_price"),
        close_row.get("position_side"),
    ):
        return "phantom_implied_return"
    return None


def is_corrupt_close(close_row: dict[str, object]) -> bool:
    """True when a close is corrupt by EITHER defence (signature or phantom guard)."""
    return corruption_reason(close_row) is not None


__all__ = [
    "QUARANTINE_SIGNATURES",
    "corruption_reason",
    "is_corrupt_close",
    "is_quarantined",
    "quarantine_reason",
]
