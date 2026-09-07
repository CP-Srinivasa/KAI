"""Unit tests for the Telegram-channel listener poll-backstop (2026-05-31).

Incident: the MTProto push-update stream died silently — run_until_disconnected
kept blocking, the heartbeat loop kept ticking, but no messages were observed
(messages_since_boot stuck at 1 for ~46h) and a NIGHT/USDT premium signal was
lost. The poll-backstop pulls via the checkpoint+replay path so a dead push
stream can no longer cause silent signal loss.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from app.ingestion import telegram_channel_worker as w


def _write_checkpoint(path: Path, chat_id: int, last_id: int) -> None:
    path.write_text(json.dumps({str(chat_id): {"last_message_id": last_id}}), encoding="utf-8")


def test_semantic_canary_records_checkpoint_gap(tmp_path: Path) -> None:
    path = tmp_path / "canary.json"
    w._write_semantic_canary(
        path=path,
        chat_id=-100123,
        checkpoint_message_id=10,
        latest_message_id=12,
        replay_processed=0,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["source_platform"] == "telegram"
    assert data["chat_id"] == -100123
    assert data["checkpoint_message_id"] == 10
    assert data["latest_message_id"] == 12
    assert data["gap"] == 2


@pytest.mark.asyncio
async def test_poll_backstop_pulls_with_current_checkpoint(tmp_path, monkeypatch):
    """Each iteration reloads the on-disk checkpoint and replays from it."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 4242)
    seen_last_seen: list[int] = []

    async def fake_replay(client, entity, *, chat_id, last_seen_id, process_fn):
        seen_last_seen.append(last_seen_id)
        # Break the loop after the first successful poll.
        raise asyncio.CancelledError

    monkeypatch.setattr(w, "replay_missed_messages", fake_replay)

    with pytest.raises(asyncio.CancelledError):
        await w._poll_backstop_loop(
            client=object(),
            entity=object(),
            checkpoint_path=ckpt,
            process_fn=lambda mid, txt: None,
            chat_id_marked=-100123,
            interval_s=0,
        )
    assert seen_last_seen == [4242]


@pytest.mark.asyncio
async def test_poll_backstop_failsoft_then_hard_recovery(tmp_path, monkeypatch):
    """Transient failures are swallowed; after N consecutive ones the client
    is disconnected so systemd restarts the worker."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 7)
    calls = {"replay": 0}

    async def always_fail(client, entity, *, chat_id, last_seen_id, process_fn):
        calls["replay"] += 1
        raise RuntimeError("simulated connection death")

    monkeypatch.setattr(w, "replay_missed_messages", always_fail)
    monkeypatch.setattr(w, "_POLL_MAX_CONSECUTIVE_FAILURES", 3)

    disconnected = {"n": 0}

    class FakeClient:
        async def disconnect(self):
            disconnected["n"] += 1

    # Returns (does not raise) once it disconnects for the systemd restart path.
    await w._poll_backstop_loop(
        client=FakeClient(),
        entity=object(),
        checkpoint_path=ckpt,
        process_fn=lambda mid, txt: None,
        chat_id_marked=-100123,
        interval_s=0,
    )
    assert calls["replay"] == 3
    assert disconnected["n"] == 1


@pytest.mark.asyncio
async def test_poll_backstop_recovers_after_transient_failure(tmp_path, monkeypatch):
    """A single failure must NOT trigger hard-recovery; the counter resets on
    the next success."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 1)
    seq = iter([RuntimeError("blip"), "ok"])

    async def flaky(client, entity, *, chat_id, last_seen_id, process_fn):
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        raise asyncio.CancelledError  # break loop on the successful pass

    monkeypatch.setattr(w, "replay_missed_messages", flaky)
    monkeypatch.setattr(w, "_POLL_MAX_CONSECUTIVE_FAILURES", 3)

    disconnected = {"n": 0}

    class FakeClient:
        async def disconnect(self):
            disconnected["n"] += 1

    with pytest.raises(asyncio.CancelledError):
        await w._poll_backstop_loop(
            client=FakeClient(),
            entity=object(),
            checkpoint_path=ckpt,
            process_fn=lambda mid, txt: None,
            chat_id_marked=-100123,
            interval_s=0,
        )
    assert disconnected["n"] == 0


# ---------------------------------------------------------------------------
# Der Fall, den diese Datei bis 2026-09-07 NICHT sehen konnte.
#
# Alle Tests oben pruefen den LAUTEN Fehler: eine Fake-Replay-Funktion wirft,
# der Zaehler laeuft hoch, nach fuenf Runden trennt der Loop die Verbindung.
# Der teure Fall ist der leise: Telethon wirft nicht, es antwortet nur nicht
# mehr. Dann steht der Loop in einem ``await``, der Zaehler bleibt bei 0, und
# die Selbstheilung greift nie.
#
# Gemessen auf kai-pi5: 2026-09-04 15:02 UTC bis 2026-09-07 08:18 UTC — 65
# Stunden ohne Canary-Datei und ohne eine einzige ``gap-replay``-Zeile, bei
# lebendem Prozess und tickendem Heartbeat. Der Premium-Healthcheck schickte in
# der Zeit im 5-Minuten-Takt ``semantic_canary FAIL`` an den Operator.
#
# Diese Tests fixieren, dass ein haengender Zyklus abgebrochen, gezaehlt und am
# Ende in einen Disconnect (= systemd-Neustart + Boot-Replay) uebersetzt wird.
# ---------------------------------------------------------------------------


class _DisconnectSpy:
    """Client-Attrappe, die den Disconnect quittiert statt zu netzwerken."""

    def __init__(self) -> None:
        self.disconnects = 0

    async def disconnect(self) -> None:
        self.disconnects += 1


@pytest.mark.asyncio
async def test_haengender_poll_zyklus_wird_abgebrochen_und_gezaehlt(tmp_path, monkeypatch, caplog):
    """Ein Replay, das nie zurueckkommt, darf den Loop nicht anhalten."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 4242)
    client = _DisconnectSpy()
    versuche = 0

    async def haengendes_replay(*args, **kwargs):
        nonlocal versuche
        versuche += 1
        await asyncio.Event().wait()  # kommt nie zurueck — wie eine tote MTProto-Verbindung

    monkeypatch.setattr(w, "replay_missed_messages", haengendes_replay)
    monkeypatch.setattr(w, "_POLL_ITERATION_TIMEOUT_SEC", 0.05)

    # Die aeussere Grenze ist der eigentliche Nachweis: ohne den Fix laeuft der
    # Loop hier in die Ewigkeit und der Test schlaegt mit TimeoutError fehl.
    await asyncio.wait_for(
        w._poll_backstop_loop(
            client=client,
            entity=object(),
            checkpoint_path=ckpt,
            process_fn=lambda mid, txt: None,
            chat_id_marked=-100123,
            interval_s=0,
        ),
        timeout=10,
    )

    assert versuche == w._POLL_MAX_CONSECUTIVE_FAILURES, (
        "jeder haengende Zyklus muss abgebrochen und der naechste versucht werden"
    )
    assert client.disconnects == 1, "nach der Fehlerschwelle trennt der Loop fuer systemd"


@pytest.mark.asyncio
async def test_haenger_meldet_sich_beim_namen(tmp_path, monkeypatch, caplog):
    """``str(TimeoutError())`` ist leer — die Log-Zeile darf es nicht sein."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 4242)

    async def haengendes_replay(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(w, "replay_missed_messages", haengendes_replay)
    monkeypatch.setattr(w, "_POLL_ITERATION_TIMEOUT_SEC", 0.05)

    caplog.set_level(logging.WARNING, logger=w.logger.name)
    await asyncio.wait_for(
        w._poll_backstop_loop(
            client=_DisconnectSpy(),
            entity=object(),
            checkpoint_path=ckpt,
            process_fn=lambda mid, txt: None,
            chat_id_marked=-100123,
            interval_s=0,
        ),
        timeout=10,
    )

    zeilen = [r.getMessage() for r in caplog.records]
    assert any("HUNG" in z for z in zeilen), f"kein Haenger-Hinweis in {zeilen}"
    assert any("antwortet nicht" in z for z in zeilen), f"keine Ursache in {zeilen}"


@pytest.mark.asyncio
async def test_gesunder_zyklus_bleibt_unbehelligt(tmp_path, monkeypatch):
    """Die Zeitgrenze darf einen normalen Poll nicht abschneiden."""
    ckpt = tmp_path / "checkpoint.json"
    _write_checkpoint(ckpt, -100123, 4242)
    # Der Pfad-Default von ``_write_semantic_canary`` wird beim Definieren
    # gebunden; ein Monkeypatch auf ``_SEMANTIC_CANARY_PATH`` traefe ihn nicht.
    # Was hier zaehlt, ist ohnehin die Frage, OB der gesunde Zyklus die Canary
    # schreibt — der Inhalt haengt am Writer, den der Test oben pruet.
    geschrieben: list[dict] = []
    monkeypatch.setattr(w, "_write_semantic_canary", lambda **kw: geschrieben.append(kw))
    runden = 0

    async def schnelles_replay(client, entity, *, chat_id, last_seen_id, process_fn):
        nonlocal runden
        runden += 1
        if runden >= 2:
            raise asyncio.CancelledError
        return {"scanned": 0, "processed": 0}

    async def kopf_id(client, entity):
        return 4242

    monkeypatch.setattr(w, "replay_missed_messages", schnelles_replay)
    monkeypatch.setattr(w, "_latest_message_id", kopf_id)

    with pytest.raises(asyncio.CancelledError):
        await w._poll_backstop_loop(
            client=_DisconnectSpy(),
            entity=object(),
            checkpoint_path=ckpt,
            process_fn=lambda mid, txt: None,
            chat_id_marked=-100123,
            interval_s=0,
        )

    assert geschrieben, "der gesunde Zyklus schreibt die Canary weiterhin"
    assert geschrieben[0]["checkpoint_message_id"] == 4242
    assert geschrieben[0]["latest_message_id"] == 4242
