"""G6 Task 6: elf bis dahin unbewachte, taktgetriebene Stroeme.

Die zwei Fehler, die hier verhindert werden, sind beide heute schon einmal
passiert: eine Schwelle, die in keiner ``files_to_check``-Zeile steht und
deshalb nie ausgewertet wird (der 0-Sentinel aus #817), und eine Schwelle, die
ab der ersten Minute feuert, weil der Strom laengst tot ist.
"""

from __future__ import annotations

from app.alerts.alert_classes import AlertClass, classify
from app.alerts.health_check import (
    _FRESHNESS_PER_FILE_MIN,
    _G6_TASK6_WATCHED,
    _check_data_freshness,
)

#: Groesster je gemessener Abstand je Strom (Pi, 2026-08-31, volle Historie).
MEASURED_MAX_GAP_HOURS = {
    "shadow_real_feed_funnel.jsonl": 0.5,
    "ln_reputation.jsonl": 1.6,
    "funding_evidence_shadow.jsonl": 6.6,
    "oi_evidence_shadow.jsonl": 6.6,
    "momentum_evidence_shadow.jsonl": 12.2,
    "momentum_crosscheck.jsonl": 12.0,
    "momentum_universe_candidates.jsonl": 24.1,
    "symbol_eligibility_audit.jsonl": 24.1,
    "kai_audit.jsonl": 24.0,
    "timer_health_audit.jsonl": 24.0,
    "onchain_fee_shadow.jsonl": 52.2,
}


def test_every_new_stream_has_a_threshold() -> None:
    for fname, _component in _G6_TASK6_WATCHED:
        assert fname in _FRESHNESS_PER_FILE_MIN, fname


def test_every_threshold_is_actually_evaluated(tmp_path) -> None:
    """Der 0-Sentinel-Fehler: eine Schwelle, die in keiner Pruefliste steht.

    ``_check_data_freshness`` liest ausschliesslich ``files_to_check``. Fehlt
    ein Strom dort, ist seine Zeile in der Tabelle Dekoration. Der Test misst
    das am Verhalten: eine ueberalterte Datei MUSS einen Befund erzeugen.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    for fname, component in _G6_TASK6_WATCHED:
        path = tmp_path / fname
        path.write_text("{}\n", encoding="utf-8")
        threshold = _FRESHNESS_PER_FILE_MIN[fname]
        stale = (now - timedelta(minutes=threshold + 60)).timestamp()
        import os

        os.utime(path, (stale, stale))
        issues, is_stale = _check_data_freshness(tmp_path, now)
        components = {i.component for i in issues}
        assert f"{component}_freshness" in components, (
            f"{fname}: Schwelle vorhanden, aber nie ausgewertet — sie steht in "
            "keiner files_to_check-Zeile"
        )
        assert is_stale
        path.unlink()


def test_thresholds_exceed_the_largest_measured_gap() -> None:
    """Keine Schwelle darf enger sein als das, was der Strom real getan hat."""
    for fname, max_gap_h in MEASURED_MAX_GAP_HOURS.items():
        threshold_min = _FRESHNESS_PER_FILE_MIN[fname]
        assert threshold_min > max_gap_h * 60, fname
        # ... und nicht absurd weit darueber: rund das Doppelte, nicht das Zehnfache.
        assert threshold_min <= max_gap_h * 60 * 4 + 120, fname


def test_all_new_components_are_class_p1() -> None:
    """``*_freshness`` ist stilles Versagen — die Klasse erbt sich, sie wird nicht gesetzt."""
    for _fname, component in _G6_TASK6_WATCHED:
        assert classify(f"{component}_freshness") is AlertClass.P1


def test_a_fresh_file_produces_no_finding(tmp_path) -> None:
    """Positivkontrolle: die neuen Waechter sind nicht dauerhaft rot."""
    import os
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    fresh = (now - timedelta(minutes=1)).timestamp()
    for fname, _component in _G6_TASK6_WATCHED:
        path = tmp_path / fname
        path.write_text("{}\n", encoding="utf-8")
        # mtime explizit setzen: die Systemzeit des Testlaufs ist nicht ``now``,
        # und eine Sonde mit injiziertem ``now`` gegen echte mtimes zu pruefen
        # misst die Uhr des Laeufers statt den Waechter.
        os.utime(path, (fresh, fresh))
    issues, _is_stale = _check_data_freshness(tmp_path, now)
    new_components = {f"{c}_freshness" for _f, c in _G6_TASK6_WATCHED}
    assert not ({i.component for i in issues} & new_components)


def test_missing_file_is_not_a_finding(tmp_path) -> None:
    """required=False: ein frischer Checkout hat keinen dieser Stroeme."""
    from datetime import UTC, datetime

    issues, _ = _check_data_freshness(tmp_path, datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    new_components = {f"{c}_freshness" for _f, c in _G6_TASK6_WATCHED}
    assert not ({i.component for i in issues} & new_components)
