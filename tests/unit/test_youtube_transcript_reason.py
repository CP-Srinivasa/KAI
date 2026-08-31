"""Ein fehlgeschlagener Transkript-Abruf muss einen GRUND hinterlassen.

Befund 2026-08-31, live auf dem Pi: seit dem 28.08. meldet der Health-Check
alle 15 Minuten „0/12 Videos tragen einen Transkript-Text — KEIN einziger
Kanal liefert; fetch_transcript gegen die installierte youtube-transcript-api
pruefen". Diese Anweisung ist erledigt und widerlegt: die API-Bindung wurde am
27./28.08. repariert (#792), der Vertragstest daneben ist gruen.

Warum die Transkripte trotzdem fehlen, konnte niemand sagen — weil es
**nirgends steht**. ``fetch_transcript`` hat drei Ausgaenge, und zwei davon
schreiben nichts:

* ``TranscriptsDisabled`` / ``NoTranscriptFound``  -> ``return None``, still
* ``find_generated_transcript`` ohne Treffer       -> ``return None``, still
* jede andere Exception                            -> eine Log-Zeile

Im Journal seit dem 30.08.: **keine einzige** ``transcript_error``-Zeile bei
0/12 Transkripten. Es lief also die stille Strasse — und die einzige Art, den
Grund zu erfahren, waere erneutes Anfragen bei YouTube gewesen. Genau das hat
am 28.08. den IP-Block ausgeloest (``kai_youtube_cost_ladder_and_ip_block``).
Ein Waechter, dessen Diagnose eine neue Messung erzwingt, die das gemessene
System beschaedigt, ist kein Waechter.

Der Grund wandert deshalb auf das Dokument: ``youtube_meta.transcript_status``.
Danach ist die naechste Diagnose eine DB-Abfrage, kein Netzaufruf.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ingestion.youtube import adapter
from app.ingestion.youtube.adapter import (
    TRANSCRIPT_STATUS_DISABLED,
    TRANSCRIPT_STATUS_NONE_FOUND,
    TRANSCRIPT_STATUS_OK,
    fetch_transcript,
    fetch_transcript_with_reason,
)


class _Snippet:
    def __init__(self, text: str) -> None:
        self.text = text


class _Transcript:
    def __init__(self, parts: list[str]) -> None:
        self._parts = parts

    def fetch(self) -> list[_Snippet]:
        return [_Snippet(p) for p in self._parts]


class _List:
    """Minimale Nachbildung von ``TranscriptList`` — nur die zwei Suchwege."""

    def __init__(self, *, found: _Transcript | None = None, generated: _Transcript | None = None):
        self._found = found
        self._generated = generated

    def find_transcript(self, languages: list[str]) -> _Transcript:
        from youtube_transcript_api import NoTranscriptFound

        if self._found is None:
            raise NoTranscriptFound("vid", languages, [])
        return self._found

    def find_generated_transcript(self, languages: list[str]) -> _Transcript:
        from youtube_transcript_api import NoTranscriptFound

        if self._generated is None:
            raise NoTranscriptFound("vid", languages, [])
        return self._generated


def _patch_api(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> None:
    class _Api:
        def list(self, video_id: str) -> Any:
            if isinstance(behaviour, Exception):
                raise behaviour
            return behaviour

    monkeypatch.setattr(adapter, "YouTubeTranscriptApi", _Api)


def test_a_found_transcript_reports_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_api(monkeypatch, _List(found=_Transcript(["hallo", "welt"])))

    text, status = fetch_transcript_with_reason("vid")

    assert text == "hallo welt"
    assert status == TRANSCRIPT_STATUS_OK


def test_disabled_transcripts_leave_a_reason_not_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    from youtube_transcript_api import TranscriptsDisabled

    _patch_api(monkeypatch, TranscriptsDisabled("vid"))

    text, status = fetch_transcript_with_reason("vid")

    assert text is None
    assert status == TRANSCRIPT_STATUS_DISABLED


def test_no_transcript_in_any_language_leaves_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Weg, der live gelaufen sein muss: kein Log, kein Grund, nur None."""
    _patch_api(monkeypatch, _List(found=None, generated=None))

    text, status = fetch_transcript_with_reason("vid")

    assert text is None
    assert status == TRANSCRIPT_STATUS_NONE_FOUND


def test_an_unexpected_error_carries_its_type_into_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein IP-Block darf nicht als 'kein Transkript vorhanden' erscheinen."""

    # Name absichtlich ohne Error-Suffix: so heisst die Ausnahme in
    # youtube-transcript-api wirklich, und genau dieser Name muss im
    # Status landen, damit ein Block nicht wie 'kein Transkript' aussieht.
    class IpBlocked(RuntimeError):  # noqa: N818
        pass

    _patch_api(monkeypatch, IpBlocked("YouTube is blocking requests from your IP"))

    text, status = fetch_transcript_with_reason("vid")

    assert text is None
    assert status.startswith("error:")
    assert "IpBlocked" in status


def test_the_old_signature_still_returns_just_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch_transcript`` bleibt der duenne Wrapper — keine Aufrufer brechen."""
    _patch_api(monkeypatch, _List(found=_Transcript(["a", "b"])))
    assert fetch_transcript("vid") == "a b"

    _patch_api(monkeypatch, _List(found=None, generated=None))
    assert fetch_transcript("vid") is None


def test_the_document_carries_the_reason_so_the_next_diagnosis_is_a_query() -> None:
    from app.ingestion.youtube.adapter import YouTubeVideo, _video_to_document

    video = YouTubeVideo(
        video_id="vid",
        title="t",
        description="kurze Beschreibung",
        published_at="2026-08-31T10:00:00Z",
        channel_id="c",
        channel_title="Kanal",
    )

    doc = _video_to_document(
        video, None, "src", "YouTube", transcript_status=TRANSCRIPT_STATUS_DISABLED
    )

    assert doc.youtube_meta is not None
    assert doc.youtube_meta.text_source == "description"
    assert doc.youtube_meta.transcript_status == TRANSCRIPT_STATUS_DISABLED


def test_a_successful_transcript_is_marked_ok_on_the_document() -> None:
    from app.ingestion.youtube.adapter import YouTubeVideo, _video_to_document

    video = YouTubeVideo(
        video_id="vid",
        title="t",
        description="d",
        published_at="2026-08-31T10:00:00Z",
        channel_id="c",
        channel_title="Kanal",
    )

    doc = _video_to_document(
        video, "ein echtes Transkript", "src", "YouTube", transcript_status=TRANSCRIPT_STATUS_OK
    )

    assert doc.youtube_meta is not None
    assert doc.youtube_meta.text_source == "transcript"
    assert doc.youtube_meta.transcript_status == TRANSCRIPT_STATUS_OK
