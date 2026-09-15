"""MB-02: die Agenten-Queue ``commands.jsonl`` ist append-only.

Bis 6e5a7b6e las der Worker die Datei komplett, fuehrte die Handler aus und
ersetzte die Datei danach (``_rewrite_jsonl``). Ein Kommando, das Dashboard
oder Telegram WAEHREND der Handler-Laufzeit anhaengten, war danach weg —
isoliert reproduziert im Codex-Plan "MindBlower" (15.09.2026), auf dem Pi nie
eingetreten (Queue seit 03.05. unbenutzt), im Code aber real.

Neuer Vertrag:
- Der Worker schreibt ``commands.jsonl`` nie zurueck. Produzenten haengen an,
  sonst nichts.
- Erledigt ist ein Kommando genau dann, wenn ``runs.jsonl`` eine Zeile mit
  seiner ``command_id`` traegt. ``runs.jsonl`` ist bereits append-only und
  wird bereits je Ausfuehrung geschrieben — kein zweiter Truth-State.
- Zeilen ohne ``id`` werden nicht ausgefuehrt (Altzeilen aus der Rewrite-Zeit
  tragen ohnehin ``status: done``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agents import worker


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _cmd(cmd_id: str, mode: str = "check") -> dict[str, Any]:
    return {
        "id": cmd_id,
        "ts": "2026-09-15T18:00:00+00:00",
        "agent": "watchdog",
        "mode": mode,
        "status": "queued",
    }


@pytest.fixture
def agent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "agents"

    def _dir(slug: str) -> Path:
        return base / slug

    monkeypatch.setattr(worker, "_agent_dir", _dir)
    monkeypatch.setattr("app.api.routers.agents._agent_dir", _dir)
    return base / "watchdog"


def test_command_appended_during_handler_survives_and_runs_next_tick(
    agent_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = agent_dir / "commands.jsonl"
    _append(queue, _cmd("A"))
    seen: list[str | None] = []

    def handler(note: str | None) -> tuple[str, str]:
        # Der Produzent (Dashboard/Telegram) haengt B an, WAEHREND A laeuft.
        if not any(r["id"] == "B" for r in _rows(queue)):
            _append(queue, _cmd("B"))
        seen.append(note)
        return ("ok", "ok")

    monkeypatch.setattr(worker, "HANDLERS", {("watchdog", "check"): handler})

    assert worker._process_agent("watchdog", {}) == 1
    assert [r["id"] for r in _rows(queue)] == ["A", "B"], "B darf nicht verschwinden"

    assert worker._process_agent("watchdog", {}) == 1
    runs = _rows(agent_dir / "runs.jsonl")
    assert [r["command_id"] for r in runs] == ["A", "B"]

    # Dritter Tick: nichts mehr offen, nichts doppelt.
    assert worker._process_agent("watchdog", {}) == 0
    assert [r["command_id"] for r in _rows(agent_dir / "runs.jsonl")] == ["A", "B"]


def test_queue_file_is_never_rewritten(agent_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = agent_dir / "commands.jsonl"
    _append(queue, _cmd("A"))
    before = queue.read_text(encoding="utf-8")
    monkeypatch.setattr(worker, "HANDLERS", {("watchdog", "check"): lambda note: ("ok", "ok")})

    worker._process_agent("watchdog", {})

    assert queue.read_text(encoding="utf-8") == before
    assert not hasattr(worker, "_rewrite_jsonl")


def test_restart_does_not_rerun_commands_recorded_in_runs_journal(
    agent_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = agent_dir / "commands.jsonl"
    _append(queue, _cmd("A"))
    _append(queue, _cmd("B"))
    # Absturz nach A: runs.jsonl kennt A, der Worker-Zustand ist leer.
    _append(
        agent_dir / "runs.jsonl",
        {"ts": "x", "mode": "check", "result": "ok", "duration_ms": 1, "command_id": "A"},
    )
    calls: list[str] = []
    monkeypatch.setattr(
        worker,
        "HANDLERS",
        {("watchdog", "check"): lambda note: (calls.append("run") or "ok", "ok")},
    )

    assert worker._process_agent("watchdog", {}) == 1
    assert calls == ["run"]
    assert [r["command_id"] for r in _rows(agent_dir / "runs.jsonl")] == ["A", "B"]


def test_legacy_rows_and_rows_without_id_are_skipped(
    agent_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = agent_dir / "commands.jsonl"
    _append(
        queue, {"status": "done", "mode": "check", "note": "d220", "result": "ok"}
    )  # Altzeile ohne id
    _append(queue, {**_cmd("old"), "status": "done"})  # von der Rewrite-Zeit erledigt
    _append(
        queue, {"ts": "x", "agent": "watchdog", "mode": "check", "status": "queued"}
    )  # queued ohne id
    calls: list[str] = []
    monkeypatch.setattr(
        worker,
        "HANDLERS",
        {("watchdog", "check"): lambda note: (calls.append("run") or "ok", "ok")},
    )

    assert worker._process_agent("watchdog", {}) == 0
    assert calls == []
    assert not (agent_dir / "runs.jsonl").exists()


def test_unknown_mode_is_recorded_as_skipped_and_not_retried(
    agent_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = agent_dir / "commands.jsonl"
    _append(queue, _cmd("A", mode="does-not-exist"))
    monkeypatch.setattr(worker, "HANDLERS", {})

    assert worker._process_agent("watchdog", {}) == 1
    assert worker._process_agent("watchdog", {}) == 0
    runs = _rows(agent_dir / "runs.jsonl")
    assert len(runs) == 1 and runs[0]["result"] == "skipped" and runs[0]["command_id"] == "A"
