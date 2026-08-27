"""Correction notices attached to a paper-book epoch.

A booked result is research substance: the append-only audit is NEVER rewritten
(memory ``paper_audit_pnl_field_semantics`` — we quarantine, never delete). But
when a booked figure is later proven to rest on fabricated inputs, citing it bare
is the error. This module is the canonical place where such a proof is recorded
against the epoch it affects, so every surface that quotes the epoch's PnL can
carry the caveat with it instead of each caller remembering.

Operator decision 2026-08-18: epoch ``paper_v2_attested`` gets such a notice.

The notice is deliberately split:
  * ``summary``/``detail`` state the *invariant* facts (which closes, what root
    cause, which incident ref) — those do not change;
  * ``measured_*`` fields are a DATED snapshot, because the epoch keeps
    accumulating closes and any hardcoded total would silently rot. Re-measure
    with ``verify_command`` before citing a number (memory
    ``feedback_audit_findings_need_live_remeasure``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EpochCorrectionNotice:
    """A proven defect in how an epoch's numbers came to be."""

    epoch_id: str
    incident_ref: str
    recorded_at_utc: str
    summary: str
    detail: str
    verify_command: str
    measured_basis: str
    measured_at_utc: str
    measured_closes: int
    measured_booked_usd: float
    measured_contaminated_closes: int
    measured_contaminated_usd: float
    # Identitaet des Quellzustands, ueber den gemessen wurde. Ohne sie laesst
    # sich NICHT beweisen, dass eine spaetere Ansicht dieselbe Population
    # zeigt: die blosse Close-ANZAHL kann zufaellig wieder uebereinstimmen,
    # waehrend darunter andere Ereignisse liegen (Reset, Requarantaene,
    # Reparatur einer Zeile). Der Vermerk vom 2026-08-18 wurde ohne Digest
    # aufgenommen und traegt darum ``None`` — jede Lese-Seite muss seine Zahlen
    # dann als historisch kennzeichnen, statt Deckung zu unterstellen.
    # Wer den naechsten Vermerk aufnimmt, setzt ihn.
    measured_source_sha256: str | None = None

    @property
    def measured_corrected_usd(self) -> float:
        """Booked minus the proven-synthetic part, as of ``measured_at_utc``."""
        return self.measured_booked_usd - self.measured_contaminated_usd

    @property
    def flips_sign(self) -> bool:
        """True when the correction reverses the sign of the booked result."""
        return (self.measured_booked_usd > 0) is not (self.measured_corrected_usd > 0)

    def as_dict(self) -> dict[str, object]:
        return {
            "epoch_id": self.epoch_id,
            "incident_ref": self.incident_ref,
            "recorded_at_utc": self.recorded_at_utc,
            "summary": self.summary,
            "detail": self.detail,
            "verify_command": self.verify_command,
            "measured_basis": self.measured_basis,
            "measured_at_utc": self.measured_at_utc,
            "measured_closes": self.measured_closes,
            "measured_booked_usd": round(self.measured_booked_usd, 2),
            "measured_contaminated_closes": self.measured_contaminated_closes,
            "measured_contaminated_usd": round(self.measured_contaminated_usd, 2),
            "measured_corrected_usd": round(self.measured_corrected_usd, 2),
            "measured_source_sha256": self.measured_source_sha256,
            "flips_sign": self.flips_sign,
        }


_PAPER_V2_DETAIL = (
    "Root cause (proven, not inferred): MockMarketDataAdapter is the last link of "
    "the live `fallback` provider chain and returns is_stale=False with "
    "freshness_seconds=0.0 unconditionally. On a tick where every real venue "
    "failed to resolve, FallbackMarketDataAdapter.get_market_data_point fell "
    "through to that synthetic point (`chosen = fresh_real or real or fresh or "
    "resolved`), the position monitor's stale-guard passed it, and every position "
    "whose SL/TP the fabricated price crossed was closed against it. "
    "Both prices reproduce bit-exactly from the mock curve, float artefacts "
    "included: 3225.6863500000004 = mock(ETH/USDT, phase 101) * (1 - 0.0005), and "
    "the older DS-20260601 signature 3259.9692 = mock(ETH/USDT, phase 297) * "
    "(1 - 0.0005). Across the whole audit the class covers 10 closes in 6 ticks, "
    "and in each of those ticks EVERY close was mock-derived (0 mixed ticks out of "
    "514 close-seconds) — the signature of one monitor pass on a fully synthetic "
    "price map. A 20% implausibility cap cannot separate the class: the same ticks "
    "produced a +2.8% BTC close. The audit is unchanged; exclusion happens on the "
    "read side via app.learning.bayes_quarantine.corruption_reason "
    "(`mock_synthetic_exit_price`), and the source was closed by tagging "
    "mock-only points stale so neither entry nor exit can use them."
)

EPOCH_CORRECTION_NOTICES: dict[str, EpochCorrectionNotice] = {
    "paper_v2_attested": EpochCorrectionNotice(
        epoch_id="paper_v2_attested",
        incident_ref="DS-20260818-MOCK-EXIT",
        recorded_at_utc="2026-08-18T00:00:00+00:00",
        summary=(
            "Four closes in this epoch were booked against synthetic mock prices "
            "(2026-08-11 23:09:58 and 2026-08-12 23:06:34, two each). Net +1701.54 USD "
            "— not all of it phantom profit: one was a phantom LOSS of -585.55 USD, so "
            "the contamination distorts in both directions. The epoch's booked PnL is "
            "not citeable without excluding them; the correction reverses its sign. "
            "Only 3 of the 4 exceed the 20% phantom cap — the fourth (+2.8% BTC) is "
            "reachable by the mock signature alone."
        ),
        detail=_PAPER_V2_DETAIL,
        verify_command=(
            "python -m app.cli.main trading canonical-edge --json  # and: "
            "app.learning.bayes_quarantine.corruption_reason over "
            "artifacts/paper_execution_audit.jsonl"
        ),
        measured_basis=(
            "sum(trade_pnl_usd) over position_closed + position_partial_closed since "
            "the epoch reset — the same population as docs/audit/"
            "phantom_close_artifact_register.md. NOT net of the entry fee "
            "(trade_pnl_usd carries only the close fee), so this series is not "
            "comparable with the fee-corrected one; the sign flip holds in both."
        ),
        measured_at_utc="2026-08-18T17:30:00+00:00",
        measured_closes=215,
        measured_booked_usd=771.05,
        measured_contaminated_closes=4,
        measured_contaminated_usd=1701.54,
    ),
}


def epoch_correction_notice(epoch_id: str | None) -> EpochCorrectionNotice | None:
    """The correction notice for ``epoch_id``, or None when the epoch is clean."""
    if not epoch_id:
        return None
    return EPOCH_CORRECTION_NOTICES.get(str(epoch_id).strip())


def epoch_correction_payload(epoch_id: str | None) -> dict[str, object] | None:
    """JSON payload of the epoch's correction notice, or None when it is clean.

    Read surfaces carry the proof WITH the number instead of each caller
    remembering that a given epoch is qualified (operator decision 2026-08-18).
    """
    notice = epoch_correction_notice(epoch_id)
    return notice.as_dict() if notice is not None else None


__all__ = [
    "EPOCH_CORRECTION_NOTICES",
    "EpochCorrectionNotice",
    "epoch_correction_notice",
    "epoch_correction_payload",
]
