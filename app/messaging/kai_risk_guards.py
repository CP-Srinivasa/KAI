"""KAI Risk Guards — Python pendant for web/src/kai/riskGuards.ts.

Spec: docs/kai_persona/technical_ui_pack_v3_2.md §5
       docs/kai_persona/final_execution_prompt_v3_4.md §12

Backend gate for Livetrade emission. Critical Risk, low data quality, missing
stop-loss or data basis, and out-of-range confidence all block the trade
BEFORE the Telegram renderer can emit a Livetrade card.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class KaiSignalForGuards:
    """Minimal view of a signal sufficient to validate Livetrade safety."""

    asset: str
    mode: str
    direction: str
    confidence: float
    risk: str
    stop_loss: str
    data_basis: tuple[str, ...]
    data_quality: str


@dataclass(frozen=True)
class KaiGuardResult:
    allowed: bool
    reasons: tuple[str, ...]


_VALID_DIRECTIONS = ("LONG", "SHORT", "NEUTRAL", "NO_TRADE")


def validate_signal_for_livetrade(signal: KaiSignalForGuards) -> KaiGuardResult:
    reasons: list[str] = []
    if signal.mode != "LIVETRADE":
        return KaiGuardResult(allowed=True, reasons=())

    if signal.risk == "CRITICAL":
        reasons.append("Critical Risk blockiert Livetrading.")

    if signal.data_quality in ("LOW", "UNKNOWN"):
        reasons.append("Datenqualitaet reicht fuer Livetrading nicht aus.")

    sl_norm = (signal.stop_loss or "").lower()
    if (
        not signal.stop_loss
        or "wartet" in sl_norm
        or "not confirmed" in sl_norm
        or "waiting" in sl_norm
    ):
        reasons.append("Stop-Loss-Logik fehlt oder ist nicht bestaetigt.")

    if not signal.data_basis:
        reasons.append("Datenbasis fehlt.")

    if signal.confidence != signal.confidence:  # NaN check
        reasons.append("Confidence ist NaN.")
    elif signal.confidence < 0 or signal.confidence > 100:
        reasons.append("Confidence liegt ausserhalb des erlaubten Bereichs 0-100.")

    return KaiGuardResult(allowed=not reasons, reasons=tuple(reasons))


def validate_signal_invariants(signal: KaiSignalForGuards) -> KaiGuardResult:
    reasons: list[str] = []
    if not signal.asset or "/" not in signal.asset:
        reasons.append("Asset fehlt oder hat kein Trading-Pair-Format.")
    if signal.confidence != signal.confidence:
        reasons.append("Confidence ist NaN.")
    elif signal.confidence < 0 or signal.confidence > 100:
        reasons.append("Confidence ausserhalb 0-100.")
    if signal.direction not in _VALID_DIRECTIONS:
        reasons.append("Direction ist kein gueltiger Wert.")
    return KaiGuardResult(allowed=not reasons, reasons=tuple(reasons))


def all_violations(*results: KaiGuardResult) -> tuple[str, ...]:
    """Concatenate violations across multiple guard results."""
    out: list[str] = []
    for r in results:
        out.extend(r.reasons)
    return tuple(out)


def first_failing_reason(reasons: Iterable[str]) -> str | None:
    for r in reasons:
        if r:
            return r
    return None
