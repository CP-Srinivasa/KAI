"""Welle 0 / ADR 0016 — der Reserve-Floor darf nicht gegen einen alten Kontostand rechnen.

Der Node-Cache serviert bei degradierten Polls bewusst den *älteren, reicheren*
Snapshot (Anti-Flicker in ``app.lightning.cache._merge``) und rückt seinen
Zeitstempel dabei NICHT vor. Genau das ist der Fall, den lnd über Tor regelmäßig
produziert. Ohne Alters-Prüfung entscheidet der Floor — laut Policy-Docstring ein
harter, nicht überschreibbarer Backstop — dann gegen einen Stand, der Stunden alt
sein kann, während der Node weiter ausgibt.

``_available_balance_sat`` liefert deshalb ``(sat, known)``: ``known=False`` heißt
„kein prüfbarer Kontostand", nicht „Kontostand ist 0". Der Unterschied ist die
Begründung im Verdikt — ein 0-Guthaben wäre eine Aussage über das Kapital, ein
unbekanntes ist eine über die Messung.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ln_control as lc
from app.lightning.adapter import LightningNodeStatus
from app.lightning.policy import PolicyEnvelope

_URL = "/dashboard/api/ln/value-action"


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(lc.router)
    return a


def _status(*, balances: bool = True) -> LightningNodeStatus:
    return LightningNodeStatus(
        state="ok",
        reachable=True,
        info_available=True,
        balances_available=balances,
        wallet_total_sat=1_000_000,
        channel_local_sat=0,
    )


def _patch_cache(monkeypatch: pytest.MonkeyPatch, status: Any, age: float | None) -> None:
    """Den Cache-Rückgabewert stellen — inklusive Alter, das der Aufrufer prüfen muss."""

    async def _fake() -> tuple[Any, float | None]:
        return status, age

    import app.lightning.cache as cache_mod

    monkeypatch.setattr(cache_mod, "get_cached_node_status", _fake)


def _envelope(*, reserve: int) -> PolicyEnvelope:
    return PolicyEnvelope(
        allowed_actions=frozenset({"send_coins"}),
        per_action_cap_sat=100_000,
        daily_cap_sat=100_000,
        reserve_floor_sat=reserve,
    )


def _post(monkeypatch: pytest.MonkeyPatch, envelope: PolicyEnvelope) -> dict[str, Any]:
    lc.reset_control_state()
    monkeypatch.setattr(lc.PolicyStore, "load", lambda self: envelope)
    monkeypatch.setattr(lc, "spent_today_sat", lambda: 0)
    r = TestClient(_app()).post(
        _URL, json={"action": "send_coins", "params": {"addr": "bc1q", "amount_sat": 1_000}}
    )
    assert r.status_code == 200
    result: dict[str, Any] = r.json()
    return result


# --- die Quelle: liefert _available_balance_sat das Alter mit? ---


@pytest.mark.asyncio
async def test_frischer_snapshot_gilt_als_bekannt(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cache(monkeypatch, _status(), age=5.0)
    assert await lc._available_balance_sat() == (1_000_000, True)


@pytest.mark.asyncio
async def test_alter_snapshot_gilt_als_unbekannt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Betrag wird nicht zurückgegeben — er ist nicht mehr prüfbar."""
    _patch_cache(monkeypatch, _status(), age=lc._BALANCE_MAX_AGE_S + 1)
    assert await lc._available_balance_sat() == (0, False)


@pytest.mark.asyncio
async def test_kalter_cache_gilt_als_unbekannt(monkeypatch: pytest.MonkeyPatch) -> None:
    """``age is None`` = noch nie erfolgreich gepollt — kein Freibrief."""
    _patch_cache(monkeypatch, _status(), age=None)
    assert await lc._available_balance_sat() == (0, False)


@pytest.mark.asyncio
async def test_fehlende_balance_felder_gelten_als_unbekannt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``balances_available=False``: die Zahlen im Snapshot stammen aus einem
    früheren Poll, den der Anti-Flicker-Merge festgehalten hat."""
    _patch_cache(monkeypatch, _status(balances=False), age=1.0)
    assert await lc._available_balance_sat() == (0, False)


@pytest.mark.asyncio
async def test_cache_fehler_gilt_als_unbekannt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom() -> tuple[Any, float | None]:
        raise RuntimeError("node weg")

    import app.lightning.cache as cache_mod

    monkeypatch.setattr(cache_mod, "get_cached_node_status", _boom)
    assert await lc._available_balance_sat() == (0, False)


# --- das Gate: was macht der Endpunkt daraus? ---


async def _unknown() -> tuple[int, bool]:
    return 0, False


def test_unbekannter_stand_verweigert_ausgabe_bei_gesetztem_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lc, "_available_balance_sat", _unknown)
    body = _post(monkeypatch, _envelope(reserve=1_840_000))

    assert body["policy"]["decision"] == "denied"
    assert "stale or unavailable" in body["policy"]["reason"]


def test_begruendung_nennt_die_messung_auch_ohne_gesetzten_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der eigentliche Gewinn des Gates ist die Begründung, nicht die Sperre.

    Ohne Gate verweigert die Policy auch hier — aber mit „would breach reserve
    floor", obwohl gar kein Floor gesetzt ist: Der unbekannte Stand kommt als 0 an
    und ``0 - amount < 0`` greift. Im Audit stünde damit ein Kapitalbefund, wo ein
    Messausfall war.
    """
    monkeypatch.setattr(lc, "_available_balance_sat", _unknown)
    body = _post(monkeypatch, _envelope(reserve=0))

    assert body["policy"]["decision"] == "denied"
    assert "stale or unavailable" in body["policy"]["reason"]
    assert "would breach reserve floor" not in body["policy"]["reason"]


def test_frischer_stand_passiert_das_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _known() -> tuple[int, bool]:
        return 5_000_000, True

    monkeypatch.setattr(lc, "_available_balance_sat", _known)
    body = _post(monkeypatch, _envelope(reserve=1_840_000))

    assert body["policy"]["decision"] == "auto_execute"
