"""Tests fuer die Zustellung im Healthcheck-Skript (G6, A4-017).

Der reale Verlauf, den diese Tests nachbilden: am 13.08. scheiterten zehn
Sendeversuche in Folge zwischen 01:04 und 01:13 an der Namensaufloesung, und
kein einziger Alarm wurde je nachgeliefert — es gab nichts, das ihn aufhob.
Der Timer laeuft jede Minute; die Nachlieferung im naechsten Lauf ist deshalb
der Mechanismus, der 15 von 19 verlorenen Alarmen gerettet haette.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.alerts.alert_delivery import classify_delivery, load_records, pending_payloads

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "premium_pipeline_healthcheck.py"
DNS_FAIL = "URLError: <urlopen error [Errno -3] Temporary failure in name resolution>"


@pytest.fixture
def script(tmp_path: Path) -> Iterator[object]:
    """Skript als Modul laden und seinen Zustell-Strom in den tmp_path umlenken."""
    spec = importlib.util.spec_from_file_location("premium_healthcheck_delivery_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["premium_healthcheck_delivery_test"] = module
    spec.loader.exec_module(module)
    module._DELIVERY_PATH = tmp_path / "alert_delivery_audit.jsonl"
    try:
        yield module
    finally:
        sys.modules.pop("premium_healthcheck_delivery_test", None)


def test_failed_send_is_recorded_with_its_reason(script, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "_send_telegram", lambda text: (False, DNS_FAIL))
    assert script._deliver("KAI premium-pipeline FAIL", alert_kind="premium_pipeline_fail") is False

    records = load_records(script._DELIVERY_PATH)
    assert len(records) == 1
    assert records[0]["delivered"] is False
    assert records[0]["reason"] == DNS_FAIL
    # Der Text bleibt im Satz — ohne ihn kann der naechste Lauf nichts nachliefern.
    assert records[0]["text"] == "KAI premium-pipeline FAIL"


def test_next_run_redelivers_what_the_dns_gap_swallowed(
    script, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Lauf um 01:04 scheitert, der Lauf um 01:05 liefert nach."""
    monkeypatch.setattr(script, "_send_telegram", lambda text: (False, DNS_FAIL))
    script._deliver("Alarm", alert_kind="premium_pipeline_fail")
    assert len(pending_payloads(load_records(script._DELIVERY_PATH))) == 1

    monkeypatch.setattr(script, "_send_telegram", lambda text: (True, "ok"))
    assert script._flush_pending() == 1
    assert pending_payloads(load_records(script._DELIVERY_PATH)) == []


def test_redelivery_counts_the_attempt(script, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "_send_telegram", lambda text: (False, DNS_FAIL))
    script._deliver("Alarm", alert_kind="premium_pipeline_fail")
    script._flush_pending()
    attempts = [r["attempt"] for r in load_records(script._DELIVERY_PATH)]
    assert attempts == [1, 2]


def test_persistent_outage_stays_a_finding(script, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negativkontrolle: Nachliefern darf einen Dauerausfall nicht wegwischen."""
    monkeypatch.setattr(script, "_send_telegram", lambda text: (False, DNS_FAIL))
    script._deliver("Alarm", alert_kind="premium_pipeline_fail")
    for _ in range(3):
        assert script._flush_pending() == 0

    records = load_records(script._DELIVERY_PATH)
    assert len(pending_payloads(records)) == 1
    from datetime import UTC, datetime, timedelta

    later = datetime.now(UTC) + timedelta(minutes=45)
    assert classify_delivery(records, now=later).status == "critical"


def test_flush_is_a_noop_on_an_empty_stream(script, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(script, "_send_telegram", lambda text: (sent.append(text), (True, "ok"))[1])
    assert script._flush_pending() == 0
    assert sent == []


def test_send_telegram_reports_a_reason_when_credentials_are_missing(
    script, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALERT_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("ALERT_TELEGRAM_CHAT_ID", raising=False)
    delivered, reason = script._send_telegram("x")
    assert delivered is False
    assert reason == "token_or_chat_id_missing"
