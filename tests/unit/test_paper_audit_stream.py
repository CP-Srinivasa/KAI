"""Ein Port für den Paper-Execution-Audit-Stream.

Befund 2026-08-17: 90 Dateien referenzieren ``paper_execution_audit.jsonl``,
50 lesen ihn mit eigenem ``open()``/``json.loads``. Zwei davon —
``observability/churn_report.py`` und ``observability/edge_report.py`` — tragen
sogar dieselbe Funktion unter demselben Namen ``load_audit_events``, Zeile für
Zeile identisch bis auf das Log-Präfix.

Ein Bus ohne Port: jede Lesestelle darf eigene Annahmen über Kodierung,
kaputte Zeilen und Leerzeilen treffen, und keine davon ist an einer Stelle
korrigierbar.

Beide Altfassungen verwerfen defekte Zeilen **still** (churn_report ohne jedes
Log, edge_report mit einer Warnung PRO Zeile — bei einer kaputten Datei also
Tausende). Der Port zählt sie stattdessen und meldet sie EINMAL mit Anzahl:
ein stiller Verlust in einem Evidenz-Stream ist genau die Sorte Lücke, gegen
die die Truth-Schicht gebaut ist.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.execution.paper_audit_stream import (
    iter_audit_events,
    load_audit_events,
    read_audit_stream,
)


def _write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_reads_well_formed_events(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "a.jsonl",
        '{"event_type": "order_filled", "document_id": "d1"}',
        '{"event_type": "position_closed", "document_id": "d1"}',
    )

    events = load_audit_events(p)

    assert [e["event_type"] for e in events] == ["order_filled", "position_closed"]


def test_missing_file_is_empty_not_an_error(tmp_path: Path) -> None:
    """Ein fehlender Stream ist ein Normalzustand (frischer Host), kein Crash."""
    events = load_audit_events(tmp_path / "nope.jsonl")

    assert events == []


def test_blank_lines_are_not_counted_as_damage(tmp_path: Path) -> None:
    p = _write(tmp_path / "a.jsonl", '{"event_type": "x"}', "", "   ", '{"event_type": "y"}')

    result = read_audit_stream(p)

    assert len(result.events) == 2
    assert result.skipped == 0


def test_corrupt_lines_are_counted_not_swallowed(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "a.jsonl",
        '{"event_type": "ok"}',
        "{not json",
        '{"event_type": "ok2"}',
        "]]broken[[",
    )

    result = read_audit_stream(p)

    assert len(result.events) == 2
    assert result.skipped == 2
    assert result.total_lines == 4


def test_non_object_rows_are_damage_too(tmp_path: Path) -> None:
    """Ein nacktes Array/Skalar ist gueltiges JSON, aber kein Audit-Event."""
    p = _write(tmp_path / "a.jsonl", '{"event_type": "ok"}', "[1, 2, 3]", '"just a string"', "42")

    result = read_audit_stream(p)

    assert len(result.events) == 1
    assert result.skipped == 3


def test_damage_is_logged_once_with_a_count(tmp_path: Path, caplog) -> None:
    """Eine Warnung PRO kaputter Zeile macht aus einem Defekt einen Log-Sturm."""
    p = _write(tmp_path / "a.jsonl", *(["{broken"] * 50), '{"event_type": "ok"}')

    with caplog.at_level(logging.WARNING):
        result = read_audit_stream(p, source="test")

    assert result.skipped == 50
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "50" in warnings[0].getMessage()


def test_clean_stream_logs_nothing(tmp_path: Path, caplog) -> None:
    p = _write(tmp_path / "a.jsonl", '{"event_type": "ok"}')

    with caplog.at_level(logging.WARNING):
        read_audit_stream(p)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_iter_streams_without_materialising(tmp_path: Path) -> None:
    """Der Stream waechst (4,5 MB / 6585 Zeilen am 2026-08-17) — Generator noetig."""
    p = _write(tmp_path / "a.jsonl", *[f'{{"i": {i}}}' for i in range(100)])

    it = iter_audit_events(p)

    assert next(it)["i"] == 0
    assert next(it)["i"] == 1
    assert sum(1 for _ in it) == 98


def test_iter_on_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_audit_events(tmp_path / "nope.jsonl")) == []


def test_undecodable_bytes_do_not_kill_the_read(tmp_path: Path) -> None:
    """Ein einzelnes kaputtes Byte darf nicht den ganzen Stream verlieren."""
    p = tmp_path / "a.jsonl"
    p.write_bytes(b'{"event_type": "ok"}\n\xff\xfe garbage\n{"event_type": "ok2"}\n')

    result = read_audit_stream(p)

    assert len(result.events) == 2
    assert result.skipped == 1
