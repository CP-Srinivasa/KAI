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

# NOTE: `is_phantom_close` is imported LAZILY inside `corruption_reason()`, not at
# module top, to break a circular import (introduced by #389): app.execution.__init__
# eagerly imports portfolio_read, which does `from app.learning.bayes_quarantine import
# is_corrupt_close`. A top-level `from app.execution.phantom_filter import …` here would
# re-enter app.execution while THIS module is still mid-initialization → ImportError on
# any bayes_quarantine-first import (e.g. scripts/bayes_posterior_recalc.py). Keep it lazy.

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
    # DS-20260818-MOCK-EXIT: the generic form of the two hand-transcribed ETH
    # signatures above — a close booked against MockMarketDataAdapter's synthetic
    # curve. Exact (bit-identical reconstruction incl. fill slippage), not a
    # magnitude heuristic, so it belongs with the signatures and not with the
    # phantom guard: it catches the small ones too (a mock BTC close came out at
    # +2.8%, far under any implausibility cap). Lazy import mirrors the note at
    # module top — keep the import graph of this module minimal.
    from app.market_data.mock_price_forensics import match_mock_price

    # Bewusst OHNE ``MockPriceMatch.discriminating``-Filter — anders als TL-002.
    # Der Waechter sucht Unbekanntes und muss deshalb still bleiben, wo die Kurve
    # nichts unterscheidet (Symbole ohne eigenen Basispreis, Kurvenabstand 0,0100).
    # Die Quarantaene urteilt dagegen ueber einen BEREITS forensisch belegten
    # Bestand: fuer die 12 Closes vom 11./12.08. steht der Tick-Kontext fest (in
    # jedem betroffenen Tick war JEDE Schliessung mock-erzeugt, 0 gemischte bei
    # 514 Close-Sekunden). Setzt jemand den Filter hier ebenfalls, fallen die
    # Default-Basis-Symbole aus der Quarantaene und der Scheingewinn kehrt ins
    # Buch zurueck. Siehe docs/audit/phantom_close_artifact_register.md §5b.
    if match_mock_price(symbol, exit_price) is not None:
        return "mock_synthetic_exit_price"
    return None


def is_quarantined(close_row: dict[str, object]) -> bool:
    """True when the ``position_closed`` row matches a corruption signature."""
    return quarantine_reason(close_row) is not None


def corruption_reason(close_row: dict[str, object]) -> str | None:
    """Grund, warum ein Close nicht in Kennzahlen einfliessen darf — sonst None.

    Duenner Adapter auf ``app.execution.close_classification.classify_close``.
    Die Schichtung liegt dort; hier steht nur, wie die bestehenden Lese-Pfade das
    Urteil sehen:

      * ``QUARANTINE``            -> Grund (bekanntes Artefakt / Remediation)
      * ``REQUIRES_VERIFICATION`` -> Grund ``extreme_move_requires_verification``
      * ``VERIFIED_MARKET_PLAUSIBLE`` / ``CLEAN`` -> None

    Zum Uebergang bei ``REQUIRES_VERIFICATION``: der Cap behauptet seit 2026-08-19
    NICHT mehr, ein Treffer sei korrupt (er fing zuletzt null Artefakte und drei
    echte Trades). Bis der automatische Close-Verifier steht, halten die
    Aggregatoren solche Closes aber weiterhin aus den Kennzahlen heraus — sie sind
    ungeprueft, und ungeprueft in eine Buch-Zahl zu geben waere die schlechtere
    Richtung. Neu ist das eigene Label: die Pruef-Schuld ist damit messbar, statt
    als "Artefakt" mitgezaehlt zu werden.
    """
    from app.execution.close_classification import classify_close

    result = classify_close(close_row)
    if result.verdict.value in ("quarantine", "requires_verification"):
        return result.reason
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
