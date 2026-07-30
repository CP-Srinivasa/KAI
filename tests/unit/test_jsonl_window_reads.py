"""Fenster-Reads auf zeitsortierten append-only JSONL-Dateien.

Befund 2026-07-30: `build_priority_gate_summary` lud alle **88.314** Zeilen von
`trading_loop_audit.jsonl` (54,8 MB, zurück bis 22.03.), um die **1.203** Zeilen
der letzten 24 h zu zählen — 1,36 %, also ~73x Verschwendung, und das Verhältnis
verschlechtert sich täglich, weil das Fenster fix ist und die Datei ewig wächst.

Rotation ist hier NICHT das Werkzeug: die Datei speist Edge-/Verdikt-Rechnungen
(21 Code-Referenzen), Löschen zerstört Evidenz. Richtig ist, nur den benötigten
Tail zu lesen.

Der Reader muss VOLLSTÄNDIG sein: ein fixes Zeilen-Limit würde bei einer
Frequenz-Spitze still abschneiden und einen truth-tragenden Zähler verfälschen.
Deshalb liest er rückwärts weiter, bis er eine MARGE an Einträgen vor dem Cutoff
gesehen hat — die Streams sind append-only, aber nicht streng monoton (live
gemessen: 143 Sortierverletzungen, max. 7 Zeilen Verschiebung).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.storage.jsonl_io import _WINDOW_CHUNK_BYTES, iter_jsonl_since


def _write(path: Path, rows: list[dict], *, newline_at_end: bool = True) -> None:
    body = "\n".join(json.dumps(r) for r in rows)
    if newline_at_end:
        body += "\n"
    open(path, "w", encoding="utf-8").write(body)


_BASE = datetime(2026, 7, 10, tzinfo=UTC)


def _rows(n: int) -> list[dict]:
    """n Zeilen mit aufsteigendem ISO-Zeitstempel (append-only-Semantik).

    Echte datetime-Arithmetik, kein handgebautes Format: bei manuell
    zusammengesetzten Datumsteilen läuft der Tag über 2 Stellen hinaus
    (``2026-07-218``) und die lexikografische Ordnung bricht — genau die
    Voraussetzung, auf der der Reader beruht.
    """
    return [{"started_at": (_BASE + timedelta(minutes=i)).isoformat(), "i": i} for i in range(n)]


def test_returns_only_records_at_or_after_cutoff(tmp_path: Path) -> None:
    """Genau das Fenster — nichts Älteres."""
    p = tmp_path / "a.jsonl"
    _write(p, _rows(48))

    got = list(
        iter_jsonl_since(p, since=(_BASE + timedelta(minutes=36)).isoformat(), key="started_at")
    )

    assert got, "Fenster darf nicht leer sein"
    assert [r["i"] for r in got] == list(range(36, 48))


def test_yields_in_chronological_order(tmp_path: Path) -> None:
    """Rückwärts gelesen, vorwärts geliefert — Aufrufer erwarten Zeitordnung."""
    p = tmp_path / "a.jsonl"
    _write(p, _rows(30))

    got = list(
        iter_jsonl_since(p, since=(_BASE + timedelta(minutes=10)).isoformat(), key="started_at")
    )

    assert [r["i"] for r in got] == sorted(r["i"] for r in got)


def test_window_is_complete_across_chunk_boundaries(tmp_path: Path) -> None:
    """Vollständigkeit auch wenn das Fenster viele Chunks überspannt.

    DAS ist die Regression, die zählt: ein fixes Limit oder ein zu kleiner Chunk
    würde hier still abschneiden und einen Zähler verfälschen.
    """
    p = tmp_path / "a.jsonl"
    rows = _rows(5000)
    _write(p, rows)
    cutoff = rows[1000]["started_at"]

    got = list(iter_jsonl_since(p, since=cutoff, key="started_at", _chunk_bytes=512))

    expected = [r["i"] for r in rows if r["started_at"] >= cutoff]
    assert [r["i"] for r in got] == expected


def test_local_disorder_does_not_truncate_the_window(tmp_path: Path) -> None:
    """Append-only heisst NICHT streng monoton — der Reader darf nicht zu früh stoppen.

    Auf dem Live-File gemessen (2026-07-30): 143 Sortierverletzungen bei 88.321
    Zeilen, max. 7 Zeilen Verschiebung, max. 9,3 s Rücksprung (parallele
    Appends). Ein Abbruch beim ERSTEN älteren Datensatz würde hier Einträge
    verlieren, die im File hinter einem verirrten alten stehen — ein stiller
    Unterzähler in einer truth-tragenden Metrik.
    """
    p = tmp_path / "a.jsonl"
    rows = _rows(2000)
    # Einen alten Datensatz mitten ins Fenster einschmuggeln (7 Zeilen versetzt,
    # wie live beobachtet), plus einen direkt an der Fenstergrenze.
    rows[1500], rows[1507] = rows[1507], rows[1500]
    _write(p, rows)
    cutoff = _rows(2000)[1400]["started_at"]

    got = list(iter_jsonl_since(p, since=cutoff, key="started_at"))

    expected = sorted(r["i"] for r in rows if r["started_at"] >= cutoff)
    assert sorted(r["i"] for r in got) == expected


def test_whole_file_when_everything_is_inside_window(tmp_path: Path) -> None:
    """Cutoff vor dem ersten Eintrag → alles, kein Verlust am Dateianfang."""
    p = tmp_path / "a.jsonl"
    _write(p, _rows(120))

    got = list(
        iter_jsonl_since(p, since="2000-01-01T00:00:00+00:00", key="started_at", _chunk_bytes=256)
    )

    assert len(got) == 120 and got[0]["i"] == 0


def test_empty_when_cutoff_after_last_record(tmp_path: Path) -> None:
    p = tmp_path / "a.jsonl"
    _write(p, _rows(10))

    assert list(iter_jsonl_since(p, since="2030-01-01T00:00:00+00:00", key="started_at")) == []


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert list(iter_jsonl_since(tmp_path / "nope.jsonl", since="x", key="started_at")) == []


def test_skips_corrupt_and_partial_lines(tmp_path: Path) -> None:
    """Eine kaputte Zeile darf den Read nie kippen (Policy wie iter_jsonl_tolerant)."""
    p = tmp_path / "a.jsonl"
    rows = _rows(6)
    body = "\n".join(json.dumps(r) for r in rows[:3])
    body += "\n{ das ist kein json\n"
    body += "\n".join(json.dumps(r) for r in rows[3:])
    body += "\n"
    open(p, "w", encoding="utf-8").write(body)

    got = list(iter_jsonl_since(p, since=_BASE.isoformat(), key="started_at"))

    assert [r["i"] for r in got] == [0, 1, 2, 3, 4, 5]


def test_tolerates_missing_final_newline(tmp_path: Path) -> None:
    """Letzte Zeile ohne \\n (racing appender) muss trotzdem ankommen."""
    p = tmp_path / "a.jsonl"
    _write(p, _rows(12), newline_at_end=False)

    got = list(
        iter_jsonl_since(p, since=(_BASE + timedelta(minutes=3)).isoformat(), key="started_at")
    )

    assert got[-1]["i"] == 11


def test_records_missing_the_key_are_kept(tmp_path: Path) -> None:
    """Ohne Zeitstempel wird nicht geraten — der Eintrag fliegt nicht still raus.

    Ein fehlendes Feld ist kein Beweis, dass der Eintrag alt ist; der Aufrufer
    filtert selbst (Legacy-Zeilen ohne started_at existieren im Bestand).
    """
    p = tmp_path / "a.jsonl"
    rows: list[dict] = [{"i": -1}, *_rows(5)]
    _write(p, rows)

    got = list(iter_jsonl_since(p, since=_BASE.isoformat(), key="started_at"))

    assert {r["i"] for r in got} >= {-1, 0, 4}


def test_non_dict_json_values_are_skipped(tmp_path: Path) -> None:
    p = tmp_path / "a.jsonl"
    open(p, "w", encoding="utf-8").write('[1,2]\n"str"\n' + json.dumps(_rows(1)[0]) + "\n")

    got = list(iter_jsonl_since(p, since=_BASE.isoformat(), key="started_at"))

    assert got == [_rows(1)[0]]


def test_reads_far_less_than_the_whole_file(tmp_path: Path) -> None:
    """Der eigentliche Zweck: I/O proportional zum FENSTER, nicht zur Datei.

    Belegt über die Anzahl gelesener Bytes; ohne diese Zusicherung ist der
    Reader nur eine umgeschriebene Variante des alten Full-Scans.
    """
    p = tmp_path / "a.jsonl"
    rows = _rows(100_000)  # ~5,7 MB — gross genug, dass ein Block klein dagegen ist
    _write(p, rows)
    total = p.stat().st_size
    cutoff = rows[-50]["started_at"]

    read_bytes = 0
    real_open = Path.open

    def counting_open(self, *a, **kw):  # noqa: ANN001, ANN202
        fh = real_open(self, *a, **kw)
        if self != p:
            return fh
        orig_read = fh.read

        def read(n=-1):  # noqa: ANN001, ANN202
            nonlocal read_bytes
            data = orig_read(n)
            read_bytes += len(data)
            return data

        fh.read = read  # type: ignore[method-assign]
        return fh

    Path.open = counting_open  # type: ignore[method-assign]
    try:
        got = list(iter_jsonl_since(p, since=cutoff, key="started_at"))
    finally:
        Path.open = real_open  # type: ignore[method-assign]

    assert len(got) == 50
    # Die tragende Zusicherung ist dateigrössen-UNABHÄNGIG: gelesen wird ein
    # kleines Vielfaches des Blocks, nicht ein Anteil der Datei. Sonst wäre der
    # Reader nur eine umgeschriebene Variante des alten Full-Scans.
    assert read_bytes <= 3 * _WINDOW_CHUNK_BYTES, f"las {read_bytes} Bytes"
    assert read_bytes < total * 0.20, f"las {read_bytes} von {total} Bytes"
