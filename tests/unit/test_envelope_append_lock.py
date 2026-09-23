"""``telegram_message_envelope.jsonl``: Writer unter Lock, Leser gegen Byte-Muell (S2-13c).

Vier Append-Stellen in zwei Prozessen (kai-tg-listener und kai-server) schrieben
diesen Strom ohne Lock. Eine verzahnte Zeile ist kein JSON mehr — und die drei
Leser fingen nur ``OSError``, sodass ein ``UnicodeDecodeError`` aus kaputten
Bytes bis in das Dedup-Gate bzw. den Dashboard-Endpunkt durchschlug (doppelter
Re-Emit bzw. 500). Geprueft wird beides: der gemeinsame Schreibhelfer haelt den
Lock, und jeder Leser ueberlebt eine unlesbare Datei.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.storage import jsonl_io
from app.storage.jsonl_io import append_jsonl_locked


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


# ── Schreibhelfer ──────────────────────────────────────────────────────────


def test_append_geht_durch_den_lock_und_legt_das_verzeichnis_an(
    monkeypatch, tmp_path: Path
) -> None:
    rec = _LockRecorder()
    monkeypatch.setattr(jsonl_io, "append_lock", rec)
    target = tmp_path / "nested" / "telegram_message_envelope.jsonl"

    append_jsonl_locked(target, {"stage": "accepted"})

    assert rec.paths == [target]
    assert rec.events == ["enter", "exit"]
    # Die Zeile liegt schon beim Verlassen des Locks auf der Platte (flush im Lock).
    assert rec.content_at_exit == '{"stage": "accepted"}\n'


def test_ensure_ascii_steuert_die_schreibweise(tmp_path: Path) -> None:
    roh = tmp_path / "roh.jsonl"
    escaped = tmp_path / "escaped.jsonl"

    append_jsonl_locked(roh, {"text": "Größe"})
    append_jsonl_locked(escaped, {"text": "Größe"}, ensure_ascii=True)

    assert roh.read_text(encoding="utf-8") == '{"text": "Größe"}\n'
    assert escaped.read_text(encoding="utf-8") == '{"text": "Gr\\u00f6\\u00dfe"}\n'
    # Beide Schreibweisen bleiben dasselbe Objekt.
    assert json.loads(roh.read_text(encoding="utf-8")) == json.loads(
        escaped.read_text(encoding="utf-8")
    )


def test_io_fehler_wird_durchgereicht(tmp_path: Path) -> None:
    # Der Helfer faengt nicht — jede Aufrufstelle behaelt ihr bisheriges Verhalten.
    target = tmp_path / "als_verzeichnis.jsonl"
    target.mkdir()
    with pytest.raises(OSError):
        append_jsonl_locked(target, {"a": 1})


def test_parallele_appends_bleiben_ganze_zeilen(tmp_path: Path) -> None:
    target = tmp_path / "telegram_message_envelope.jsonl"
    threads, per_thread = 4, 5
    # > 8 KiB: ein write() zerfaellt im TextIOWrapper in mehrere Syscalls.
    payload = "x" * 9000
    barrier = threading.Barrier(threads)
    errors: list[BaseException] = []

    def work(worker: int) -> None:
        try:
            barrier.wait(timeout=30)
            for i in range(per_thread):
                append_jsonl_locked(target, {"w": worker, "i": i, "p": payload})
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


# ── Leser ueberleben unlesbare Bytes ───────────────────────────────────────

#: Eine halbe UTF-8-Sequenz, wie sie ein zerrissener Append hinterlaesst.
_KAPUTTE_BYTES = b'{"idempotency_key": "abc", "stage": "accepted"}\n{"text": "Gr\xc3'


def test_approval_leser_ueberlebt_kaputte_bytes(tmp_path: Path) -> None:
    from app.ingestion import telegram_channel_approval as approval

    path = tmp_path / "envelope.jsonl"
    path.write_bytes(_KAPUTTE_BYTES)

    assert approval._iter_records(path) == []
    assert approval.load_envelope_by_id(path, "ENV-1") is None


def test_bot_dedup_gate_ueberlebt_kaputte_bytes(tmp_path: Path) -> None:
    from app.messaging.telegram_bot import TelegramOperatorBot

    path = tmp_path / "envelope.jsonl"
    path.write_bytes(_KAPUTTE_BYTES)
    bot = TelegramOperatorBot.__new__(TelegramOperatorBot)
    bot._message_envelope_log_path = path  # type: ignore[attr-defined]

    # Fail-open statt Absturz: das Gate meldet "nicht gesehen", der naechste
    # saubere Append entscheidet wieder regulaer.
    assert bot._is_duplicate_envelope("abc") is False


@pytest.mark.asyncio
async def test_dashboard_endpunkt_ueberlebt_kaputte_bytes(monkeypatch, tmp_path: Path) -> None:
    from app.api.routers import signals as signals_mod

    path = tmp_path / "envelope.jsonl"
    path.write_bytes(_KAPUTTE_BYTES)
    monkeypatch.setattr(signals_mod, "_ENVELOPE_AUDIT_PATH", path)

    response = await signals_mod.recent_envelopes(limit=50, _auth=None)

    assert response.count == 0
    assert response.records == []
