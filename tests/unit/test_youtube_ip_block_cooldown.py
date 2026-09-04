"""Wer gesperrt ist, hoert auf zu fragen.

Der Vorfall (gemessen 2026-09-04): 0/26 Videos der letzten 24 h trugen ein
Transkript, davon 21x ``error:IpBlocked``. Der Adapter rief fuer JEDES Video in
JEDEM Zyklus erneut bei YouTube an — und jede dieser Anfragen erneuerte genau
den Block, auf dessen Ablauf gewartet wurde. Der Ausfall hielt sich seit Tagen
selbst am Leben.

Der Kommentar an ``TRANSCRIPT_STATUS_ERROR_PREFIX`` sagte das Richtige schon
lange: „``error:IpBlocked`` ist eine voellig andere Handlung als ``none_found``
— die eine heisst warten." Gewartet wurde nur nie.

Diese Datei prueft die drei Eigenschaften, auf die es dabei ankommt: der Block
wird gesetzt, er verhindert den naechsten Netzaufruf, und er laeuft wieder ab.
Das Letzte ist nicht Beiwerk — ein Breaker, der nie oeffnet, ist ein Ausfall
mit besserer Presse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingestion.youtube import adapter


@pytest.fixture(autouse=True)
def _isolierter_zustand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Kein Test fasst den echten ``artifacts/``-Baum an."""
    ziel = tmp_path / "artifacts" / "youtube" / "ip_block.json"
    monkeypatch.setattr(adapter, "_block_state_path", lambda: ziel)
    return ziel


def test_ohne_zustand_ist_kein_block_aktiv() -> None:
    assert adapter.ip_block_active() is False


def test_ein_ip_block_setzt_die_sperrpause(_isolierter_zustand: Path) -> None:
    adapter._note_ip_block(now_s=1000.0)
    raw = json.loads(_isolierter_zustand.read_text(encoding="utf-8"))
    assert raw["blocked_at_s"] == 1000.0
    assert raw["cooldown_until_s"] == 1000.0 + adapter._BLOCK_COOLDOWN_S
    assert adapter.ip_block_active(now_s=1000.0) is True


def test_die_sperrpause_laeuft_ab(_isolierter_zustand: Path) -> None:
    """Ein Breaker, der nie wieder oeffnet, ist keine Erholung."""
    adapter._note_ip_block(now_s=1000.0)
    kurz_davor = 1000.0 + adapter._BLOCK_COOLDOWN_S - 1
    danach = 1000.0 + adapter._BLOCK_COOLDOWN_S + 1
    assert adapter.ip_block_active(now_s=kurz_davor) is True
    assert adapter.ip_block_active(now_s=danach) is False


def test_ein_kaputter_zustand_blockiert_nicht(_isolierter_zustand: Path) -> None:
    """Fail-OPEN, und das ist hier richtig.

    Ein unlesbarer Merker darf die Transkripte nicht dauerhaft abschalten — der
    Schaden waere groesser als der, den er verhindern soll. Der Block kommt
    ohnehin binnen einer Anfrage zurueck, falls er noch besteht.
    """
    _isolierter_zustand.parent.mkdir(parents=True, exist_ok=True)
    _isolierter_zustand.write_text("{kein json", encoding="utf-8")
    assert adapter.ip_block_active() is False


def test_bei_aktivem_block_wird_youtube_nicht_gefragt(
    _isolierter_zustand: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der eigentliche Punkt: kein Netzaufruf, solange die Pause laeuft."""
    gefragt: list[str] = []

    class _Api:
        def list(self, video_id: str):  # noqa: ANN202 - Testdouble
            gefragt.append(video_id)
            raise AssertionError("YouTube wurde trotz aktiver Sperrpause gefragt")

    monkeypatch.setattr(adapter, "YouTubeTranscriptApi", _Api)
    adapter._note_ip_block()

    text, status = adapter.fetch_transcript_with_reason("vid123")

    assert text is None
    assert status == adapter.TRANSCRIPT_STATUS_BLOCK_COOLDOWN
    assert gefragt == []


def test_ein_ip_block_aus_dem_abruf_setzt_die_pause(
    _isolierter_zustand: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Der Name ist der Vertrag: youtube-transcript-api heisst diese Ausnahme
    # genau so, und der Adapter erkennt sie ueber ihren Typnamen.
    class IpBlocked(Exception):  # noqa: N818
        pass

    class _Api:
        def list(self, video_id: str):  # noqa: ANN202 - Testdouble
            raise IpBlocked("YouTube is blocking requests from your IP")

    monkeypatch.setattr(adapter, "YouTubeTranscriptApi", _Api)
    assert adapter.ip_block_active() is False

    text, status = adapter.fetch_transcript_with_reason("vid123")

    assert text is None
    assert status == f"{adapter.TRANSCRIPT_STATUS_ERROR_PREFIX}IpBlocked"
    assert adapter.ip_block_active() is True, "der Block wurde gemeldet, aber nicht gemerkt"


def test_ein_gewoehnlicher_fehler_setzt_keine_pause(
    _isolierter_zustand: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nur IP-Sperren pausieren. Ein Timeout ist kein Grund, 6 h stillzustehen."""

    class _Api:
        def list(self, video_id: str):  # noqa: ANN202 - Testdouble
            raise TimeoutError("read timeout")

    monkeypatch.setattr(adapter, "YouTubeTranscriptApi", _Api)

    _, status = adapter.fetch_transcript_with_reason("vid123")

    assert status == f"{adapter.TRANSCRIPT_STATUS_ERROR_PREFIX}TimeoutError"
    assert adapter.ip_block_active() is False
