"""Die Operator-Lesepfade duerfen den Event-Loop nicht blockieren.

Hintergrund (2026-09-08): Nach dem Symlink-Boundary-Fix liefern
``/operator/portfolio-snapshot``, ``/operator/exposure-summary`` und
``/operator/trading-loop/recent-cycles`` wieder Daten statt 503 — und rechnen
damit auch wieder. Gemessen auf dem Pi: ein Portfolio-Aufruf braucht 2,28 s,
``build_recent_cycles_summary`` liest 138.263 Zeilen vollstaendig ein.

Liefe diese Arbeit synchron im async-Pfad, wuerde der wiederbelebte Request-Pfad
den Loop im Minutentakt blockieren: ``/health`` liefe in den 6-s-Timeout des
Watchdogs, der wuerde neu starten, und jeder Neustart kostet 15-16 s Vollausfall.
Der Fix (``asyncio.to_thread``) muss deshalb an der BETRIEBSSEMANTIK gemessen
werden, nicht am Rueckgabewert: waehrend des Aufrufs muss der Loop weiterlaufen.

Die Gegenprobe unten ruft denselben Code SYNCHRON auf und muss scheitern —
sonst ist die Fixture zu klein und der Test misst nichts.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.agents.tools import _helpers, canonical_read

# Gross genug, dass ein synchroner Durchlauf den Loop sichtbar anhaelt, und
# klein genug fuer eine schnelle Suite.
_ROWS = 20_000
_TICK_S = 0.005


def _write_loop_audit(path: Path, rows: int = _ROWS) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i in range(rows):
            fh.write(
                json.dumps(
                    {
                        "cycle_id": f"cyc_{i:08x}",
                        "started_at": "2026-03-22T20:04:53.434767+00:00",
                        "completed_at": "2026-03-22T20:04:53.434831+00:00",
                        "symbol": "BTC/USDT",
                        "status": "no_signal" if i % 3 else "signal",
                        "market_data_fetched": True,
                        "signal_generated": bool(i % 3 == 0),
                        "notes": ["signal_filtered_or_absent"],
                    }
                )
                + "\n"
            )


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "artifacts").mkdir()
    monkeypatch.setattr(_helpers, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


class _LoopTicker:
    """Zaehlt, wie oft der Event-Loop waehrend eines Aufrufs drankommt."""

    def __init__(self) -> None:
        self.ticks = 0
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> _LoopTicker:
        async def run() -> None:
            while True:
                self.ticks += 1
                await asyncio.sleep(_TICK_S)

        self._task = asyncio.create_task(run())
        await asyncio.sleep(0.05)  # Ticker anlaufen lassen
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()


@pytest.mark.asyncio
async def test_recent_cycles_keeps_the_loop_alive(workspace: Path) -> None:
    """Der ausgelagerte Aufruf muss den Loop DEUTLICH weiterlaufen lassen als der
    synchrone — beide im selben Lauf gemessen.

    Eine absolute Tick-Schwelle taugt hier nicht: in CI laeuft die Suite mit
    ``pytest -n auto`` und Coverage, mehrere xdist-Worker konkurrieren um wenige
    Kerne, und der Worker-Thread haelt waehrend ``json.loads`` die GIL. Eine feste
    Zahl misst dann die Maschinenlast mit, nicht die Auslagerung — am 2026-09-08
    schlug genau das mit "nur 3 Ticks" fehl, obwohl der Fix in Ordnung ist.

    Der VERGLEICH ist maschinenunabhaengig: derselbe Rechner, dieselbe Last,
    dieselbe Datei, einmal ausgelagert und einmal synchron. Nur die Auslagerung
    kann den Unterschied erklaeren.
    """
    from app.orchestrator.trading_loop import build_recent_cycles_summary

    audit = workspace / "artifacts" / "trading_loop_audit.jsonl"
    _write_loop_audit(audit)

    # (a) synchron — der Loop steht, solange gerechnet wird
    async with _LoopTicker() as ticker:
        before = ticker.ticks
        build_recent_cycles_summary(audit_path=audit, last_n=20)
        moved_sync = ticker.ticks - before

    # (b) ueber den echten Endpunkt, also mit to_thread
    async with _LoopTicker() as ticker:
        before = ticker.ticks
        payload = await canonical_read.get_recent_trading_cycles(
            audit_path="artifacts/trading_loop_audit.jsonl", last_n=20
        )
        moved_async = ticker.ticks - before

    assert payload["total_cycles"] == _ROWS
    # Gegenprobe gegen den eigenen Leerlauf: blockiert der synchrone Pfad gar
    # nicht, ist die Fixture zu klein und der Vergleich unten sagt nichts aus.
    assert moved_sync <= 1, (
        f"Die Fixture ist zu klein: der synchrone Aufruf liess den Loop {moved_sync} mal "
        "weiterlaufen. Erhoehe _ROWS, sonst misst dieser Test nichts."
    )
    assert moved_async > moved_sync, (
        f"Keine Entlastung messbar: ausgelagert {moved_async} Ticks, synchron {moved_sync}."
    )


@pytest.mark.asyncio
async def test_audit_stream_validation_runs_off_loop(workspace: Path) -> None:
    """Der teuerste der drei Paesse ueber paper_execution_audit.jsonl."""
    audit = workspace / "artifacts" / "paper_execution_audit.jsonl"
    with audit.open("w", encoding="utf-8") as fh:
        for i in range(_ROWS):
            fh.write(json.dumps({"event": "fill", "seq": i, "symbol": "BTC/USDT"}) + "\n")

    async with _LoopTicker() as ticker:
        before = ticker.ticks
        await canonical_read._audit_stream_validation_summary(audit, "paper_execution_audit")
        moved = ticker.ticks - before

    # Absichtlich niedrig: geprueft wird, DASS der Loop ueberhaupt drankommt. Wie
    # oft, entscheidet unter xdist+Coverage die Maschinenlast, nicht der Code.
    assert moved >= 1, f"Event-Loop stand waehrend der Validierung still (nur {moved} Ticks)"
