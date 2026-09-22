"""System-Audit 16.09. (S2-11): stille Handler in der Alert-Kette werden sichtbar.

Drei Stellen verschluckten Fehler ohne Spur: das Dedup-Seeding des
``AlertService`` (nach einem Neustart waren die Alerts der letzten 24 h wieder
frei), das Daily Briefing zeigte bei unlesbarem Audit "0 Alerts" bzw.
"0 Annotations" statt "nicht erhebbar", und die Schwellen-Fallbacks in
``eligibility.py`` ersetzten die Operator-Schwelle durch den Default, ohne es
zu sagen.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import structlog

from app.alerts import daily_briefing as briefing
from app.alerts import eligibility
from app.alerts import service as alert_service
from app.alerts.threshold import ThresholdEngine


def _boom(_path: object) -> object:
    raise OSError("Permission denied: alert_audit.jsonl")


# ── AlertService: Dedup-Seed ───────────────────────────────────────────────


def test_dedup_seed_fehler_wird_geloggt_und_startet_leer(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(alert_service, "load_alert_audits", _boom)
    with structlog.testing.capture_logs() as logs:
        service = alert_service.AlertService(
            channels=[], threshold=ThresholdEngine(), audit_dir=tmp_path
        )
    events = [e for e in logs if e["event"] == "alert_dedup_seed_failed"]
    assert len(events) == 1
    assert events[0]["error"] == "OSError: Permission denied: alert_audit.jsonl"
    assert events[0]["audit_dir"] == str(tmp_path)
    assert events[0]["lookback_hours"] == alert_service._DEDUP_LOOKBACK_HOURS
    assert service._seen_title_hashes == set()


# ── Daily Briefing: "nicht erhebbar" statt Null ────────────────────────────


def test_briefing_nennt_unlesbares_alert_audit_statt_null(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(briefing, "load_alert_audits", _boom)
    data = briefing.build_daily_briefing(tmp_path)
    assert data.alerts_source_error == "OSError: Permission denied: alert_audit.jsonl"
    text = data.to_text()
    assert (
        "Alerts (last 24h)\n  nicht erhebbar [OSError: Permission denied: alert_audit.jsonl]"
        in text
    )
    assert "Dispatched:" not in text
    assert "[P10] tier:" not in text


def test_briefing_nennt_unlesbare_annotationen_statt_null(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(briefing, "load_outcome_annotations", _boom)
    data = briefing.build_daily_briefing(tmp_path)
    assert data.annotations_source_error == "OSError: Permission denied: alert_audit.jsonl"
    text = data.to_text()
    assert "Directional Precision (all time)\n  nicht erhebbar [OSError:" in text
    assert "Annotations:" not in text
    assert "Hits:" not in text


def test_briefing_unterdrueckt_episode_zeilen_bei_unlesbaren_annotationen() -> None:
    # Episode-Dedup liest dieselbe Datei mit eigenem Reader; eine Zahl neben
    # "nicht erhebbar" waere ein Widerspruch im selben Block.
    data = briefing.BriefingData(
        generated_at="2026-09-22T10:00:00+00:00",
        lookback_hours=24,
        annotations_source_error="TypeError: kaputte Provenienz",
        episode_precision_pct=55.0,
        episode_hits=11,
        episode_count=20,
        episode_rows=40,
    )
    text = data.to_text()
    assert "nicht erhebbar [TypeError: kaputte Provenienz]" in text
    assert "Episode-dedup" not in text


def test_briefing_ohne_fehler_zeigt_zahlen(tmp_path: Path) -> None:
    data = briefing.build_daily_briefing(tmp_path)
    assert data.alerts_source_error is None
    assert data.annotations_source_error is None
    text = data.to_text()
    assert "  Dispatched:   0" in text
    assert "  Annotations:  0" in text
    assert "nicht erhebbar" not in text


# ── eligibility: Schwellen-Fallback ist sichtbar ───────────────────────────


def _settings_stub(**alerts: float) -> object:
    return SimpleNamespace(alerts=SimpleNamespace(**alerts))


def test_bullish_schwelle_kommt_aus_den_settings_ohne_warnung(monkeypatch) -> None:
    import app.core.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: _settings_stub(min_directional_confidence_bullish=0.7),
    )
    with structlog.testing.capture_logs() as logs:
        assert eligibility._bullish_confidence_threshold() == 0.7
    assert [e for e in logs if e["event"] == "alert_threshold_fallback"] == []


def test_bullish_schwelle_fallback_nennt_env_var_und_default(monkeypatch) -> None:
    import app.core.settings as settings_mod

    def boom() -> object:
        raise RuntimeError("settings kaputt")

    monkeypatch.setattr(settings_mod, "get_settings", boom)
    with structlog.testing.capture_logs() as logs:
        value = eligibility._bullish_confidence_threshold()
    assert value == eligibility.MIN_DIRECTIONAL_CONFIDENCE_BULLISH
    events = [e for e in logs if e["event"] == "alert_threshold_fallback"]
    assert len(events) == 1
    assert events[0]["setting"] == "ALERT_MIN_DIRECTIONAL_CONFIDENCE_BULLISH"
    assert events[0]["default"] == eligibility.MIN_DIRECTIONAL_CONFIDENCE_BULLISH
    assert events[0]["error"] == "RuntimeError: settings kaputt"


def test_technical_schwelle_kommt_aus_den_settings_ohne_warnung(monkeypatch) -> None:
    import app.core.settings as settings_mod

    monkeypatch.setattr(
        settings_mod, "get_settings", lambda: _settings_stub(min_technical_strength=0.3)
    )
    with structlog.testing.capture_logs() as logs:
        assert eligibility._min_technical_strength_threshold() == 0.3
    assert [e for e in logs if e["event"] == "alert_threshold_fallback"] == []


def test_technical_schwelle_fallback_nennt_env_var_und_default(monkeypatch) -> None:
    import app.core.settings as settings_mod

    def boom() -> object:
        raise RuntimeError("settings kaputt")

    monkeypatch.setattr(settings_mod, "get_settings", boom)
    with structlog.testing.capture_logs() as logs:
        value = eligibility._min_technical_strength_threshold()
    assert value == eligibility.MIN_TECHNICAL_STRENGTH
    events = [e for e in logs if e["event"] == "alert_threshold_fallback"]
    assert len(events) == 1
    assert events[0]["setting"] == "ALERT_MIN_TECHNICAL_STRENGTH"
    assert events[0]["default"] == eligibility.MIN_TECHNICAL_STRENGTH
    assert events[0]["error"] == "RuntimeError: settings kaputt"
