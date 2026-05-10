"""Hard-Caps für KAI Phase-0 Live-Trading.

Spec: docs/security/kai_light_live_phase0_spec.md §1.

Dieses Modul ist die **erste** der zwei unabhängigen Verifikations-Schichten
gegen Cap-Bypass (vgl. Red-Team-Befund S-002): wenn ein Angreifer die
RiskEngine-Methode in-process patched, müsste er ZUSÄTZLICH dieses Modul
patchen, um Order größer als $200 oder mehr als 2 offene Positionen zu
platzieren.

Coding-Regeln:
1. Keine Settings-Felder. Hardcoded-Konstanten — Änderung = Code-Review-Pflicht.
2. Kein dev-Mode-Bypass-Flag. Tests müssen real-cap-konform laufen.
3. Fail-closed: any error im Verify → LiveCapBreach raise.

Status 2026-05-09: Skeleton. Nicht im Live-Order-Pfad eingebunden, weil Live
heute disabled (paper_engine.py:73-78). Eingebunden mit Sprint-Item 7 in spec
(`live_engine.py` plus `place_live_order`).
"""

from __future__ import annotations

from dataclasses import dataclass

# 2026-05-09 — DO NOT make configurable.
# Phase-0-Schutz: jeder Override braucht Code-Edit + Re-Deploy + Operator-Review.
# Eskalation auf $500-1000/position kommt nach 3 Monaten Stable-Run, nicht heute.
MAX_POSITION_USD: float = 200.0
MAX_OPEN_POSITIONS: int = 2

# Live-Mode-Default beim Boot. Wird per HOTP-Command geunlockt, nie automatisch.
LIVE_TRADING_DEFAULT_ENABLED: bool = False

# Auto-Lock nach Idle. 60 Min ist enger als üblich, um S-001 zu mitigieren
# (Session-Timer ist nicht adversarial-wirksam, aber als zweite Linie OK).
LIVE_MODE_IDLE_LOCK_SECONDS: int = 3600


class LiveCapBreach(Exception):
    """Wird geworfen wenn eine Live-Order eine Phase-0-Cap verletzt.

    Nicht mit `RiskCheckFailed` o.ä. zusammenführen — das soll explizit
    sichtbar als zweite, unabhängige Schicht im Audit-Log auftauchen.
    """


@dataclass(frozen=True)
class LiveOrderCapsView:
    """Snapshot der relevanten Werte für eine Cap-Verifikation."""

    notional_usd: float
    current_open_positions: int
    symbol: str
    side: str


def verify_live_order(view: LiveOrderCapsView) -> None:
    """Verifiziert eine Live-Order gegen die Phase-0-Hard-Caps.

    Wirft `LiveCapBreach` wenn eine Cap verletzt ist. Kein Return-Value —
    keine Cap erlaubt einen Soft-Pass mit Warning.

    Args:
        view: ``LiveOrderCapsView`` mit notional, open-positions und kontext.

    Raises:
        LiveCapBreach: bei jeder Cap-Verletzung. Audit-Logger sollte den
            Exception-Text 1:1 in den Live-Audit-Stream schreiben.
    """
    if view.notional_usd <= 0:
        raise LiveCapBreach(
            f"invalid_notional: {view.notional_usd} (symbol={view.symbol})"
        )
    if view.notional_usd > MAX_POSITION_USD:
        raise LiveCapBreach(
            f"position_size_exceeds_cap: {view.notional_usd:.2f} USD > "
            f"{MAX_POSITION_USD:.2f} USD (symbol={view.symbol}, side={view.side})"
        )
    if view.current_open_positions < 0:
        raise LiveCapBreach(
            f"invalid_open_count: {view.current_open_positions} "
            f"(symbol={view.symbol})"
        )
    if view.current_open_positions >= MAX_OPEN_POSITIONS:
        raise LiveCapBreach(
            f"max_open_positions_exceeded: {view.current_open_positions} >= "
            f"{MAX_OPEN_POSITIONS} (incoming symbol={view.symbol})"
        )


def caps_summary() -> dict[str, float | int | bool | str]:
    """Read-only Snapshot der aktiven Caps.

    Used by:
        - Telegram `/live status` command output
        - Audit-Log header für jede Live-Session
        - Boot-Log auf kai-server-Start (sichtbar dass Phase-0 aktiv ist)
    """
    return {
        "max_position_usd": MAX_POSITION_USD,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "live_default_enabled": LIVE_TRADING_DEFAULT_ENABLED,
        "live_idle_lock_seconds": LIVE_MODE_IDLE_LOCK_SECONDS,
        "phase": "phase-0-light-live",
        "spec_doc": "docs/security/kai_light_live_phase0_spec.md",
    }
