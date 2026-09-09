"""Die WARP-Sonde darf den Event-Loop nicht anhalten.

``_summarize_warp_status`` ist synchron und macht zwei blockierende Dinge: auf
Windows ein ``subprocess.run`` mit 3 s Timeout, plattformuebergreifend ein
``socket.gethostbyname_ex`` — ein Resolver-Aufruf, der ohne Antwort bis zu
``timeout x attempts`` haengt (glibc-Default 5 s x 2).

Aufgerufen wurde sie direkt aus ``get_daily_operator_summary`` (async), und der
Dienst laeuft als Single-Worker-Uvicorn (app/api/event_hub.py:5, D-159). Ein
haengender Resolver hielt damit nicht diesen einen Request auf, sondern den
gesamten Prozess — jede andere Route wartete mit.

Erschwerend: gesucht wird Cloudflare WARP, das auf dem Pi nie laeuft.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agents.tools import canonical_read


@pytest.mark.asyncio
async def test_warp_probe_does_not_stall_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Waehrend die Sonde blockiert, muss der Loop weiterlaufen."""

    def _slow_probe() -> dict[str, object]:
        time.sleep(0.4)  # steht fuer einen haengenden Resolver
        return {"active": False, "detection_method": "none"}

    monkeypatch.setattr(canonical_read, "_summarize_warp_status", _slow_probe)

    ticks = 0

    async def _heartbeat() -> None:
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.02)
            ticks += 1

    beat = asyncio.create_task(_heartbeat())
    await canonical_read._warp_status_offloop()
    await beat

    # Bei blockierendem Aufruf steht der Loop 0,4 s still und der Heartbeat
    # kommt praktisch nicht zum Zug.
    assert ticks >= 10, f"Event-Loop war blockiert — nur {ticks} Ticks"


@pytest.mark.asyncio
async def test_warp_probe_still_returns_its_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off-loop heisst nicht: Ergebnis verwerfen."""
    monkeypatch.setattr(
        canonical_read,
        "_summarize_warp_status",
        lambda: {"active": True, "detection_method": "interface", "hint": "x"},
    )
    out = await canonical_read._warp_status_offloop()
    assert out["active"] is True
    assert out["detection_method"] == "interface"
