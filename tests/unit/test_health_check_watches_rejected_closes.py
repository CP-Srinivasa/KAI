"""Ein abgewiesener Phantom-Close erreichte niemanden.

``close_price_sanity_rejected`` wird seit DS-20260529-V1 geschrieben. Konsumenten
am 2026-08-18: **null** — nur zwei Unit-Tests und ein Doc-Kommentar in
``phantom_filter.py`` erwaehnen das Ereignis ueberhaupt. Es lag im Stream und
niemand schaute hin, dieselbe Familie wie [[feedback_monitoring_watches_outputs_not_inputs]].

Das Ereignis ist doppeldeutig, und **beide** Lesarten brauchen Augen:

* Der Preis-Feed liefert Muell (der Fall, fuer den der Breaker gebaut ist).
* Oder der Breaker liegt falsch und eine echte Position kommt nicht mehr zu —
  sie bleibt offen und wird bei jedem Tick erneut abgewiesen.

Seit die Schwelle von 200 % auf 20 % gesenkt wurde, ist die zweite Lesart nicht
mehr theoretisch. Ein stummer Wächter waere hier ein Rueckschritt gewesen.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.health_check import _check_rejected_closes


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "paper_execution_audit.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8"
    )
    return tmp_path


def _rejection(ts: datetime, symbol: str = "ETH/USDT", pct: float = 72.1) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "close_price_sanity_rejected",
        "timestamp_utc": ts.isoformat(),
        "symbol": symbol,
        "reason": "take",
        "entry_price": 1874.25,
        "close_price": 3225.6863500000004,
        "implied_return_pct": pct,
        "max_close_return_pct": 20.0,
        "position_side": "long",
    }


def test_keine_abweisung_kein_befund(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    adir = _write(tmp_path, [{"event_type": "position_closed", "timestamp_utc": now.isoformat()}])
    assert _check_rejected_closes(adir, now, lookback_hours=24) == []


def test_abweisung_im_fenster_ist_ein_befund(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    adir = _write(tmp_path, [_rejection(now - timedelta(hours=2))])
    issues = _check_rejected_closes(adir, now, lookback_hours=24)
    assert len(issues) == 1
    assert issues[0].component == "close_price_sanity"
    assert "ETH/USDT" in issues[0].message
    assert "72.1" in issues[0].message


def test_alte_abweisung_zaehlt_nicht(tmp_path: Path) -> None:
    """Sonst meldet der Check den MATIC-Vorfall vom Mai bis in alle Ewigkeit."""
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    adir = _write(tmp_path, [_rejection(now - timedelta(days=30))])
    assert _check_rejected_closes(adir, now, lookback_hours=24) == []


def test_mehrere_abweisungen_werden_zusammengefasst(tmp_path: Path) -> None:
    """Eine Meldung mit Anzahl, nicht eine je Ereignis — sonst ist es Spam."""
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    rows = [
        _rejection(now - timedelta(hours=1), "ETH/USDT", 72.1),
        _rejection(now - timedelta(hours=3), "SOL/USDT", 96.9),
        _rejection(now - timedelta(hours=5), "ETH/USDT", 71.5),
    ]
    issues = _check_rejected_closes(_write(tmp_path, rows), now, lookback_hours=24)
    assert len(issues) == 1
    assert "3" in issues[0].message
    assert "ETH/USDT" in issues[0].message and "SOL/USDT" in issues[0].message


def test_fehlende_datei_ist_kein_fehlalarm(tmp_path: Path) -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    assert _check_rejected_closes(tmp_path, now, lookback_hours=24) == []


def test_kaputte_zeitangabe_zaehlt_fail_closed_mit(tmp_path: Path) -> None:
    """Ein unlesbarer Zeitstempel darf einen Befund nicht verschwinden lassen."""
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    bad = _rejection(now)
    bad["timestamp_utc"] = "nicht-lesbar"
    assert len(_check_rejected_closes(_write(tmp_path, [bad]), now, lookback_hours=24)) == 1
