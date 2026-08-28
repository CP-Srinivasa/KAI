"""Die Transkript-Abdeckungswache — Logik UND die Naht zur Datenbank.

Der Vorfall, gegen den diese Tests stehen: die YouTube-Pipeline lieferte vier
Monate lang Videos ohne Transkript (0 von 2584), weil ``fetch_transcript`` seinen
eigenen ``AttributeError`` abfing und ``None`` zurueckgab. Ankunft gruen, Inhalt
leer, kein Log.

Zwei Ebenen, bewusst getrennt:

* die reine Urteilslogik (``classify_coverage``) — ohne DB, ohne Uhr,
* die **Naht** ``_check_youtube_transcript_coverage`` gegen eine echte SQLite-Datei.

Die Naht ist die Ebene, an der es zweimal geknallt hat (#726, #748): reine
Funktionen waren getestet, der Uebergang zum echten Zustand nicht. Damit die
synthetische Tabelle hier nicht ihrerseits luegt, pinnt
``test_query_columns_exist_in_the_real_schema`` die benutzten Spalten gegen das
echte Modell.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.alerts.health_check import _check_youtube_transcript_coverage
from app.alerts.youtube_transcript_coverage import (
    COVERAGE_WINDOW_HOURS,
    TRANSCRIPT_MIN_CHARS,
    ChannelCoverage,
    classify_coverage,
    render_message,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

# Am 2026-08-28 auf dem Pi gemessen: 2584 Beschreibungen, laengste 143 Zeichen.
DESCRIPTION = "x" * 143
# Kuerzestes echtes Transkript derselben Stichprobe.
SHORTEST_REAL_TRANSCRIPT = "y" * 315


def _meta(text_source: str | None) -> str | None:
    """``youtube_meta`` so, wie SQLAlchemy die JSON-Spalte schreibt."""
    return None if text_source is None else json.dumps({"text_source": text_source})


def _db(
    tmp_path: Path,
    rows: list[tuple[str, str, str, datetime]],
    *,
    text_source: str | None = None,
) -> Path:
    """Eine minimale, aber echte SQLite-Datei — kein Mock der Datenbank.

    ``text_source=None`` erzeugt Altzeilen ohne ``youtube_meta``; dort greift die
    Laengen-Heuristik. Mit gesetztem Wert greift das explizite Signal.
    """
    path = tmp_path / "kai.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE canonical_documents ("
        "author TEXT, raw_text TEXT, source_type TEXT, fetched_at TEXT, youtube_meta TEXT)"
    )
    con.executemany(
        "INSERT INTO canonical_documents VALUES (?, ?, ?, ?, ?)",
        [
            (a, t, st, ts.replace(tzinfo=None).isoformat(sep=" "), _meta(text_source))
            for a, t, st, ts in rows
        ],
    )
    con.commit()
    con.close()
    return path


def _url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


# ── Der Trenner ──────────────────────────────────────────────────────────


def test_threshold_sits_between_measured_description_and_measured_transcript() -> None:
    """Die Schwelle ist gemessen, nicht gesetzt — dieser Test haelt die Messung fest."""
    assert len(DESCRIPTION) < TRANSCRIPT_MIN_CHARS < len(SHORTEST_REAL_TRANSCRIPT)


# ── Reine Urteilslogik ───────────────────────────────────────────────────


def test_no_documents_is_not_a_finding() -> None:
    """Eine ruhige Nacht ohne neue Uploads ist kein Ausfall."""
    verdict = classify_coverage([])

    assert verdict.status == "no_population"
    assert verdict.is_healthy
    assert verdict.ratio is None


def test_every_channel_dry_is_the_historical_outage() -> None:
    verdict = classify_coverage(
        [ChannelCoverage("Bankless", 4, 0), ChannelCoverage("Coin Bureau", 3, 0)]
    )

    assert verdict.status == "blackout"
    assert not verdict.is_healthy
    assert verdict.ratio == 0.0
    # Kein Kanal liefert ⇒ es gibt keine Vergleichsgruppe, das ist der Unterschied
    # zum Sprach-Artefakt.
    assert verdict.ratio_excluding_dry is None


def test_dry_channels_next_to_healthy_ones_are_a_language_artifact() -> None:
    """Der gemessene Normalfall: zwei nicht-englische Kanaele liefern legitim nichts."""
    verdict = classify_coverage(
        [
            ChannelCoverage("Crypto Banter", 4, 4),
            ChannelCoverage("Bankless", 3, 3),
            ChannelCoverage("Trader sanju", 3, 0),
            ChannelCoverage("DAY TRADER telugu", 2, 0),
        ]
    )

    assert verdict.status == "ok"
    assert verdict.ratio == pytest.approx(7 / 12)
    # Leave-one-out: ohne die trockenen Kanaele ist die Abdeckung vollstaendig.
    assert verdict.ratio_excluding_dry == 1.0
    assert [c.channel for c in verdict.dry_channels] == ["Trader sanju", "DAY TRADER telugu"]


def test_small_sample_below_the_ratio_stays_quiet() -> None:
    """Unter der Mindest-Stichprobe ist eine Quote nicht aussagekraeftig."""
    verdict = classify_coverage([ChannelCoverage("Bankless", 4, 1)])

    assert verdict.ratio == 0.25
    assert verdict.status == "ok"


def test_large_sample_below_the_ratio_is_reported() -> None:
    verdict = classify_coverage(
        [ChannelCoverage("Bankless", 12, 3), ChannelCoverage("Coin Bureau", 8, 1)]
    )

    assert verdict.status == "low"
    assert not verdict.is_healthy


def test_blackout_needs_more_than_a_single_video() -> None:
    """Ein einzelnes Video ohne Transkript ist Alltag, kein Ausfall."""
    assert classify_coverage([ChannelCoverage("Bankless", 1, 0)]).status == "ok"
    assert classify_coverage([ChannelCoverage("Bankless", 3, 0)]).status == "blackout"


# ── Der Meldetext traegt die Zerlegung, nicht nur das Aggregat ───────────


def test_message_carries_aggregate_and_decomposition() -> None:
    verdict = classify_coverage(
        [
            ChannelCoverage("Crypto Banter", 12, 2),
            ChannelCoverage("Trader sanju", 8, 0),
        ]
    )
    message = render_message(verdict)

    assert "2/20" in message
    assert "10%" in message
    assert "Crypto Banter 2/12" in message  # Untergruppe
    assert "trocken: Trader sanju" in message  # Konzentration
    assert "ohne diese 17%" in message  # leave-one-out


def test_blackout_message_names_the_suspect_not_the_language() -> None:
    message = render_message(classify_coverage([ChannelCoverage("Bankless", 5, 0)]))

    assert "KEIN einziger Kanal liefert" in message
    assert "youtube-transcript-api" in message


# ── Die Naht: echte SQLite-Datei, injizierte Uhr ─────────────────────────


def test_seam_reproduces_the_incident_descriptions_only(tmp_path: Path) -> None:
    """Genau der Zustand vom 2026-08-27: Videos da, Text nur die Beschreibung."""
    db = _db(
        tmp_path,
        [
            ("Bankless", DESCRIPTION, "youtube_channel", NOW - timedelta(hours=2)),
            ("Coin Bureau", DESCRIPTION, "youtube_channel", NOW - timedelta(hours=3)),
            ("CryptosRUs", DESCRIPTION, "youtube_channel", NOW - timedelta(hours=4)),
        ],
    )

    issues = _check_youtube_transcript_coverage(_url(db), NOW)

    assert len(issues) == 1
    assert issues[0].component == "youtube_transcript_coverage"
    assert issues[0].severity == "warning"
    assert "0/3" in issues[0].message


def test_seam_is_quiet_once_transcripts_arrive(tmp_path: Path) -> None:
    db = _db(
        tmp_path,
        [
            ("Bankless", SHORTEST_REAL_TRANSCRIPT, "youtube_channel", NOW - timedelta(hours=1)),
            ("Coin Bureau", "z" * 12000, "youtube_channel", NOW - timedelta(hours=2)),
            ("CryptosRUs", "z" * 9000, "youtube_channel", NOW - timedelta(hours=3)),
        ],
    )

    assert _check_youtube_transcript_coverage(_url(db), NOW) == []


def test_seam_ignores_documents_outside_the_window(tmp_path: Path) -> None:
    """Der alte Ausfall darf nicht ewig nachhallen — sonst meldet die Wache Geschichte."""
    db = _db(
        tmp_path,
        [
            ("Bankless", DESCRIPTION, "youtube_channel", NOW - timedelta(hours=200)),
            ("Coin Bureau", DESCRIPTION, "youtube_channel", NOW - timedelta(hours=100)),
            (
                "CryptosRUs",
                DESCRIPTION,
                "youtube_channel",
                NOW - timedelta(hours=COVERAGE_WINDOW_HOURS + 1),
            ),
        ],
    )

    assert _check_youtube_transcript_coverage(_url(db), NOW) == []


def test_seam_ignores_non_youtube_documents(tmp_path: Path) -> None:
    """RSS-Artikel sind kurz und haetten die Quote sonst grundlos gedrueckt."""
    db = _db(
        tmp_path,
        [
            ("Some Feed", "kurz", "rss_feed", NOW - timedelta(hours=1)),
            ("Other Feed", "auch kurz", "rss_feed", NOW - timedelta(hours=2)),
            ("Third Feed", "ebenfalls", "rss_feed", NOW - timedelta(hours=3)),
        ],
    )

    assert _check_youtube_transcript_coverage(_url(db), NOW) == []


def test_seam_is_silent_without_a_database(tmp_path: Path) -> None:
    """Frischer Checkout ohne DB ist kein Systembefund."""
    assert _check_youtube_transcript_coverage(_url(tmp_path / "fehlt.db"), NOW) == []


def test_seam_is_silent_on_non_sqlite_deployments() -> None:
    """Dokumentierte Abdeckungsgrenze: nicht raten, sondern schweigen."""
    assert _check_youtube_transcript_coverage("postgresql://host/db", NOW) == []


def test_seam_reports_an_unreadable_table_instead_of_crashing(tmp_path: Path) -> None:
    broken = tmp_path / "kai.db"
    broken.write_bytes(b"kein sqlite")

    issues = _check_youtube_transcript_coverage(_url(broken), NOW)

    assert len(issues) == 1
    assert "unbelegbar" in issues[0].message


def test_query_columns_exist_in_the_real_schema() -> None:
    """Haelt die synthetische Fixture ehrlich: die Spalten muessen es wirklich geben.

    Ohne diesen Test koennte die Tabelle oben von der echten abweichen und alle
    Nahttests waeren gruen, waehrend die Abfrage auf dem Pi scheitert.
    """
    from app.storage.models.document import CanonicalDocumentModel

    columns = set(CanonicalDocumentModel.__table__.columns.keys())

    assert {"author", "raw_text", "source_type", "fetched_at", "youtube_meta"} <= columns


def test_probe_is_wired_into_the_health_report() -> None:
    """Eine Sonde, die niemand aufruft, ist keine Wache (#791: 23/56 verdrahtet)."""
    import inspect

    from app.alerts import health_check

    source = inspect.getsource(health_check.run_health_check_report)

    assert "_check_youtube_transcript_coverage(" in source


# ── Herkunfts-Signal schlaegt Laenge (Kopplung zum Atom-Feed-Umbau) ──────


def test_long_description_is_not_mistaken_for_a_transcript(tmp_path: Path) -> None:
    """Die Regression, die der Feed-Umbau sonst genau hier ausgeloest haette.

    Der Atom-Feed liefert volle Beschreibungen (~1400 Zeichen). Ohne das
    ``text_source``-Feld haette die Laengen-Heuristik sie als Transkript gezaehlt
    und die Wache waere gruen geworden, waehrend nichts ankommt.
    """
    long_description = "x" * 1400
    db = _db(
        tmp_path,
        [
            ("Bankless", long_description, "youtube_channel", NOW - timedelta(hours=1)),
            ("Coin Bureau", long_description, "youtube_channel", NOW - timedelta(hours=2)),
            ("CryptosRUs", long_description, "youtube_channel", NOW - timedelta(hours=3)),
        ],
        text_source="description",
    )

    issues = _check_youtube_transcript_coverage(_url(db), NOW)

    assert len(issues) == 1
    assert "0/3" in issues[0].message


def test_short_transcript_is_counted_when_the_source_says_so(tmp_path: Path) -> None:
    """Umgekehrt: ein kurzes Transkript zaehlt, auch wenn die Laenge dagegen spraeche."""
    db = _db(
        tmp_path,
        [
            ("Bankless", "kurz, aber echt", "youtube_channel", NOW - timedelta(hours=1)),
            ("Coin Bureau", "auch kurz", "youtube_channel", NOW - timedelta(hours=2)),
            ("CryptosRUs", "ebenfalls", "youtube_channel", NOW - timedelta(hours=3)),
        ],
        text_source="transcript",
    )

    assert _check_youtube_transcript_coverage(_url(db), NOW) == []


def test_legacy_rows_without_the_field_still_use_the_length_heuristic(tmp_path: Path) -> None:
    """Altbestand hat kein ``youtube_meta`` — dort bleibt die Laenge das Beste, was da ist."""
    db = _db(
        tmp_path,
        [
            ("Bankless", SHORTEST_REAL_TRANSCRIPT, "youtube_channel", NOW - timedelta(hours=1)),
            ("Coin Bureau", "z" * 9000, "youtube_channel", NOW - timedelta(hours=2)),
            ("CryptosRUs", "z" * 8000, "youtube_channel", NOW - timedelta(hours=3)),
        ],
    )

    assert _check_youtube_transcript_coverage(_url(db), NOW) == []
