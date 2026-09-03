"""Vorbedingungen des Kapital-Cockpits (W0-P1 / PR-C / M-8).

Herausgeloest aus :mod:`app.api.routers.ln_control`, weil das Modul sonst die
350-Zeilen-Grenze reisst — und weil es zwei verschiedene Fragen sind: *"darf
diese Aktion ueberhaupt?"* (hier) und *"was tut sie dann?"* (dort). Die
Antworten hier haengen an Node-Zustand und Journal, nicht an HTTP.

Der gemeinsame Nenner aller vier Funktionen ist die Richtung ihres Zweifels:
kein frischer Kontostand heisst ``None`` und nicht 0, ein unlesbares Journal
heisst Blocker und nicht "nichts gebucht". Ein Default in die andere Richtung
waere jedes Mal ein Freifahrtschein.
"""

from __future__ import annotations

from typing import Any

from app.lightning import value_layer as vl


async def _available_balance_sat() -> int:
    """Best-effort on-chain+channel balance for NON-capital actions (0 if
    unavailable → policy errs conservative: a spend with unknown balance is denied
    if a reserve floor is set). Capital actions use the freshness-gated variant."""
    try:
        from app.lightning.cache import get_cached_node_status

        status, _ = await get_cached_node_status()
        return int(getattr(status, "wallet_total_sat", 0) or 0) + int(
            getattr(status, "channel_local_sat", 0) or 0
        )
    except Exception:  # noqa: BLE001 — balance is best-effort, never block the endpoint
        return 0


async def _fresh_capital_balance_sat() -> int | None:
    """W0-P1: on-chain+channel balance from a FRESH node snapshot, or ``None``.

    ``None`` means no balance-bearing snapshot within ``CAPITAL_MAX_AGE_SECONDS``
    could be obtained (node degraded/unreachable/stale) — the caller must fail
    CLOSED and deny the capital action, never fall back to a cached value.
    """
    try:
        from app.lightning.cache import get_capital_grade_status

        status, _age = await get_capital_grade_status()
    except Exception:  # noqa: BLE001 — any failure counts as "no fresh state"
        return None
    if status is None:
        return None
    return int(getattr(status, "wallet_total_sat", 0) or 0) + int(
        getattr(status, "channel_local_sat", 0) or 0
    )


def _money_journal_blocker() -> str:
    """``""`` when the money journal may be extended, else the deny reason.

    Thin wrapper over the value layer's own precondition so the cockpit denies
    EARLY — before an idempotency key is burned and a HOTP code is consumed — with
    the same verdict the value layer would reach later. Receive actions never
    consult it (see the value-layer asymmetry).
    """
    ok, reason = vl.money_journal_status()
    return "" if ok else reason


def _build_hotp_verifier() -> Any:
    """Der HOTP-Verifier des Cockpits — Seed und Journal aus den Settings."""
    from pathlib import Path

    from app.core.settings import get_settings
    from app.security.hotp_auth import HotpVerifier

    ln = get_settings().lightning
    return HotpVerifier(seed_path=Path(ln.hotp_seed_path), journal_path=Path(ln.hotp_journal_path))


__all__ = [
    "_available_balance_sat",
    "_build_hotp_verifier",
    "_fresh_capital_balance_sat",
    "_money_journal_blocker",
]
