"""Praezision je Pfad neben ihrer naiven Basisrate (S2, Daily Review 2026-09-16).

Der Bericht zaehlt je Signalpfad die REIFEN bullischen Episoden der letzten 30 Tage
(alle Bewertungsstufen des Annotators abgelaufen), gewichtet die Basisrate "immer
bullish zu jeder vollen Stunde" nach den Basiswerten des Pfads und legt das
vorab festgelegte S1-Kriterium an. Preise werden injiziert — kein Netz im Test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability.outcome_dedupe_report import build_episode_reports_by
from app.observability.precision_baseline_report import (
    REPORT_FILENAME,
    build_precision_baseline_report,
    format_baseline_de,
    format_baseline_en,
    load_fresh_report,
    write_report,
)

NOW = datetime(2026, 9, 16, 21, 0, tzinfo=UTC)
UUID = "3f2c9a1e-7b4d-4c2a-9e1f-0a1b2c3d4e5f"
NOTE = "auto@4h: bullish BTC/USDT $100.00->$104.00 (+4.00% over 4.0h, thr=0.42%)"


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _pair(tmp: Path, doc: str, outcome: str, *, at: datetime, asset: str = "BTC/USDT") -> None:
    _write(
        tmp / "alert_outcomes.jsonl",
        [
            {
                "document_id": doc,
                "outcome": outcome,
                "annotated_at": at.isoformat(),
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
                "channel": "telegram",
                "dispatched_at": at.isoformat(),
                "sentiment_label": "bullish",
            }
        ],
    )


def _rising_fetcher(calls: list[str]):
    """Stetig steigende Preise -> Basisrate 100 %. Zeichnet Abrufe auf."""

    def fetch(asset: str, start: datetime, end: datetime):
        calls.append(asset)
        hours = int((end - start).total_seconds() // 3600) + 1
        return [(start + timedelta(hours=h), 100.0 * (1.001**h)) for h in range(hours)]

    return fetch


def _build(tmp: Path, fetch):
    return build_precision_baseline_report(
        now=NOW,
        audit_path=tmp / "alert_outcomes.jsonl",
        alert_audit_path=tmp / "alert_audit.jsonl",
        fetch_series=fetch,
    )


# ---------------------------------------------------------------------------
# Gruppierte Episoden
# ---------------------------------------------------------------------------


def test_gruppierung_nach_beliebigem_schluessel(tmp_path: Path) -> None:
    at = NOW - timedelta(days=10)
    _pair(tmp_path, "tv:a", "hit", at=at)
    _pair(tmp_path, "tv:b", "miss", at=at + timedelta(days=2), asset="ETH/USDT")
    _pair(tmp_path, UUID, "hit", at=at)

    reports = build_episode_reports_by(
        key_of=lambda doc, rec, anchor, sentiment: f"{doc[:3]}|{rec.get('asset')}",
        audit_path=tmp_path / "alert_outcomes.jsonl",
        alert_audit_path=tmp_path / "alert_audit.jsonl",
    )

    assert reports["tv:|BTC/USDT"].episode_hit == 1
    assert reports["tv:|ETH/USDT"].episode_total == 1
    assert sum(r.resolved_rows for r in reports.values()) == 3


def test_schluessel_none_schliesst_aus(tmp_path: Path) -> None:
    _pair(tmp_path, "tv:a", "hit", at=NOW - timedelta(days=10))
    reports = build_episode_reports_by(
        key_of=lambda *_: None,
        audit_path=tmp_path / "alert_outcomes.jsonl",
        alert_audit_path=tmp_path / "alert_audit.jsonl",
    )
    assert reports == {}


# ---------------------------------------------------------------------------
# Bericht
# ---------------------------------------------------------------------------


def test_nur_reife_episoden_im_fenster_zaehlen(tmp_path: Path) -> None:
    _pair(tmp_path, "tv:reif", "hit", at=NOW - timedelta(days=10))
    _pair(tmp_path, "tv:unreif", "hit", at=NOW - timedelta(days=2), asset="ETH/USDT")
    _pair(tmp_path, "tv:alt", "hit", at=NOW - timedelta(days=40), asset="SOL/USDT")

    report = _build(tmp_path, _rising_fetcher([]))

    tv = report["paths"]["tradingview"]
    assert tv["n"] == 1
    assert set(tv["per_asset"]) == {"BTC/USDT"}


def test_basisrate_und_urteil_je_pfad(tmp_path: Path) -> None:
    for i in range(3):
        _pair(tmp_path, f"tv:{i}", "hit", at=NOW - timedelta(days=10 + 2 * i))
    report = _build(tmp_path, _rising_fetcher([]))

    tv = report["paths"]["tradingview"]
    assert tv["hits"] == 3
    assert tv["baseline_rate"] == 1.0
    assert tv["timing_value"] is False, "Untergrenze kann eine Basis von 100 % nicht uebertreffen"


def test_basiswert_ohne_preise_faellt_aus_und_wird_genannt(tmp_path: Path) -> None:
    _pair(tmp_path, "tv:a", "hit", at=NOW - timedelta(days=10))
    _pair(tmp_path, "tv:b", "miss", at=NOW - timedelta(days=12), asset="XYZ/USDT")
    rising = _rising_fetcher([])

    def fetch(asset, start, end):
        return None if asset.startswith("XYZ") else rising(asset, start, end)

    report = _build(tmp_path, fetch)

    assert report["paths"]["tradingview"]["n"] == 1
    assert report["assets_without_prices"] == ["XYZ/USDT"]


def test_jeder_basiswert_wird_nur_einmal_abgerufen(tmp_path: Path) -> None:
    _pair(tmp_path, "tv:a", "hit", at=NOW - timedelta(days=10))
    _pair(tmp_path, UUID, "hit", at=NOW - timedelta(days=11))
    calls: list[str] = []

    _build(tmp_path, _rising_fetcher(calls))

    assert sorted(calls) == ["BTC/USDT"], "BTC dient zugleich als Volatilitaetsreihe"


def test_ohne_episoden_kein_abruf(tmp_path: Path) -> None:
    calls: list[str] = []
    report = _build(tmp_path, _rising_fetcher(calls))
    assert calls == []
    assert report["paths"] == {}


# ---------------------------------------------------------------------------
# Ablage und Frische
# ---------------------------------------------------------------------------


def test_bericht_wird_abgelegt_und_frisch_gelesen(tmp_path: Path) -> None:
    _pair(tmp_path, "tv:a", "hit", at=NOW - timedelta(days=10))
    report = _build(tmp_path, _rising_fetcher([]))
    write_report(report, tmp_path)

    assert (tmp_path / REPORT_FILENAME).exists()
    assert load_fresh_report(tmp_path, now=NOW + timedelta(hours=35)) == report


def test_veralteter_bericht_wird_nicht_gezeigt(tmp_path: Path) -> None:
    _pair(tmp_path, "tv:a", "hit", at=NOW - timedelta(days=10))
    write_report(_build(tmp_path, _rising_fetcher([])), tmp_path)

    assert load_fresh_report(tmp_path, now=NOW + timedelta(hours=37)) is None


def test_fehlender_oder_kaputter_bericht_ist_none(tmp_path: Path) -> None:
    assert load_fresh_report(tmp_path, now=NOW) is None
    (tmp_path / REPORT_FILENAME).write_text("{kaputt", encoding="utf-8")
    assert load_fresh_report(tmp_path, now=NOW) is None


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


_S1_REPORT = {
    "generated_at": NOW.isoformat(),
    "paths": {
        "tradingview": {
            "hits": 35,
            "n": 63,
            "precision": 35 / 63,
            "wilson_low": 0.4333,
            "wilson_high": 0.6722,
            "baseline_rate": 0.560,
            "timing_value": False,
        },
        "technical": {
            "hits": 59,
            "n": 97,
            "precision": 59 / 97,
            "wilson_low": 0.5087,
            "wilson_high": 0.6991,
            "baseline_rate": 0.605,
            "timing_value": False,
        },
    },
}


def test_deutsche_zelle_stellt_praezision_neben_basis() -> None:
    cell = format_baseline_de(_S1_REPORT)
    assert cell.startswith(
        "TradingView 55.6 % vs. Basis 56.0 % (n=63, Wilson95 43.3–67.2 %) → kein Timing-Wert belegt"
    )
    assert "Technical 60.8 % vs. Basis 60.5 %" in cell


def test_englische_zeilen() -> None:
    lines = format_baseline_en(_S1_REPORT)
    assert lines[0] == "  30d vs naive baseline (matured, bullish):"
    assert (
        lines[1]
        == "    TradingView: 55.6% vs base 56.0% (n=63, CI 43.3–67.2%) - no timing value shown"
    )


def test_ohne_bericht_gedankenstrich_und_keine_zeilen() -> None:
    assert format_baseline_de(None) == "—"
    assert format_baseline_en(None) == []


# ---------------------------------------------------------------------------
# Leser
# ---------------------------------------------------------------------------


def test_briefing_zeigt_frischen_bericht(tmp_path: Path) -> None:
    from app.alerts.daily_briefing import build_daily_briefing

    now = datetime.now(UTC)
    _pair(tmp_path, "tv:a", "hit", at=now)
    report = dict(_S1_REPORT, generated_at=now.isoformat())
    write_report(report, tmp_path)

    text = build_daily_briefing(tmp_path).to_text()

    assert "  30d vs naive baseline (matured, bullish):" in text
    assert "TradingView: 55.6% vs base 56.0%" in text
    assert (
        text.index("By path") < text.index("30d vs naive baseline") < text.index("Precision (raw):")
    )


def test_briefing_ohne_frischen_bericht_ohne_basiszeilen(tmp_path: Path) -> None:
    from app.alerts.daily_briefing import build_daily_briefing

    now = datetime.now(UTC)
    _pair(tmp_path, "tv:a", "hit", at=now)
    write_report(dict(_S1_REPORT, generated_at=(now - timedelta(days=3)).isoformat()), tmp_path)

    assert "naive baseline" not in build_daily_briefing(tmp_path).to_text()


def test_pfad_urteil_traegt_seine_zerlegung_nach_basiswert(tmp_path: Path) -> None:
    """Direktive 2026-08-08: kein Aggregat ohne Zerlegung."""
    _pair(tmp_path, "tv:a", "hit", at=NOW - timedelta(days=10))
    _pair(tmp_path, "tv:b", "miss", at=NOW - timedelta(days=12), asset="ETH/USDT")
    report = _build(tmp_path, _rising_fetcher([]))

    deco = report["paths"]["tradingview"]["decomposition"]
    assert set(deco["by_group"]) == {"BTC/USDT", "ETH/USDT"}
    assert report["paths"]["tradingview"]["numerator"] == 1
    assert report["paths"]["tradingview"]["denominator"] == 2
