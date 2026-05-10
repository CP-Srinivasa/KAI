"""Normalized cross-source trading-signal model + Lifecycle-State-Machine + Validator.

Spec: docs/architecture/signal_to_execution_gap_analysis_20260510.md (Aufgabenpakete 3 + 5).

Zweck
-----
Bridges ``ParsedSignal`` (telegram_channel_parser), Dashboard-Paste-Signale
und TradingView-Events zu einer einheitlichen ``NormalizedTradeSignal``
die der Execution-Layer (Paper + Live) konsumiert. Macht margin / leverage /
risk_allocation zu **expliziten Pflicht-Feldern** statt versteckter
Konstanten — ein channel-stated ``Leverage: 10x`` darf nicht mehr im
Bridge-Pfad verschluckt werden.

Plus die 16-Status-Lifecycle-State-Machine aus dem Operator-Auftrag
(2026-05-10) mit Pflicht-Transition-Matrix — jeder Übergang ist
auditierbar und reproduzierbar testbar.

Vertrag
-------
1. **Status ist Wahrheit.** Die ``status_history`` ist die kanonische
   Audit-Spur des Lifecycle. Kein Sub-System darf den Status direkt
   setzen; nur ``transition_to()`` mit Matrix-Validierung.
2. **Immutability.** ``NormalizedTradeSignal`` ist ``frozen=True``.
   Transitions liefern eine NEUE Instanz, der Vorgänger bleibt im
   Audit-Stream sichtbar.
3. **Sizing-Pflicht.** Mindestens eines von ``risk_allocation_pct``,
   ``margin_size_usd`` oder ``leverage`` muss gesetzt sein. Der Validator
   weist andernfalls mit ``REJECTED_INVALID_SIGNAL`` zurück.
4. **Plausibility-Pflicht.** SL und Targets müssen geometrisch auf der
   richtigen Seite des Entry liegen (LONG: SL<entry<targets;
   SHORT: targets<entry<SL).

Status 2026-05-10
-----------------
Sprint 1 aus Operator-Auftrag (Aufgabenpaket 1 → erst A). Modul ist neu
aufgesetzt; Konsumenten (Bridge / paper_engine / Telegram-Renderer)
werden in Sprint B (Top-3-Bugfixes) und Sprint 2 (Pre-Sprint A
Lifecycle-Wiring) angeschlossen.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Final, Literal

logger = logging.getLogger(__name__)


# ─── Type-Aliases ────────────────────────────────────────────────────────────


Side = Literal["buy", "sell"]
Direction = Literal["long", "short"]
EntryType = Literal["limit", "market", "range", "trigger"]
MarginMode = Literal["isolated", "cross"]
OrderIntent = Literal["OPEN_POSITION", "CLOSE_POSITION", "MODIFY", "CANCEL"]


# ─── Lifecycle-State-Machine ─────────────────────────────────────────────────


class SignalStatus(str, Enum):
    """16 explicit Lifecycle-States per Operator-Auftrag 2026-05-10.

    Die Reihenfolge in dieser Enum entspricht der typischen Vorwärts-
    Progression — Tests dürfen das aber NICHT als Ordnungsrelation
    annehmen. Erlaubte Transitions sind ausschließlich in
    ``LIFECYCLE_TRANSITIONS`` definiert.
    """

    # Ingest
    RECEIVED = "received"
    PARSED = "parsed"
    VALIDATED = "validated"
    REJECTED_INVALID_SIGNAL = "rejected_invalid_signal"

    # Entry-Phase
    WAITING_FOR_ENTRY = "waiting_for_entry"
    ENTRY_TRIGGERED = "entry_triggered"

    # Order-Phase
    ORDER_BUILDING = "order_building"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_ACCEPTED = "order_accepted"

    # Position-Phase
    POSITION_OPEN = "position_open"
    PARTIAL_TP_HIT = "partial_tp_hit"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"

    # Termination
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Terminal states accept no further transitions.
TERMINAL_STATES: Final[frozenset[SignalStatus]] = frozenset(
    {
        SignalStatus.REJECTED_INVALID_SIGNAL,
        SignalStatus.TP_HIT,
        SignalStatus.SL_HIT,
        SignalStatus.EXPIRED,
        SignalStatus.CANCELLED,
        SignalStatus.FAILED,
    }
)


# Transition-Matrix — Wahrheit für jeden Übergang. Jede Transition außerhalb
# dieser Matrix wirft ``IllegalLifecycleTransition``. Die Matrix ist Pflicht-
# Vertrag für Pre-Sprint A (Lifecycle-State-Machine in paper_engine).
LIFECYCLE_TRANSITIONS: Final[dict[SignalStatus, frozenset[SignalStatus]]] = {
    SignalStatus.RECEIVED: frozenset(
        {SignalStatus.PARSED, SignalStatus.FAILED}
    ),
    SignalStatus.PARSED: frozenset(
        {
            SignalStatus.VALIDATED,
            SignalStatus.REJECTED_INVALID_SIGNAL,
            SignalStatus.FAILED,
        }
    ),
    SignalStatus.VALIDATED: frozenset(
        {
            SignalStatus.WAITING_FOR_ENTRY,
            SignalStatus.ENTRY_TRIGGERED,  # market-orders direkt triggern
            SignalStatus.CANCELLED,
            SignalStatus.FAILED,
        }
    ),
    SignalStatus.WAITING_FOR_ENTRY: frozenset(
        {
            SignalStatus.ENTRY_TRIGGERED,
            SignalStatus.EXPIRED,
            SignalStatus.CANCELLED,
            SignalStatus.FAILED,
        }
    ),
    SignalStatus.ENTRY_TRIGGERED: frozenset(
        {SignalStatus.ORDER_BUILDING, SignalStatus.FAILED, SignalStatus.CANCELLED}
    ),
    SignalStatus.ORDER_BUILDING: frozenset(
        {
            SignalStatus.ORDER_SUBMITTED,
            SignalStatus.FAILED,
            SignalStatus.CANCELLED,
        }
    ),
    SignalStatus.ORDER_SUBMITTED: frozenset(
        {
            SignalStatus.ORDER_ACCEPTED,
            SignalStatus.REJECTED_INVALID_SIGNAL,  # Exchange-Reject
            SignalStatus.FAILED,
            SignalStatus.CANCELLED,
        }
    ),
    SignalStatus.ORDER_ACCEPTED: frozenset(
        {
            SignalStatus.POSITION_OPEN,
            SignalStatus.SL_HIT,  # SL feuert vor Position-Open (Server-Side-OCO)
            SignalStatus.FAILED,
            SignalStatus.CANCELLED,
        }
    ),
    SignalStatus.POSITION_OPEN: frozenset(
        {
            SignalStatus.PARTIAL_TP_HIT,
            SignalStatus.TP_HIT,
            SignalStatus.SL_HIT,
            SignalStatus.CANCELLED,  # manueller Close
            SignalStatus.FAILED,
        }
    ),
    SignalStatus.PARTIAL_TP_HIT: frozenset(
        {
            SignalStatus.PARTIAL_TP_HIT,  # weitere Teil-TPs
            SignalStatus.TP_HIT,
            SignalStatus.SL_HIT,
            SignalStatus.CANCELLED,
        }
    ),
    # Terminale Zustände akzeptieren keine weiteren Transitions
    SignalStatus.REJECTED_INVALID_SIGNAL: frozenset(),
    SignalStatus.TP_HIT: frozenset(),
    SignalStatus.SL_HIT: frozenset(),
    SignalStatus.EXPIRED: frozenset(),
    SignalStatus.CANCELLED: frozenset(),
    SignalStatus.FAILED: frozenset(),
}


class IllegalLifecycleTransition(ValueError):
    """Wird geworfen wenn eine Status-Transition nicht in der Matrix steht."""


# ─── Datenmodelle ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StatusTransition:
    """Audit-Record einer einzelnen State-Transition."""

    from_status: SignalStatus
    to_status: SignalStatus
    timestamp_utc: str
    actor: str  # "TelegramParser" | "SignalValidator" | "EntryWatcher" | "PaperEngine" | "Operator" | "RiskEngine"
    reason: str  # human-readable, audit-grade


@dataclass(frozen=True)
class NormalizedTradeSignal:
    """Cross-source normalized signal.

    Eingang: ParsedSignal (Telegram), Dashboard-Paste, TradingView-Event.
    Ausgang: konsumiert von ``envelope_to_paper_bridge``, ``paper_engine``
    und (Phase-0) ``live_engine``.
    """

    # Identity
    correlation_id: str  # SIG-TGCH-... | SIG-DASH-... | SIG-TV-...
    source: str  # "telegram_premium_channel" | "dashboard" | "tradingview" | ...

    # Symbol + Direction
    symbol: str  # internal canonical, z.B. "BTCUSDT"
    display_symbol: str  # human-readable, z.B. "BTC/USDT"
    side: Side
    direction: Direction

    # Order-Intent (Operator-Auftrag Aufgabenpaket 3)
    order_intent: OrderIntent

    # Entry — entweder entry_value XOR (entry_min + entry_max)
    entry_type: EntryType
    entry_value: float | None
    entry_min: float | None
    entry_max: float | None

    # Risk-Anchors (Validator-Pflicht: SL non-None und mind. 1 target)
    stop_loss: float
    targets: tuple[float, ...]

    # Sizing — mind. eines der drei muss gesetzt sein (Validator-Pflicht)
    leverage: int | None
    margin_mode: MarginMode | None
    margin_size_usd: float | None
    risk_allocation_pct: float | None  # 0.05 = 5% des Account-Equity

    # Lifecycle
    status: SignalStatus

    # Audit
    parsed_at_utc: str
    raw_text: str = ""
    audit_reason: str | None = None
    status_history: tuple[StatusTransition, ...] = ()

    # ----- Lifecycle -----------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    def transition_to(
        self,
        new_status: SignalStatus,
        *,
        actor: str,
        reason: str,
    ) -> NormalizedTradeSignal:
        """Return new instance mit Transition-validiertem Status-Wechsel.

        Wirft ``IllegalLifecycleTransition`` wenn die Transition nicht in
        ``LIFECYCLE_TRANSITIONS`` steht. Die ``status_history`` wird um
        einen ``StatusTransition``-Record erweitert. Bei Übergang in
        einen Terminal-State wird ``audit_reason`` mitgesetzt.
        """
        allowed = LIFECYCLE_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise IllegalLifecycleTransition(
                f"transition not allowed: "
                f"{self.status.value} → {new_status.value} "
                f"(actor={actor}, reason={reason!r}, "
                f"correlation_id={self.correlation_id})"
            )
        ts = _now_utc()
        transition = StatusTransition(
            from_status=self.status,
            to_status=new_status,
            timestamp_utc=ts,
            actor=actor,
            reason=reason,
        )
        new_history = (*self.status_history, transition)
        new_audit_reason = (
            reason if new_status in TERMINAL_STATES else self.audit_reason
        )
        return dataclasses.replace(
            self,
            status=new_status,
            status_history=new_history,
            audit_reason=new_audit_reason,
        )

    # ----- Convenience ---------------------------------------------------

    @property
    def primary_entry(self) -> float | None:
        """Single entry price for downstream consumers (Bridge, Risk-Engine).

        Range entries return midpoint. Trigger entries return the trigger value.
        """
        if self.entry_type == "range" and self.entry_min and self.entry_max:
            return (self.entry_min + self.entry_max) / 2.0
        return self.entry_value

    @property
    def has_range_entry(self) -> bool:
        return (
            self.entry_type == "range"
            and self.entry_min is not None
            and self.entry_max is not None
        )


# ─── Validator (Aufgabenpaket 5) ─────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationResult:
    """Outcome eines Validator-Runs."""

    is_valid: bool
    rejected_reason: str | None = None
    needs_review: bool = False  # edge cases that merit human eyes
    warnings: tuple[str, ...] = ()


def validate(signal: NormalizedTradeSignal) -> ValidationResult:
    """Phase-0 Pflicht-Validierung pro Aufgabenpaket 5.

    Pflicht-Felder
    --------------
    - symbol non-empty
    - side ∈ {"buy", "sell"} (Type-System enforcedt das schon, aber wir prüfen
      defensive falls ein Konstruktor um den Type-Check herumkommt)
    - entry_value XOR (entry_min und entry_max) je nach entry_type
    - stop_loss > 0
    - len(targets) >= 1
    - sizing: at least one of (margin_size_usd, risk_allocation_pct, leverage)

    Plausibility (geometrisch)
    --------------------------
    - LONG: stop_loss < primary_entry < min(targets)
    - SHORT: max(targets) < primary_entry < stop_loss

    Wirft KEINE Exceptions — verpackt jede Verletzung in
    ``ValidationResult(is_valid=False, rejected_reason=...)``. Konsumenten
    nutzen das Ergebnis für ``signal.transition_to(REJECTED_INVALID_SIGNAL,
    reason=result.rejected_reason)``.
    """
    rejections: list[str] = []
    warnings: list[str] = []

    # 1. Symbol
    if not signal.symbol or not signal.symbol.strip():
        rejections.append("symbol_missing")

    # 2. Side
    if signal.side not in {"buy", "sell"}:
        rejections.append(f"side_invalid:{signal.side}")

    # 3. Direction-side consistency
    if (signal.direction == "long" and signal.side != "buy") or (
        signal.direction == "short" and signal.side != "sell"
    ):
        rejections.append(
            f"direction_side_mismatch:direction={signal.direction},side={signal.side}"
        )

    # 4. Entry-Spec
    has_value = signal.entry_value is not None and signal.entry_value > 0
    has_range = (
        signal.entry_min is not None
        and signal.entry_max is not None
        and signal.entry_min > 0
        and signal.entry_max > signal.entry_min
    )
    if signal.entry_type == "market":
        # market-orders dürfen keine entry_value/range haben
        if has_value or has_range:
            warnings.append("market_entry_with_explicit_price")
    elif signal.entry_type == "range":
        if not has_range:
            rejections.append("range_entry_requires_min_and_max")
    elif signal.entry_type in {"limit", "trigger"}:
        if not has_value:
            rejections.append(f"{signal.entry_type}_entry_requires_value")

    # 5. Stop-Loss
    if signal.stop_loss is None or signal.stop_loss <= 0:
        rejections.append("stop_loss_missing_or_invalid")

    # 6. Targets — mind. eins Pflicht
    if not signal.targets or len(signal.targets) < 1:
        rejections.append("targets_missing")
    else:
        for i, t in enumerate(signal.targets):
            if t <= 0:
                rejections.append(f"target_{i}_invalid:{t}")

    # 7. Sizing — mindestens eines der drei (Operator-Auftrag Aufgabenpaket 5)
    has_sizing = (
        (signal.margin_size_usd is not None and signal.margin_size_usd > 0)
        or (
            signal.risk_allocation_pct is not None
            and signal.risk_allocation_pct > 0
        )
        or (signal.leverage is not None and signal.leverage > 0)
    )
    if not has_sizing:
        rejections.append("sizing_missing:need_margin_or_risk_alloc_or_leverage")

    # 8. Plausibility — nur wenn Pflicht-Felder grundsätzlich da sind
    if not rejections:
        primary = signal.primary_entry
        if (
            primary is not None
            and signal.stop_loss is not None
            and signal.targets
        ):
            if signal.direction == "long":
                if signal.stop_loss >= primary:
                    rejections.append(
                        f"long_sl_above_entry:sl={signal.stop_loss},entry={primary}"
                    )
                if min(signal.targets) <= primary:
                    rejections.append(
                        f"long_target_below_entry:min_target={min(signal.targets)},entry={primary}"
                    )
            elif signal.direction == "short":
                if signal.stop_loss <= primary:
                    rejections.append(
                        f"short_sl_below_entry:sl={signal.stop_loss},entry={primary}"
                    )
                if max(signal.targets) >= primary:
                    rejections.append(
                        f"short_target_above_entry:max_target={max(signal.targets)},entry={primary}"
                    )

    # 9. Leverage-sanity
    if signal.leverage is not None:
        if signal.leverage < 1 or signal.leverage > 125:
            warnings.append(f"leverage_unusual:{signal.leverage}")

    # 10. Risk-Allocation-sanity
    if signal.risk_allocation_pct is not None:
        if signal.risk_allocation_pct <= 0 or signal.risk_allocation_pct > 1.0:
            warnings.append(f"risk_allocation_unusual:{signal.risk_allocation_pct}")

    if rejections:
        return ValidationResult(
            is_valid=False,
            rejected_reason="; ".join(rejections),
            needs_review=False,
            warnings=tuple(warnings),
        )

    return ValidationResult(
        is_valid=True,
        rejected_reason=None,
        needs_review=False,
        warnings=tuple(warnings),
    )


# ─── Constructors ────────────────────────────────────────────────────────────


_CORRELATION_ID_RE = re.compile(r"^SIG-[A-Z0-9]+-[0-9]{14}-[A-Z0-9]+$")


def make_correlation_id(*, source_tag: str, symbol: str) -> str:
    """Channel-scoped correlation_id: SIG-<TAG>-YYYYMMDDHHMMSS-SYMBOL."""
    now = datetime.now(UTC)
    clean_symbol = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    clean_tag = re.sub(r"[^A-Z0-9]", "", source_tag.upper())
    return f"SIG-{clean_tag}-{now.strftime('%Y%m%d%H%M%S')}-{clean_symbol}"


def is_valid_correlation_id(correlation_id: str) -> bool:
    return bool(_CORRELATION_ID_RE.match(correlation_id))


def new_signal(
    *,
    correlation_id: str,
    source: str,
    symbol: str,
    display_symbol: str | None = None,
    side: Side,
    direction: Direction,
    order_intent: OrderIntent = "OPEN_POSITION",
    entry_type: EntryType,
    entry_value: float | None = None,
    entry_min: float | None = None,
    entry_max: float | None = None,
    stop_loss: float,
    targets: tuple[float, ...] | list[float],
    leverage: int | None = None,
    margin_mode: MarginMode | None = None,
    margin_size_usd: float | None = None,
    risk_allocation_pct: float | None = None,
    raw_text: str = "",
    initial_status: SignalStatus = SignalStatus.PARSED,
) -> NormalizedTradeSignal:
    """Convenience-Konstruktor mit konsistenten Defaults.

    Setzt ``parsed_at_utc`` auf ``now()``. Konvertiert ``targets`` zu
    immutablem Tuple. Wirft ValueError bei offensichtlichen Bug-Conditions
    (z.B. correlation_id leer) — Validierung aller Felder bleibt
    Aufgabe von ``validate()``.
    """
    if not correlation_id:
        raise ValueError("correlation_id must be non-empty")
    if not source:
        raise ValueError("source must be non-empty")

    return NormalizedTradeSignal(
        correlation_id=correlation_id,
        source=source,
        symbol=symbol,
        display_symbol=display_symbol or symbol,
        side=side,
        direction=direction,
        order_intent=order_intent,
        entry_type=entry_type,
        entry_value=entry_value,
        entry_min=entry_min,
        entry_max=entry_max,
        stop_loss=stop_loss,
        targets=tuple(targets),
        leverage=leverage,
        margin_mode=margin_mode,
        margin_size_usd=margin_size_usd,
        risk_allocation_pct=risk_allocation_pct,
        status=initial_status,
        parsed_at_utc=_now_utc(),
        raw_text=raw_text,
        audit_reason=None,
        status_history=(),
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "Direction",
    "EntryType",
    "IllegalLifecycleTransition",
    "LIFECYCLE_TRANSITIONS",
    "MarginMode",
    "NormalizedTradeSignal",
    "OrderIntent",
    "Side",
    "SignalStatus",
    "StatusTransition",
    "TERMINAL_STATES",
    "ValidationResult",
    "is_valid_correlation_id",
    "make_correlation_id",
    "new_signal",
    "validate",
]
