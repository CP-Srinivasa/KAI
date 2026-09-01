"""Tests fuer die Zustell-Wache (G6, A4-017).

Der Befund, den diese Sonde schliesst, ist gemessen: von 19 FAIL-Alarmen des
Premium-Healthchecks in 30 Tagen erreichten 15 den Operator nie — alle mit
``Temporary failure in name resolution``, alle im naechtlichen Fenster
01:04-01:25. Die Tests bilden genau diesen Verlauf nach: Fehlschlag, Stille,
Nachlieferung im naechsten Lauf.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.alerts.alert_delivery import (
    HEARTBEAT_INTERVAL_S,
    classify_delivery,
    heartbeat_due,
    load_records,
    payload_digest,
    pending_payloads,
    prune_delivered,
    record_attempt,
    record_heartbeat,
)
from app.alerts.health_check import _check_alert_delivery

T0 = datetime(2026, 8, 13, 1, 4, tzinfo=UTC)
DNS_FAIL = "URLError: <urlopen error [Errno -3] Temporary failure in name resolution>"


def _attempt(path: Path, *, now: datetime, delivered: bool, text: str, reason: str) -> None:
    record_attempt(
        path,
        now=now,
        channel="telegram",
        alert_kind="premium_pipeline_fail",
        delivered=delivered,
        reason=reason,
        text=text,
    )


# --------------------------------------------------------------------------
# Strom + Nachlieferung
# --------------------------------------------------------------------------


def test_failed_attempt_stays_pending(tmp_path: Path) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    _attempt(path, now=T0, delivered=False, text="KAI premium-pipeline FAIL", reason=DNS_FAIL)
    pending = pending_payloads(load_records(path))
    assert len(pending) == 1
    assert pending[0]["reason"] == DNS_FAIL


def test_later_success_clears_the_same_alert(tmp_path: Path) -> None:
    """Die Nachlieferung im naechsten Lauf loescht den Eintrag — ueber den digest."""
    path = tmp_path / "alert_delivery_audit.jsonl"
    text = "KAI premium-pipeline FAIL @ 2026-08-13T01:04:00Z"
    _attempt(path, now=T0, delivered=False, text=text, reason=DNS_FAIL)
    _attempt(path, now=T0 + timedelta(minutes=1), delivered=True, text=text, reason="ok")
    assert pending_payloads(load_records(path)) == []


def test_success_for_a_different_alert_does_not_clear_the_first(tmp_path: Path) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    _attempt(path, now=T0, delivered=False, text="Alarm A", reason=DNS_FAIL)
    _attempt(path, now=T0 + timedelta(minutes=1), delivered=True, text="Alarm B", reason="ok")
    pending = pending_payloads(load_records(path))
    assert [p["digest"] for p in pending] == [payload_digest("Alarm A")]


def test_a_later_failure_keeps_it_pending(tmp_path: Path) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    text = "Alarm A"
    _attempt(path, now=T0, delivered=False, text=text, reason=DNS_FAIL)
    _attempt(path, now=T0 + timedelta(minutes=1), delivered=True, text=text, reason="ok")
    _attempt(path, now=T0 + timedelta(minutes=2), delivered=False, text=text, reason=DNS_FAIL)
    assert len(pending_payloads(load_records(path))) == 1


def test_corrupt_lines_are_skipped_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    _attempt(path, now=T0, delivered=True, text="ok", reason="ok")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{kaputt\n\n")
    assert len(load_records(path)) == 1


# --------------------------------------------------------------------------
# Urteil
# --------------------------------------------------------------------------


def test_empty_stream_is_ok(tmp_path: Path) -> None:
    verdict = classify_delivery(load_records(tmp_path / "nope.jsonl"), now=T0)
    assert verdict.status == "ok"
    assert verdict.undelivered == 0


def test_fresh_failure_is_not_yet_a_finding(tmp_path: Path) -> None:
    """Ein Netzwerk-Schluckauf ist kein Befund — der naechste Lauf liefert nach."""
    path = tmp_path / "alert_delivery_audit.jsonl"
    _attempt(path, now=T0, delivered=False, text="Alarm", reason=DNS_FAIL)
    verdict = classify_delivery(load_records(path), now=T0 + timedelta(minutes=2))
    assert verdict.status == "ok"


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(6, "warning"), (29, "warning"), (31, "critical"), (600, "critical")],
)
def test_age_decides_severity(tmp_path: Path, minutes: int, expected: str) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    _attempt(path, now=T0, delivered=False, text="Alarm", reason=DNS_FAIL)
    verdict = classify_delivery(load_records(path), now=T0 + timedelta(minutes=minutes))
    assert verdict.status == expected


def test_measured_dns_gap_does_not_trigger_critical(tmp_path: Path) -> None:
    """Die gemessene Luecke am 13.08. dauerte 10 min (01:04-01:13) — warning, nicht critical."""
    path = tmp_path / "alert_delivery_audit.jsonl"
    _attempt(path, now=T0, delivered=False, text="Alarm", reason=DNS_FAIL)
    verdict = classify_delivery(load_records(path), now=T0 + timedelta(minutes=10))
    assert verdict.status == "warning"


def test_reason_is_carried_into_the_verdict(tmp_path: Path) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    _attempt(path, now=T0, delivered=False, text="Alarm", reason=DNS_FAIL)
    verdict = classify_delivery(load_records(path), now=T0 + timedelta(minutes=40))
    assert verdict.reasons == (DNS_FAIL,)


# --------------------------------------------------------------------------
# Heartbeat (macht die Freshness-Zeile zu einer Aussage)
# --------------------------------------------------------------------------


def test_heartbeat_due_on_empty_stream(tmp_path: Path) -> None:
    assert heartbeat_due([], now=T0, interval_s=HEARTBEAT_INTERVAL_S) is True


def test_heartbeat_not_due_within_interval(tmp_path: Path) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    record_heartbeat(path, now=T0, channel="telegram")
    records = load_records(path)
    assert heartbeat_due(records, now=T0 + timedelta(minutes=30), interval_s=3600) is False
    assert heartbeat_due(records, now=T0 + timedelta(minutes=61), interval_s=3600) is True


def test_heartbeat_is_not_an_attempt(tmp_path: Path) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    record_heartbeat(path, now=T0, channel="telegram")
    assert pending_payloads(load_records(path)) == []
    assert classify_delivery(load_records(path), now=T0).status == "ok"


def test_prune_delivered_counts_but_deletes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "alert_delivery_audit.jsonl"
    _attempt(path, now=T0, delivered=True, text="alt", reason="ok")
    before = path.read_bytes()
    assert prune_delivered(load_records(path), now=T0 + timedelta(days=40), keep_days=30) == 1
    assert path.read_bytes() == before


# --------------------------------------------------------------------------
# Verdrahtung in den Health-Check
# --------------------------------------------------------------------------


def test_probe_is_silent_when_everything_arrived(tmp_path: Path) -> None:
    _attempt(tmp_path / "alert_delivery_audit.jsonl", now=T0, delivered=True, text="a", reason="ok")
    assert _check_alert_delivery(tmp_path, T0 + timedelta(hours=1)) == []


def test_probe_reports_the_lost_alert_with_reason(tmp_path: Path) -> None:
    _attempt(
        tmp_path / "alert_delivery_audit.jsonl",
        now=T0,
        delivered=False,
        text="Alarm",
        reason=DNS_FAIL,
    )
    issues = _check_alert_delivery(tmp_path, T0 + timedelta(minutes=45))
    assert len(issues) == 1
    assert issues[0].severity == "critical"
    assert issues[0].component == "alert_delivery"
    assert "name resolution" in issues[0].message


def test_probe_survives_a_missing_stream(tmp_path: Path) -> None:
    assert _check_alert_delivery(tmp_path, T0) == []


def test_delivery_stream_has_a_freshness_line() -> None:
    """Positivkontrolle: ohne diese Zeile saehe ein toter Zustellpfad ruhig aus."""
    from app.alerts.health_check import _FRESHNESS_PER_FILE_MIN

    assert _FRESHNESS_PER_FILE_MIN["alert_delivery_audit.jsonl"] == 180
