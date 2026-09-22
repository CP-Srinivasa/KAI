"""``entry_watcher_audit.jsonl`` wird aus drei Prozessen beschrieben — unter Lock (S2-13b).

Writer: `kai-entry-watch.service` (Dauerlauf), der zweite entry-watch aus
`scripts/paper_trading_cron.sh` (alle 10 min, 55 s) und `run_watch_loop` im
kai-server nach einer Freigabe. Eine Append-Stelle (`_append_watch_audit`),
bisher ohne Lock. Geprueft: der Append geht durch ``append_lock`` und die Zeile
liegt beim Verlassen des Locks auf der Platte; IO-Fehler bleiben geloggt statt
geworfen.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

from app.execution import operator_entry_watch as watch_mod


class _LockRecorder:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.paths: list[Path] = []
        self.content_at_exit: str | None = None

    @contextlib.contextmanager
    def __call__(self, target: Path, *, strict: bool = False) -> Iterator[None]:
        target = Path(target)
        self.paths.append(target)
        self.events.append("enter")
        try:
            yield
        finally:
            self.events.append("exit")
            if target.is_file():
                self.content_at_exit = target.read_text(encoding="utf-8")


def test_watch_audit_append_geht_durch_den_lock(monkeypatch, tmp_path: Path) -> None:
    rec = _LockRecorder()
    monkeypatch.setattr(watch_mod, "append_lock", rec)
    target = tmp_path / "entry_watcher_audit.jsonl"
    monkeypatch.setattr(watch_mod, "_ENTRY_WATCH_AUDIT", target)

    watch_mod._append_watch_audit({"event": "tick", "pending": 0})

    assert rec.paths == [target]
    assert rec.events == ["enter", "exit"]
    assert rec.content_at_exit == '{"event": "tick", "pending": 0}\n'
    assert json.loads(target.read_text(encoding="utf-8")) == {"event": "tick", "pending": 0}


def test_watch_audit_io_fehler_wird_geloggt_nicht_geworfen(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    target = tmp_path / "as_dir.jsonl"
    target.mkdir()
    monkeypatch.setattr(watch_mod, "_ENTRY_WATCH_AUDIT", target)
    with caplog.at_level("ERROR", logger="app.execution.operator_entry_watch"):
        watch_mod._append_watch_audit({"event": "tick"})
    assert any("audit write failed" in r.getMessage() for r in caplog.records)
