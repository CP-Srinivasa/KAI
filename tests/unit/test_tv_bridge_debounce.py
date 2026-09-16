"""Entprellung im TV-Bruecken-Pfad (V10, Daily Review 2026-09-16).

Der TV-Pfad laeuft am Eligibility-Gate vorbei: ``persist_tv_events_as_alert_audits``
schreibt ``AlertAuditRecord`` direkt, mit ``actionable=True`` und
``directional_eligible=True`` fest verdrahtet. Es gab dort bis hierhin keinerlei
Bremse gegen wiederholte Meldungen desselben Setups -- die Replay-Caches am
Webhook greifen nur gegen identische Nutzlasten, nicht gegen dieselbe Aussage mit
neuer ``event_id``.

Gemessen am 16.09. ueber 09.-16.09.: 293 dispatchte TV-Alerts, alle bullish, auf
zwei Basiswerten, Median-Abstand 1,08 min.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.alerts.alert_debounce import DEBOUNCE_BLOCK_REASON
from app.alerts.tv_bridge import persist_tv_events_as_alert_audits


def _write_pending(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _event(event_id: str, *, minute: int, action: str = "buy", ticker: str = "ETHUSDT") -> dict:
    return {
        "event_id": event_id,
        "ticker": ticker,
        "action": action,
        "received_at": f"2026-09-15T05:{minute:02d}:00+00:00",
    }


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _run(pending: Path, audit: Path, blocked: Path, **kw):
    return persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        blocked_alerts_path=blocked,
        **kw,
    )


# ---------------------------------------------------------------------------
# Kernverhalten
# ---------------------------------------------------------------------------


def test_serie_gleicher_richtung_wird_auf_einen_alert_verdichtet(tmp_path: Path) -> None:
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(pending, [_event(f"e{i}", minute=i) for i in range(30)])

    counts = _run(pending, audit, blocked, debounce_window_minutes=60)

    assert counts["written"] == 1
    assert counts["skipped_debounced"] == 29
    assert len(_rows(audit)) == 1


def test_unterdrueckte_landen_nachvollziehbar_im_blocked_strom(tmp_path: Path) -> None:
    """Sonst verschwindet die Messgrundlage fuer die naechste Runde."""
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(pending, [_event("e0", minute=0), _event("e1", minute=1)])

    _run(pending, audit, blocked, debounce_window_minutes=60)

    rows = _rows(blocked)
    assert len(rows) == 1
    assert rows[0]["document_id"] == "tv:e1"
    assert rows[0]["block_reason"] == DEBOUNCE_BLOCK_REASON
    assert rows[0]["sentiment_label"] == "bullish"
    assert rows[0]["source_name"] == "tradingview_webhook"


def test_richtungswechsel_geht_durch(tmp_path: Path) -> None:
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(
        pending,
        [
            _event("e0", minute=0, action="buy"),
            _event("e1", minute=1, action="buy"),
            _event("e2", minute=2, action="sell"),
        ],
    )

    counts = _run(pending, audit, blocked, debounce_window_minutes=60)

    assert counts["written"] == 2
    assert counts["skipped_debounced"] == 1
    assert [r["sentiment_label"] for r in _rows(audit)] == ["bullish", "bearish"]


def test_verschiedene_werte_bleiben_unabhaengig(tmp_path: Path) -> None:
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(
        pending,
        [
            _event("e0", minute=0, ticker="ETHUSDT"),
            _event("e1", minute=1, ticker="BTCUSDT"),
            _event("e2", minute=2, ticker="ETHUSDT"),
        ],
    )

    counts = _run(pending, audit, blocked, debounce_window_minutes=60)

    assert counts["written"] == 2
    assert counts["skipped_debounced"] == 1


def test_nach_dem_fenster_geht_wieder_einer_durch(tmp_path: Path) -> None:
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(pending, [_event("e0", minute=0), _event("e1", minute=31)])

    counts = _run(pending, audit, blocked, debounce_window_minutes=30)

    assert counts["written"] == 2
    assert counts["skipped_debounced"] == 0


# ---------------------------------------------------------------------------
# Takt-Uebergreifend: der Kern des Ganzen
# ---------------------------------------------------------------------------


def test_das_fenster_ueberlebt_den_takt(tmp_path: Path) -> None:
    """Die Bruecke laeuft alle 300 s neu an. Ohne Wiederanlauf aus dem Audit
    waere das Fenster faktisch so kurz wie der Takt."""
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))

    _write_pending(pending, [_event("e0", minute=0)])
    first = _run(pending, audit, blocked, debounce_window_minutes=60)
    assert first["written"] == 1

    _write_pending(pending, [_event("e0", minute=0), _event("e1", minute=5)])
    second = _run(pending, audit, blocked, debounce_window_minutes=60)

    assert second["skipped_existing"] == 1
    assert second["written"] == 0, "der zweite Takt darf die Serie nicht neu beginnen"
    assert second["skipped_debounced"] == 1


def test_einmal_unterdrueckt_bleibt_unterdrueckt(tmp_path: Path) -> None:
    """Die Bruecke raeumt die Pending-Datei nicht ab. Ein unterdruecktes Ereignis
    darf beim naechsten Takt weder erneut geprueft noch doppelt protokolliert
    werden."""
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(pending, [_event("e0", minute=0), _event("e1", minute=1)])

    _run(pending, audit, blocked, debounce_window_minutes=60)
    second = _run(pending, audit, blocked, debounce_window_minutes=60)

    assert len(_rows(blocked)) == 1, "kein zweiter Blocked-Eintrag fuer dasselbe Ereignis"
    assert second["skipped_blocked"] == 1
    assert second["written"] == 0


# ---------------------------------------------------------------------------
# Abschaltbarkeit und Rueckwaertsvertraeglichkeit
# ---------------------------------------------------------------------------


def test_fenster_null_laesst_alles_durch(tmp_path: Path) -> None:
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(pending, [_event(f"e{i}", minute=i) for i in range(10)])

    counts = _run(pending, audit, blocked, debounce_window_minutes=0)

    assert counts["written"] == 10
    assert counts["skipped_debounced"] == 0
    assert not blocked.exists() or _rows(blocked) == []


def test_ohne_blocked_pfad_bleibt_das_alte_verhalten(tmp_path: Path) -> None:
    """Ruft jemand die Bruecke ohne den neuen Parameter auf, wird nichts
    unterdrueckt -- sonst verschwaende ein Aufrufer still Alerts."""
    pending, audit = tmp_path / "p.jsonl", tmp_path / "a.jsonl"
    _write_pending(pending, [_event(f"e{i}", minute=i) for i in range(5)])

    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        debounce_window_minutes=60,
    )

    assert counts["written"] == 5
    assert counts["skipped_debounced"] == 0


def test_fenster_kommt_aus_der_umgebung_wenn_nichts_uebergeben_wird(
    tmp_path: Path, monkeypatch
) -> None:
    """Der eine Schalter, den ein Operator ohne Deploy umlegen kann."""
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(pending, [_event(f"e{i}", minute=i) for i in range(5)])
    monkeypatch.setenv("KAI_ALERT_DEBOUNCE_WINDOW_MIN", "0")

    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        blocked_alerts_path=blocked,
    )

    assert counts["written"] == 5
    assert counts["skipped_debounced"] == 0


def test_ohne_umgebung_gilt_das_gemessene_defaultfenster(tmp_path: Path, monkeypatch) -> None:
    pending, audit, blocked = (tmp_path / n for n in ("p.jsonl", "a.jsonl", "b.jsonl"))
    _write_pending(pending, [_event(f"e{i}", minute=i) for i in range(5)])
    monkeypatch.delenv("KAI_ALERT_DEBOUNCE_WINDOW_MIN", raising=False)

    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        blocked_alerts_path=blocked,
    )

    assert counts["written"] == 1
    assert counts["skipped_debounced"] == 4
