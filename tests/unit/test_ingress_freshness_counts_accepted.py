r"""Der Eingangs-Waechter liess sich von ABGELEHNTEN Requests beruhigen.

Befund 2026-08-18, unfreiwillig selbst ausgeloest. Bei der Diagnose des seit
16 Tagen toten TradingView-Ingests habe ich drei unsignierte Test-Requests an
``POST /tradingview/webhook`` geschickt. Ergebnis im Audit:

    {"source_ip": "127.0.0.1", "body_bytes": 2,
     "auth_mode": "hmac_strict_event_id", "outcome": "rejected", ...}

Sie wurden korrekt abgewiesen -- und haben trotzdem in
``tradingview_webhook_audit.jsonl`` geschrieben. Der Frische-Waechter misst die
Datei-**mtime**, also war er danach gruen: der naechste Health-Check meldete
"All systems healthy", obwohl das letzte AKZEPTIERTE Event vom
``2026-08-02T17:23:45Z`` stammt.

Damit schaltet jeder beliebige Request den Waechter fuer 12 Stunden stumm --
auch ein Portscanner, der die oeffentliche Adresse abklopft. Ein Eingangs-
Waechter, den man von aussen beruhigen kann, ist keiner.

Der Ingest-Tod war ueberhaupt nur deshalb 6 Tage unbemerkt geblieben, weil
niemand auf den Eingang schaute. Ihn dann an eine Groesse zu haengen, die
Fremde setzen koennen, wiederholt denselben Fehler eine Ebene tiefer.

Gemessen wird jetzt der letzte Record mit ``outcome == "accepted"``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.ingress_audit import last_accepted_ingress_event


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _row(when: datetime, outcome: str, ip: str = "34.212.75.30") -> dict:
    return {
        "request_id": f"tvwh_{outcome}_{when.timestamp()}",
        "received_at": when.isoformat(),
        "source_ip": ip,
        "outcome": outcome,
    }


def test_rejected_requests_do_not_refresh_the_watchdog(tmp_path: Path) -> None:
    """Der Realfall: alt+akzeptiert, dann frisch+abgelehnt."""
    path = tmp_path / "tradingview_webhook_audit.jsonl"
    now = datetime.now(UTC)
    accepted = now - timedelta(days=16)
    _write(
        path,
        [
            _row(accepted, "accepted"),
            # meine drei Sonden von eben
            _row(now - timedelta(minutes=9), "rejected", ip="127.0.0.1"),
            _row(now - timedelta(minutes=8), "rejected", ip="127.0.0.1"),
            _row(now - timedelta(minutes=7), "rejected", ip="127.0.0.1"),
        ],
    )
    got = last_accepted_ingress_event(path)
    assert got is not None
    assert abs((got - accepted).total_seconds()) < 1, (
        "der Waechter darf nicht auf die abgelehnten Sonden zeigen"
    )


def test_accepted_event_is_recognised(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    now = datetime.now(UTC)
    _write(path, [_row(now - timedelta(days=3), "accepted"), _row(now, "accepted")])
    got = last_accepted_ingress_event(path)
    assert got is not None and abs((got - now).total_seconds()) < 1


def test_only_rejected_means_never_delivered(tmp_path: Path) -> None:
    """Ein Strom, der nur Abweisungen kennt, hat noch nie geliefert."""
    path = tmp_path / "a.jsonl"
    now = datetime.now(UTC)
    _write(path, [_row(now, "rejected"), _row(now, "rejected")])
    assert last_accepted_ingress_event(path) is None


def test_missing_or_empty_file_is_none(tmp_path: Path) -> None:
    assert last_accepted_ingress_event(tmp_path / "gibt-es-nicht.jsonl") is None
    empty = tmp_path / "leer.jsonl"
    empty.write_text("", encoding="utf-8")
    assert last_accepted_ingress_event(empty) is None


def test_garbage_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    """Eine kaputte Zeile darf den Waechter nicht abschalten."""
    path = tmp_path / "a.jsonl"
    now = datetime.now(UTC)
    path.write_text(
        "{kaputt\n" + json.dumps(_row(now, "accepted")) + "\nnoch mehr muell\n",
        encoding="utf-8",
    )
    got = last_accepted_ingress_event(path)
    assert got is not None and abs((got - now).total_seconds()) < 1


def test_reads_only_the_tail_of_a_large_file(tmp_path: Path) -> None:
    """Der Waechter laeuft alle 15 min gegen eine 2,5-MB-Datei -- er darf sie
    nicht jedesmal komplett lesen."""
    path = tmp_path / "gross.jsonl"
    now = datetime.now(UTC)
    filler = [_row(now - timedelta(days=30), "rejected") for _ in range(20000)]
    _write(path, [*filler, _row(now, "accepted")])
    assert path.stat().st_size > 2_000_000
    got = last_accepted_ingress_event(path)
    assert got is not None and abs((got - now).total_seconds()) < 1


def test_report_is_not_fooled_by_a_fresh_rejection(tmp_path: Path) -> None:
    """Der ganze Report, nicht nur der Helper: eine frische Abweisung darf den
    16 Tage toten Eingang nicht als gesund ausweisen.

    Genau dieser Zustand stand am 2026-08-18 auf dem Pi: 'All systems healthy',
    waehrend das letzte akzeptierte Event 16 Tage zurueck lag.
    """
    import os

    from app.alerts.health_check import run_health_check_report

    now = datetime.now(UTC)
    (tmp_path / "alert_audit.jsonl").touch()
    (tmp_path / "trading_loop_audit.jsonl").write_text(
        json.dumps(
            {
                "cycle_id": "c1",
                "started_at": now.isoformat(),
                "symbol": "BTC/USDT",
                "status": "completed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    tv = tmp_path / "tradingview_webhook_audit.jsonl"
    _write(
        tv,
        [
            _row(now - timedelta(days=16), "accepted"),
            _row(now - timedelta(minutes=2), "rejected", ip="127.0.0.1"),
        ],
    )
    # mtime brandaktuell -- der alte Waechter waere hier gruen gewesen.
    fresh = now.timestamp()
    os.utime(tv, (fresh, fresh))

    report = run_health_check_report(tmp_path)
    ingress = [i for i in report.issues if i.component == "tradingview_ingress_freshness"]
    assert ingress, "toter Eingang wurde trotz frischer Abweisung nicht gemeldet"
    # Und er bleibt ein Systembefund, kein Probe-Abbruch.
    assert report.data_sources_stale is False
