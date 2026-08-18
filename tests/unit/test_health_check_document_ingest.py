"""Der Dokumenten-Eingang (RSS/OKX/NewsData) stand in keiner Wachliste.

Die Freshness-Liste bewacht Dateien. RSS, OKX-Announcements und NewsData
schreiben aber nicht in eine Datei — sie schreiben nach ``canonical_documents``.
Fuer den Waechter existierte dieser Eingang deshalb gar nicht, und ein
stillgelegter Ingest haette exakt so ausgesehen wie ein ruhiger Nachrichtentag:
alle Ausgaenge frisch, alle Timer gruen, nichts kommt mehr herein. Genau diese
Verwechslung war der TV-Ingest-Ausfall vom 02.–08.08.

Kadenz live gemessen (Pi, 18.08., 2 Tage, 337 Fetch-Minuten): groesster Abstand
**31 min**. Die Schwelle von 240 min ist damit rund das Achtfache der real
beobachteten Stille — spaet, aber nicht flatternd.

Der zweite neue Eingang ist der Binance-Liquidations-Websocket. Er schreibt
``artifacts/liquidation_stream_heartbeat.txt`` alle <=15 s, ausdruecklich um
"ruhiger Markt" von "Feed tot" zu trennen — nur schaute niemand hin.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.health_check import (
    DOCUMENT_INGEST_MAX_AGE_MIN,
    _check_document_ingest,
)


def _db(tmp_path: Path, newest: datetime | None) -> Path:
    path = tmp_path / "dev.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE canonical_documents (id INTEGER PRIMARY KEY, fetched_at TEXT)")
    if newest is not None:
        con.execute(
            "INSERT INTO canonical_documents (fetched_at) VALUES (?)",
            (newest.strftime("%Y-%m-%d %H:%M:%S.%f"),),
        )
    con.commit()
    con.close()
    return path


def test_frischer_ingest_erzeugt_keinen_befund(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 8, 20, tzinfo=UTC)
    db = _db(tmp_path, now - timedelta(minutes=5))
    assert _check_document_ingest(f"sqlite+aiosqlite:///{db.as_posix()}", now) == []


def test_toter_ingest_ist_ein_befund(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 8, 20, tzinfo=UTC)
    db = _db(tmp_path, now - timedelta(minutes=DOCUMENT_INGEST_MAX_AGE_MIN + 1))
    issues = _check_document_ingest(f"sqlite+aiosqlite:///{db.as_posix()}", now)
    assert len(issues) == 1
    assert issues[0].component == "document_ingest"
    assert issues[0].severity == "warning"
    assert "Quelle" in issues[0].message or "source" in issues[0].message.lower()


def test_leere_tabelle_ist_ein_befund(tmp_path: Path) -> None:
    """Kein einziges Dokument heisst nicht 'alles ruhig'."""
    now = datetime(2026, 8, 18, 8, 20, tzinfo=UTC)
    db = _db(tmp_path, None)
    issues = _check_document_ingest(f"sqlite+aiosqlite:///{db.as_posix()}", now)
    assert len(issues) == 1
    assert issues[0].component == "document_ingest"


def test_fehlende_db_ist_kein_fehlalarm(tmp_path: Path) -> None:
    """Frischer Checkout hat noch keine DB — das ist kein Systembefund."""
    now = datetime(2026, 8, 18, 8, 20, tzinfo=UTC)
    missing = tmp_path / "nope.db"
    assert _check_document_ingest(f"sqlite+aiosqlite:///{missing.as_posix()}", now) == []


def test_nicht_sqlite_wird_nicht_geraten(tmp_path: Path) -> None:
    """Postgres-Deployment: die Sonde schweigt, statt etwas zu behaupten."""
    now = datetime(2026, 8, 18, 8, 20, tzinfo=UTC)
    assert _check_document_ingest("postgresql+asyncpg://u:p@h:5432/db", now) == []


def test_vorhandene_db_ohne_tabelle_meldet_sich(tmp_path: Path) -> None:
    """Eine DB, die es gibt, aber nicht lesbar ist, ist eine Anomalie."""
    now = datetime(2026, 8, 18, 8, 20, tzinfo=UTC)
    path = tmp_path / "dev.db"
    sqlite3.connect(path).close()
    issues = _check_document_ingest(f"sqlite+aiosqlite:///{path.as_posix()}", now)
    assert len(issues) == 1
    assert issues[0].component == "document_ingest"


def test_liquidations_heartbeat_steht_in_der_wachliste() -> None:
    """Der Binance-Liquidations-WS ist ein Eingang und wird als solcher gefuehrt."""
    from app.alerts.health_check import _FRESHNESS_PER_FILE_MIN, _INGRESS_COMPONENTS

    assert "liquidation_stream_heartbeat.txt" in _FRESHNESS_PER_FILE_MIN
    assert "liquidation_ingress" in _INGRESS_COMPONENTS
    assert "document_ingest" in _INGRESS_COMPONENTS
