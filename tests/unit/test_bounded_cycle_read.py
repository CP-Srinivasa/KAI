"""Begrenztes Lesen des Zyklus-Stroms (31.08., Folge des Health-Check-OOM).

`load_trading_loop_cycles` lud die GESAMTE Historie: 128.501 Saetze aus 79 MB
= **+320 MB**, der groesste Einzelposten des Dienstes, der ab 20:00 vom
OOM-Killer erschlagen wurde. Der Health-Check braucht davon nur sein
`lookback_hours`-Fenster.

Der Kern dieser Tests ist nicht die Ersparnis, sondern die Zusage, dass sie
**nichts kostet**: die Zahlen der Sonde muessen mit und ohne Fenster identisch
sein, und wo das Fenster nicht reicht, muss ungekuerzt nachgelesen werden.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.health_check import CYCLE_PROBE_TAIL, _load_cycles_for_window
from app.orchestrator.trading_loop import load_trading_loop_cycles

NOW = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)


def _write_cycles(path: Path, count: int, *, spacing_minutes: float = 1.0) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i in range(count):
            started = NOW - timedelta(minutes=spacing_minutes * (count - i))
            fh.write(
                json.dumps(
                    {
                        "cycle_id": f"cyc-{i:06d}",
                        "started_at": started.isoformat(),
                        "status": "completed",
                    }
                )
                + "\n"
            )


def test_tail_returns_the_newest_records(tmp_path: Path) -> None:
    path = tmp_path / "trading_loop_audit.jsonl"
    _write_cycles(path, 100)
    rows = load_trading_loop_cycles(path, tail=10)
    assert len(rows) == 10
    assert rows[-1]["cycle_id"] == "cyc-000099"
    assert rows[0]["cycle_id"] == "cyc-000090"


def test_without_tail_nothing_changes(tmp_path: Path) -> None:
    """Positivkontrolle: die Begrenzung ist opt-in.

    ``build_recent_cycles_summary`` zaehlt trotz seines Namens ueber die ganze
    Historie — ein Default-Fenster wuerde diese Zahl still veraendern.
    """
    path = tmp_path / "trading_loop_audit.jsonl"
    _write_cycles(path, 250)
    assert len(load_trading_loop_cycles(path)) == 250


def test_tail_zero_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "trading_loop_audit.jsonl"
    _write_cycles(path, 5)
    assert load_trading_loop_cycles(path, tail=0) == []
    assert load_trading_loop_cycles(tmp_path / "fehlt.jsonl", tail=10) == []


def test_malformed_lines_are_still_skipped(tmp_path: Path) -> None:
    path = tmp_path / "trading_loop_audit.jsonl"
    _write_cycles(path, 10)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{kaputt\n\n")
    assert len(load_trading_loop_cycles(path, tail=100)) == 10


# ---------------------------------------------------------------------------
# Die Zusage: der Schnitt darf die Zahlen der Sonde nicht veraendern
# ---------------------------------------------------------------------------


def test_window_is_complete_when_tail_covers_it(tmp_path: Path) -> None:
    path = tmp_path / "trading_loop_audit.jsonl"
    _write_cycles(path, 200, spacing_minutes=10.0)  # 200 Saetze ueber ~33 h
    cutoff = NOW - timedelta(hours=24)
    rows = _load_cycles_for_window(path, cutoff)
    in_window = [r for r in rows if datetime.fromisoformat(str(r["started_at"])) >= cutoff]
    assert len(in_window) == 144  # 24 h / 10 min


def test_falls_back_to_full_read_when_the_window_is_truncated(tmp_path: Path, monkeypatch) -> None:
    """Die entscheidende Negativkontrolle.

    Reicht das Fenster nicht bis zum Cutoff, wuerde die Sonde zu wenige Zyklen
    zaehlen und FAELSCHLICH Alarm schlagen. Dann wird ungekuerzt nachgelesen.
    """
    path = tmp_path / "trading_loop_audit.jsonl"
    _write_cycles(path, 300, spacing_minutes=1.0)  # 300 Saetze in 5 h
    monkeypatch.setattr("app.alerts.health_check.CYCLE_PROBE_TAIL", 50)
    cutoff = NOW - timedelta(hours=24)
    rows = _load_cycles_for_window(path, cutoff)
    assert len(rows) == 300, "abgeschnittenes Fenster muss ungekuerzt nachgelesen werden"


def test_no_fallback_when_the_file_is_shorter_than_the_tail(tmp_path: Path) -> None:
    path = tmp_path / "trading_loop_audit.jsonl"
    _write_cycles(path, 20)
    assert len(_load_cycles_for_window(path, NOW - timedelta(hours=24))) == 20


def test_tail_is_measured_against_the_observed_daily_rate() -> None:
    """10.000 Saetze decken 4,6 Tage ab — gemessen an der hoechsten Tagesrate
    (2.178 am dichtesten von 149 Tagen; Median 1.126)."""
    assert CYCLE_PROBE_TAIL == 10_000
    assert CYCLE_PROBE_TAIL / 2178 > 4.0
