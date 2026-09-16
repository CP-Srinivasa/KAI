"""Episoden-Praezision je Signalpfad (Daily Review 2026-09-16, V7 Option B).

Die Trefferquote, nach der das Haus steuert, ist eine GEMEINSAME Zahl ueber alle
aufgeloesten Alerts. Gemessen am 16.09. auf dem Pi (``alert_outcomes.jsonl``, je
``document_id`` juengste Zeile, 3 998 aufgeloest):

    tv:                3 284  (82 %)  TradingView-Webhook, am Eligibility-Gate vorbei
    technical_paper_…    336          Technical Screener, umgeht die Narrativ-Gates
    <UUID>               378          Nachrichten, durch das Eligibility-Gate

Im Vier-Wochen-Fenster ab 19.08. bestand die gemeinsame Zahl zu ~99 % aus
TradingView (51 %), waehrend das Gate ausserhalb von TradingView nur 14
direktionale Alerts durchliess. Die gemeinsame Zahl verdeckte also, dass die
Qualitaet der Nachrichten-Pipeline praktisch ungemessen ist.

Die Aufteilung ist ADDITIV: die gemeinsame Zahl bleibt, und die Teile werden mit
exakt derselben Episoden-Regel gezaehlt (Precision-Sprachregel #579). Die
Zuordnung laeuft ueber die ``document_id``, die jeder Pfad selbst vergibt --
``tv:`` deckte sich am 16.09. 3 284 : 3 284 mit ``provenance.source``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.observability.outcome_dedupe_report import (
    SIGNAL_PATH_NEWS,
    SIGNAL_PATH_OTHER,
    SIGNAL_PATH_TECHNICAL,
    SIGNAL_PATH_TRADINGVIEW,
    build_episode_dedupe_report,
    build_episode_reports_by_path,
    format_path_precision_de,
    format_path_precision_en,
    signal_path_of,
)

UUID = "3f2c9a1e-7b4d-4c2a-9e1f-0a1b2c3d4e5f"
NOTE = "auto@4h: bullish BTC/USDT $100.00->$104.00 (+4.00% over 4.0h, thr=0.42%)"


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _pair(
    tmp: Path,
    doc: str,
    outcome: str,
    *,
    at: str,
    asset: str = "BTC/USDT",
    channel: str = "telegram",
) -> None:
    _write(
        tmp / "alert_outcomes.jsonl",
        [
            {
                "document_id": doc,
                "outcome": outcome,
                "annotated_at": at,
                "asset": asset,
                "note": NOTE,
            }
        ],
    )
    _write(
        tmp / "alert_audit.jsonl",
        [
            {
                "document_id": doc,
                "channel": channel,
                "dispatched_at": at,
                "sentiment_label": "bullish",
            }
        ],
    )


def _reports(tmp: Path):
    return build_episode_reports_by_path(
        audit_path=tmp / "alert_outcomes.jsonl",
        alert_audit_path=tmp / "alert_audit.jsonl",
    )


# ---------------------------------------------------------------------------
# Zuordnung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc_id", "path"),
    [
        ("tv:tvsig_a680027d3a0d4407", SIGNAL_PATH_TRADINGVIEW),
        (
            "technical_paper_ARBUSDT_tech-ARBUSDT-2026-09-16T06:13:42.000466+00:00",
            SIGNAL_PATH_TECHNICAL,
        ),
        (UUID, SIGNAL_PATH_NEWS),
        (UUID.upper(), SIGNAL_PATH_NEWS),
        ("premium:abc123", SIGNAL_PATH_OTHER),
        ("autonomous_generator_BTC_1", SIGNAL_PATH_OTHER),
        ("", SIGNAL_PATH_OTHER),
    ],
)
def test_pfad_folgt_der_document_id(doc_id: str, path: str) -> None:
    assert signal_path_of(doc_id) == path


def test_unbekannte_pfade_werden_nicht_den_nachrichten_zugeschlagen() -> None:
    """Ein kuenftiger Pfad darf die Nachrichten-Zahl nicht still verfaelschen."""
    assert signal_path_of("new_feed:xyz") != SIGNAL_PATH_NEWS


# ---------------------------------------------------------------------------
# Zaehlung
# ---------------------------------------------------------------------------


def test_je_pfad_ein_eigener_bericht(tmp_path: Path) -> None:
    _pair(tmp_path, "tv:a", "hit", at="2026-09-01T10:00:00+00:00")
    _pair(tmp_path, "tv:b", "miss", at="2026-09-03T10:00:00+00:00", asset="ETH/USDT")
    _pair(tmp_path, UUID, "hit", at="2026-09-05T10:00:00+00:00", asset="SOL/USDT")

    reports = _reports(tmp_path)

    assert set(reports) == {SIGNAL_PATH_TRADINGVIEW, SIGNAL_PATH_NEWS}
    assert reports[SIGNAL_PATH_TRADINGVIEW].resolved_rows == 2
    assert reports[SIGNAL_PATH_TRADINGVIEW].episode_hit == 1
    assert reports[SIGNAL_PATH_NEWS].resolved_rows == 1
    assert reports[SIGNAL_PATH_NEWS].episode_hit == 1


def test_zeilen_der_teile_ergeben_die_gemeinsame_zahl(tmp_path: Path) -> None:
    """Gegenprobe auf Zeilenebene: nichts geht verloren, nichts wird doppelt gezaehlt."""
    for i in range(5):
        _pair(tmp_path, f"tv:x{i}", "hit", at=f"2026-09-0{i + 1}T10:00:00+00:00")
    _pair(tmp_path, UUID, "miss", at="2026-09-02T10:00:00+00:00", asset="ETH/USDT")
    _pair(tmp_path, "technical_paper_BTC_1", "hit", at="2026-09-04T12:00:00+00:00")

    pooled = build_episode_dedupe_report(
        audit_path=tmp_path / "alert_outcomes.jsonl",
        alert_audit_path=tmp_path / "alert_audit.jsonl",
    )
    parts = _reports(tmp_path)

    assert sum(r.resolved_rows for r in parts.values()) == pooled.resolved_rows


def test_pfaduebergreifende_episode_zaehlt_in_jedem_pfad_einmal(tmp_path: Path) -> None:
    """Die gemeinsame Zahl legt TradingView und Nachrichten auf derselben Bewegung
    zu EINER Episode zusammen; je Pfad ist es je eine. Episoden summieren sich
    daher nicht -- die Teile sind mindestens so viele wie das Ganze."""
    _pair(tmp_path, "tv:a", "hit", at="2026-09-01T10:00:00+00:00")
    _pair(tmp_path, UUID, "hit", at="2026-09-01T10:30:00+00:00")

    pooled = build_episode_dedupe_report(
        audit_path=tmp_path / "alert_outcomes.jsonl",
        alert_audit_path=tmp_path / "alert_audit.jsonl",
    )
    parts = _reports(tmp_path)

    assert pooled.episode_total == 1
    assert parts[SIGNAL_PATH_TRADINGVIEW].episode_total == 1
    assert parts[SIGNAL_PATH_NEWS].episode_total == 1
    assert pooled.episode_total <= sum(r.episode_total for r in parts.values())


def test_gemeinsame_zahl_bleibt_unveraendert(tmp_path: Path) -> None:
    """Additiv: ohne Filter zaehlt der Bericht wie bisher."""
    _pair(tmp_path, "tv:a", "hit", at="2026-09-01T10:00:00+00:00")
    _pair(tmp_path, UUID, "miss", at="2026-09-01T10:30:00+00:00")

    default = build_episode_dedupe_report(
        audit_path=tmp_path / "alert_outcomes.jsonl",
        alert_audit_path=tmp_path / "alert_audit.jsonl",
    )
    explicit_all = build_episode_dedupe_report(
        audit_path=tmp_path / "alert_outcomes.jsonl",
        alert_audit_path=tmp_path / "alert_audit.jsonl",
        doc_filter=lambda _doc: True,
    )

    assert default.to_dict() == explicit_all.to_dict()


def test_fehlende_dateien_ergeben_keine_pfade(tmp_path: Path) -> None:
    assert _reports(tmp_path) == {}


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


def test_deutsche_zeile_nennt_pfad_quote_umfang_und_bereich(tmp_path: Path) -> None:
    for i in range(3):
        _pair(tmp_path, f"tv:h{i}", "hit", at=f"2026-09-0{i + 1}T10:00:00+00:00")
    _pair(tmp_path, "tv:m", "miss", at="2026-09-07T10:00:00+00:00")

    line = format_path_precision_de(_reports(tmp_path))

    assert line.startswith("TradingView 75.0 % (3/4 Episoden, Wilson95 30.1–95.4 %)")


def test_reihenfolge_ist_fest_und_leere_pfade_fehlen(tmp_path: Path) -> None:
    _pair(tmp_path, "technical_paper_BTC_1", "hit", at="2026-09-01T10:00:00+00:00")
    _pair(tmp_path, "tv:a", "hit", at="2026-09-02T10:00:00+00:00", asset="ETH/USDT")

    line = format_path_precision_de(_reports(tmp_path))

    assert line.index("TradingView") < line.index("Technical")
    assert "Nachrichten" not in line


def test_ohne_daten_ein_gedankenstrich() -> None:
    assert format_path_precision_de({}) == "—"
    assert format_path_precision_en({}) == []


def test_englische_zeilen_fuer_das_briefing(tmp_path: Path) -> None:
    _pair(tmp_path, UUID, "hit", at="2026-09-01T10:00:00+00:00")

    lines = format_path_precision_en(_reports(tmp_path))

    assert lines == ["    News:        100.0% (1/1 episodes, CI 20.7–100.0%)"]


# ---------------------------------------------------------------------------
# Leser
# ---------------------------------------------------------------------------


def test_briefing_zeigt_die_aufteilung_zwischen_episode_und_rohwert(tmp_path: Path) -> None:
    from app.alerts.daily_briefing import build_daily_briefing

    now = datetime.now(UTC).isoformat()
    _pair(tmp_path, "tv:a", "hit", at=now)
    _pair(tmp_path, UUID, "miss", at=now, asset="ETH/USDT")

    text = build_daily_briefing(tmp_path).to_text()

    assert "Episode-dedup:" in text
    assert "  By path (episode-dedup):" in text
    assert "    TradingView: 100.0% (1/1 episodes" in text
    assert "    News:        0.0% (0/1 episodes" in text
    assert text.index("Episode-dedup:") < text.index("By path") < text.index("Precision (raw):")


def test_briefing_ohne_ergebnisse_ohne_aufteilung(tmp_path: Path) -> None:
    from app.alerts.daily_briefing import build_daily_briefing

    assert "By path" not in build_daily_briefing(tmp_path).to_text()
