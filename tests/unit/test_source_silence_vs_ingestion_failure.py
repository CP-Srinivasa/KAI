"""Schweigt die Quelle, oder haengen wir hinterher?

Der Premium-Kanal lieferte zwischen dem 2026-09-04 und dem 2026-09-08 keine
Nachricht. Die Frage, die vier Tage lang niemand beantworten konnte, war nicht
"ist etwas kaputt", sondern **wo**: bei der Quelle oder bei uns. `gap=0` allein
sagt nur "wir sind auf dem Stand" — nicht, wie alt dieser Stand ist.

Diese Tests halten die Trennung fest, die daraus folgt (Operator-Entscheidung
2026-09-08):

    last_source_message_at    wann hat die QUELLE zuletzt gesendet
    last_ingested_message_at  wann haben WIR zuletzt etwas verarbeitet
    source_silence_age_s      wie lange schweigt die Quelle

Ohne sie liest sich Anbieter-Stille wie ein eigener Defekt — und ein eigener
Defekt wie Anbieter-Stille. Beide Verwechslungen sind teuer, die zweite mehr.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ingestion.telegram_channel_worker import _write_semantic_canary
from app.observability.premium_pipeline_health import (
    SOURCE_STATE_BEHIND,
    SOURCE_STATE_CURRENT,
    SOURCE_STATE_UNKNOWN,
    render_source_state,
    source_state,
)

# ── Die Zustandsableitung ────────────────────────────────────────────────────


def test_rueckstand_ist_unser_befund() -> None:
    """``gap > 0``: die Quelle hat gesendet, wir haben es nicht verarbeitet."""
    assert source_state({"gap": 3}) == SOURCE_STATE_BEHIND


def test_kein_rueckstand_heisst_auf_dem_stand_der_quelle() -> None:
    """``gap == 0`` sagt NICHT 'alles gut', sondern 'wir sind auf dem Stand'."""
    assert source_state({"gap": 0}) == SOURCE_STATE_CURRENT


def test_ohne_gap_wird_nicht_geraten() -> None:
    assert source_state({}) == SOURCE_STATE_UNKNOWN
    assert source_state({"gap": None}) == SOURCE_STATE_UNKNOWN


def test_vier_tage_stille_bleiben_source_current() -> None:
    """Der reale Fall vom 2026-09-08 — und der Kern der Entscheidung.

    Vier Tage ohne Nachricht sind KEIN Ingestion-Fehler, solange kein Rueckstand
    besteht. Der Zustand bleibt SOURCE_CURRENT; das Alter daneben traegt die
    eigentliche Aussage. Genau deshalb steht in ``source_state`` keine
    Schweige-Schwelle: ab wann ein Anbieter zu lange schweigt, ist eine
    Bewertung ueber den Anbieter, keine Eigenschaft der Messung.
    """
    daten = {
        "gap": 0,
        "source_silence_age_s": 4 * 24 * 3600,
        "last_source_message_at": "2026-09-04T13:02:00+00:00",
        "last_ingested_message_at": "2026-09-04T13:02:05+00:00",
    }

    assert source_state(daten) == SOURCE_STATE_CURRENT

    text = render_source_state(daten)
    assert "SOURCE_CURRENT" in text
    assert "source_silence=96.0h" in text, text
    assert "last_source=2026-09-04" in text


def test_meldung_zeigt_beide_zeitpunkte_getrennt() -> None:
    """Fallen sie auseinander, liegt es an uns — das muss ablesbar sein."""
    text = render_source_state(
        {
            "gap": 5,
            "source_silence_age_s": 600,
            "last_source_message_at": "2026-09-08T10:00:00+00:00",
            "last_ingested_message_at": "2026-09-08T08:00:00+00:00",
        }
    )

    assert "INGESTION_BEHIND" in text
    assert "last_source=2026-09-08T10:00:00" in text
    assert "last_ingested=2026-09-08T08:00:00" in text


# ── Die Fortschreibung im Canary ─────────────────────────────────────────────


def test_stille_altert_ueber_neustarts_hinweg(tmp_path: Path) -> None:
    """Der Kernpunkt: ein Zustand, den jeder Restart loescht, belegt nichts.

    Nach einem Neustart ist ``messages_since_boot`` wieder 0. Wuerden die beiden
    Zeitpunkte daraus abgeleitet, koennte KAI nach jedem Restart nicht mehr
    sagen, wann zuletzt etwas ankam — und damit Stille ueber Tage nicht belegen.
    """
    pfad = tmp_path / "canary.json"

    # Erster Lauf: eine Nachricht kommt an.
    _write_semantic_canary(
        path=pfad,
        chat_id=-100123,
        checkpoint_message_id=23961,
        latest_message_id=23961,
        replay_processed=1,
        last_source_message_at="2026-09-04T13:02:00+00:00",
    )
    erst = json.loads(pfad.read_text(encoding="utf-8"))
    assert erst["last_ingested_message_at"] is not None
    assert erst["last_source_message_at"] == "2026-09-04T13:02:00+00:00"

    # Spaeterer Lauf, nichts Neues, kein Zeitstempel von der Quelle uebergeben
    # (etwa weil der Abruf gerade nichts lieferte): beide Werte muessen stehen
    # bleiben, statt auf None zurueckzufallen.
    _write_semantic_canary(
        path=pfad,
        chat_id=-100123,
        checkpoint_message_id=23961,
        latest_message_id=23961,
        replay_processed=0,
    )
    zweit = json.loads(pfad.read_text(encoding="utf-8"))

    assert zweit["last_source_message_at"] == erst["last_source_message_at"]
    assert zweit["last_ingested_message_at"] == erst["last_ingested_message_at"]
    assert zweit["source_silence_age_s"] > 0


def test_bewegter_checkpoint_setzt_die_ingestion_zeit_neu(tmp_path: Path) -> None:
    """Verarbeitet heisst: Replay lief ODER der Checkpoint ist gewandert."""
    pfad = tmp_path / "canary.json"
    alt = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    pfad.write_text(
        json.dumps(
            {
                "checkpoint_message_id": 23961,
                "last_source_message_at": alt,
                "last_ingested_message_at": alt,
            }
        ),
        encoding="utf-8",
    )

    _write_semantic_canary(
        path=pfad,
        chat_id=-100123,
        checkpoint_message_id=23962,  # gewandert
        latest_message_id=23962,
        replay_processed=0,  # ohne Replay
        last_source_message_at=datetime.now(UTC).isoformat(),
    )
    daten = json.loads(pfad.read_text(encoding="utf-8"))

    assert daten["last_ingested_message_at"] != alt, "der Fortschritt muss sichtbar werden"
    assert daten["source_silence_age_s"] < 60


def test_kaputte_vorgaengerdatei_bricht_nichts(tmp_path: Path) -> None:
    """Der Canary-Writer laeuft im Listener — er darf nie das Sammeln stoppen."""
    pfad = tmp_path / "canary.json"
    pfad.write_text("{kein json", encoding="utf-8")

    _write_semantic_canary(
        path=pfad,
        chat_id=-100123,
        checkpoint_message_id=1,
        latest_message_id=1,
        replay_processed=0,
    )

    daten = json.loads(pfad.read_text(encoding="utf-8"))
    assert daten["gap"] == 0
    assert daten["last_source_message_at"] is None
