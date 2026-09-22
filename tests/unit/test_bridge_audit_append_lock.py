"""System-Audit 16.09. (NEO-A-014, S2-13): Bridge-Audit-Append und Rotation unter Lock.

``bridge_pending_orders.jsonl`` wird aus vier Prozessen beschrieben; bisher
ohne Lock. Geprueft wird Verhalten: der Append geht durch ``append_lock``,
parallele Writer hinterlassen nur ganze JSON-Zeilen, ein IO-Fehler wird
geloggt statt geworfen, der Bridge-Wrapper ruft den Event-Store nur nach
erfolgreichem Schreiben, und die Rotation haelt denselben Lock ueber Lesen,
Umbenennen und Neuanlegen.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import app.observability.premium_event_store as premium_event_store
from app.execution import bridge_audit_log
from app.execution import envelope_to_paper_bridge as bridge

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import audit_rotate  # noqa: E402


class _LockRecorder:
    """Ersatz fuer ``append_lock``: protokolliert Ziel und Reihenfolge."""

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


def _record(w: int, i: int, payload: str) -> dict[str, object]:
    return {"w": w, "i": i, "p": payload}


# ── append_bridge_audit ────────────────────────────────────────────────────


def test_append_geht_durch_den_lock(monkeypatch, tmp_path: Path) -> None:
    rec = _LockRecorder()
    monkeypatch.setattr(bridge_audit_log, "append_lock", rec)
    target = tmp_path / "bridge_pending_orders.jsonl"

    assert bridge_audit_log.append_bridge_audit(target, {"a": 1}) is True

    assert rec.paths == [target]
    assert rec.events == ["enter", "exit"]
    # Die Zeile steht schon beim Verlassen des Locks auf der Platte (flush im Lock).
    assert rec.content_at_exit == '{"a": 1}\n'
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_parallel_appends_bleiben_ganze_zeilen(tmp_path: Path) -> None:
    target = tmp_path / "bridge_pending_orders.jsonl"
    # 4 statt 8 Threads: msvcrt.locking wartet im 1-s-Takt hoechstens 10x und
    # faellt dann best-effort auf ungelockt zurueck; weniger Kontention haelt
    # den Test auf Windows deterministisch und kurz.
    threads, per_thread = 4, 5
    # > 8 KiB: ein write() zerfaellt im TextIOWrapper in mehrere Syscalls.
    payload = "x" * 9000
    barrier = threading.Barrier(threads)
    errors: list[BaseException] = []

    def work(w: int) -> None:
        try:
            barrier.wait(timeout=30)
            for i in range(per_thread):
                assert bridge_audit_log.append_bridge_audit(target, _record(w, i, payload))
        except BaseException as exc:  # noqa: BLE001 — im Haupt-Thread pruefen
            errors.append(exc)

    pool = [threading.Thread(target=work, args=(w,)) for w in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(timeout=60)

    assert errors == []
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == threads * per_thread
    records = [json.loads(line) for line in lines]
    assert {(r["w"], r["i"]) for r in records} == {
        (w, i) for w in range(threads) for i in range(per_thread)
    }
    assert all(r["p"] == payload for r in records)


def test_io_fehler_wird_geloggt_nicht_geworfen(tmp_path: Path, caplog) -> None:
    target = tmp_path / "as_dir.jsonl"
    target.mkdir()
    with caplog.at_level("ERROR", logger="app.execution.bridge_audit_log"):
        assert bridge_audit_log.append_bridge_audit(target, {"a": 1}) is False
    assert any("audit write failed" in r.getMessage() for r in caplog.records)


# ── Bridge-Wrapper ─────────────────────────────────────────────────────────


def test_bridge_wrapper_nutzt_lock_und_event_store_nach_erfolg(monkeypatch, tmp_path: Path) -> None:
    rec = _LockRecorder()
    monkeypatch.setattr(bridge_audit_log, "append_lock", rec)
    target = tmp_path / "bridge_pending_orders.jsonl"
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", target)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(premium_event_store, "record_bridge_decision", calls.append)

    bridge._append_bridge_audit({"stage": "test"})

    assert rec.paths == [target]
    assert calls == [{"stage": "test"}]
    assert json.loads(target.read_text(encoding="utf-8")) == {"stage": "test"}


def test_bridge_wrapper_ueberspringt_event_store_bei_io_fehler(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "as_dir.jsonl"
    target.mkdir()
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", target)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(premium_event_store, "record_bridge_decision", calls.append)

    bridge._append_bridge_audit({"stage": "test"})

    assert calls == []


# ── Rotation ───────────────────────────────────────────────────────────────


def test_rotation_haelt_den_lock_ueber_lesen_rename_und_neuanlage(
    monkeypatch, tmp_path: Path
) -> None:
    rec = _LockRecorder()
    monkeypatch.setattr(audit_rotate, "append_lock", rec)
    f = tmp_path / "stream.jsonl"
    with f.open("w", encoding="utf-8") as fh:
        for i in range(100):
            fh.write(json.dumps({"i": i, "pad": "x" * 100}) + "\n")
    original_rename = Path.rename

    def rename(self: Path, target: Path) -> Path:
        rec.events.append("rename")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)

    result = audit_rotate.rotate_stream(
        f, max_bytes=100, keep_lines=10, archive_dir=tmp_path / "archive", apply=True
    )

    assert result.rotated is True
    assert rec.paths == [f]
    assert rec.events == ["enter", "rename", "exit"]
    assert len(f.read_text(encoding="utf-8").splitlines()) == 10


def test_rotation_no_shrink_guard_gibt_den_lock_frei(monkeypatch, tmp_path: Path) -> None:
    rec = _LockRecorder()
    monkeypatch.setattr(audit_rotate, "append_lock", rec)
    f = tmp_path / "stream.jsonl"
    with f.open("w", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"i": i, "pad": "x" * 100}) + "\n")

    result = audit_rotate.rotate_stream(
        f, max_bytes=100, keep_lines=10, archive_dir=tmp_path / "archive", apply=True
    )

    assert result.rotated is False
    assert result.reason.startswith("tail_covers_whole_file")
    assert rec.events == ["enter", "exit"]
