"""Tests for _summarize_approval_latency_24h — operator-click latency for /status.

Replaces the manual JSONL pull that the TTL-decision (2026-05-07) was previously
gated on. The summary joins the approval-send audit (when the bot posted a card)
with envelope decisions (when the operator clicked or TTL expired) by envelope
id, and surfaces p50/p90/p99 plus decision-rate so the operator can see whether
TTL itself is binding (raise TTL) versus operators missing the click (add
reminder).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.agents.tools.canonical_read import _summarize_approval_latency_24h


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _send(env_id: str, ts: datetime) -> dict[str, Any]:
    return {
        "timestamp_utc": ts.isoformat(),
        "event": "telegram_approval_send",
        "stage": "approval_sent",
        "envelope_id": env_id,
        "chat_id": -100123,
        "bot_message_id": 9001,
        "status": "ok",
        "ttl_minutes": 60,
    }


def _decision(
    origin_id: str,
    stage: str,
    ts: datetime,
) -> dict[str, Any]:
    return {
        "timestamp_utc": ts.isoformat(),
        "event": "telegram_channel_approval",
        "message_type": "signal",
        "stage": stage,
        "status": "ok",
        "source": "telegram_premium_channel",
        "origin_envelope_id": origin_id,
    }


def test_missing_send_log_returns_status_missing(tmp_path: Path) -> None:
    result = _summarize_approval_latency_24h(
        now=datetime.now(UTC),
        send_audit_path_override=str(tmp_path / "absent.jsonl"),
        envelope_path_override=str(tmp_path / "envs.jsonl"),
    )
    assert result["status"] == "missing"
    assert "send-audit log not found" in str(result["reason"])


def test_no_sends_in_24h_returns_zero_buckets(tmp_path: Path) -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    send_path = tmp_path / "send.jsonl"
    # Send is 48h old → outside 24h window
    _write_jsonl(
        send_path,
        [_send("ENV-A", now - timedelta(hours=48))],
    )
    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(tmp_path / "envs.jsonl"),
    )
    assert result["status"] == "no_sends_24h"
    assert result["sent"] == 0
    assert result["decision_rate_pct"] is None


def test_decided_filled_populates_latency_bucket(tmp_path: Path) -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    sent_at = now - timedelta(minutes=30)
    decided_at = sent_at + timedelta(seconds=120)  # 2-min latency

    send_path = tmp_path / "send.jsonl"
    env_path = tmp_path / "env.jsonl"
    _write_jsonl(send_path, [_send("ENV-A", sent_at)])
    _write_jsonl(env_path, [_decision("ENV-A", "accepted", decided_at)])

    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(env_path),
    )
    assert result["status"] == "ok"
    assert result["sent"] == 1
    assert result["decided"] == 1
    assert result["expired"] == 0
    assert result["still_open"] == 0
    assert result["p50_seconds"] == 120.0
    assert result["decision_rate_pct"] == 100.0


def test_ignored_counts_as_decided(tmp_path: Path) -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    sent_at = now - timedelta(minutes=30)
    ignored_at = sent_at + timedelta(seconds=45)

    send_path = tmp_path / "send.jsonl"
    env_path = tmp_path / "env.jsonl"
    _write_jsonl(send_path, [_send("ENV-A", sent_at)])
    _write_jsonl(env_path, [_decision("ENV-A", "ignored", ignored_at)])

    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(env_path),
    )
    # Ignored is also a real decision — it just chose the negative branch.
    # Operator-Latenz misst die Reaktion, nicht das Ergebnis.
    assert result["decided"] == 1
    assert result["expired"] == 0
    assert result["p50_seconds"] == 45.0


def test_expired_separated_from_decided(tmp_path: Path) -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    sent_at = now - timedelta(minutes=90)
    expired_at = sent_at + timedelta(minutes=60)  # 1h after send

    send_path = tmp_path / "send.jsonl"
    env_path = tmp_path / "env.jsonl"
    _write_jsonl(send_path, [_send("ENV-A", sent_at)])
    _write_jsonl(env_path, [_decision("ENV-A", "expired", expired_at)])

    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(env_path),
    )
    assert result["sent"] == 1
    assert result["decided"] == 0
    assert result["expired"] == 1
    # Expired must NOT pollute the decision-latency percentiles.
    assert result["p50_seconds"] is None
    assert result["decision_rate_pct"] == 0.0


def test_still_open_when_no_decision_yet(tmp_path: Path) -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    send_path = tmp_path / "send.jsonl"
    env_path = tmp_path / "env.jsonl"
    _write_jsonl(send_path, [_send("ENV-A", now - timedelta(minutes=10))])
    _write_jsonl(env_path, [])  # no decisions

    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(env_path),
    )
    assert result["still_open"] == 1
    assert result["decided"] == 0
    assert result["expired"] == 0


def test_percentiles_on_mixed_latencies(tmp_path: Path) -> None:
    # Five decided clicks at 10s, 30s, 60s, 180s, 600s.
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    base_send = now - timedelta(hours=2)

    sends: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for i, lat_s in enumerate([10, 30, 60, 180, 600]):
        env_id = f"ENV-{i:03d}"
        sent_at = base_send + timedelta(minutes=i)
        decided_at = sent_at + timedelta(seconds=lat_s)
        sends.append(_send(env_id, sent_at))
        decisions.append(_decision(env_id, "accepted", decided_at))

    send_path = tmp_path / "send.jsonl"
    env_path = tmp_path / "env.jsonl"
    _write_jsonl(send_path, sends)
    _write_jsonl(env_path, decisions)

    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(env_path),
    )
    assert result["decided"] == 5
    # nearest-rank: p50 of [10,30,60,180,600] → idx round(0.5*4)=2 → 60
    assert result["p50_seconds"] == 60.0
    # p90 → idx round(0.9*4)=4 → 600
    assert result["p90_seconds"] == 600.0
    # p99 → idx round(0.99*4)=4 → 600
    assert result["p99_seconds"] == 600.0


def test_failed_send_records_excluded(tmp_path: Path) -> None:
    # Send-audit can carry failed records (status=failed). They are not
    # operator-visible cards — exclude from the denominator entirely.
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    sent_at = now - timedelta(minutes=15)

    send_path = tmp_path / "send.jsonl"
    failed_send = _send("ENV-FAIL", sent_at)
    failed_send["status"] = "failed"
    failed_send["failure_reason"] = "http_error"
    _write_jsonl(send_path, [failed_send, _send("ENV-OK", sent_at)])

    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(tmp_path / "env.jsonl"),
    )
    # Only ENV-OK survived the filter
    assert result["sent"] == 1
    assert result["still_open"] == 1


def test_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    send_path = tmp_path / "send.jsonl"
    send_path.parent.mkdir(parents=True, exist_ok=True)
    with send_path.open("w", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps(_send("ENV-A", now - timedelta(minutes=5))) + "\n")

    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(tmp_path / "env.jsonl"),
    )
    # Corrupt line tolerated, valid line counted.
    assert result["sent"] == 1


def test_duplicate_sends_keep_latest(tmp_path: Path) -> None:
    # Telegram retries can produce two send-audit rows per envelope.
    # Latency must be measured against the *latest* send (the one the
    # operator actually saw).
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    first_send = now - timedelta(minutes=20)
    second_send = now - timedelta(minutes=10)
    decided_at = second_send + timedelta(seconds=30)

    send_path = tmp_path / "send.jsonl"
    env_path = tmp_path / "env.jsonl"
    _write_jsonl(
        send_path,
        [_send("ENV-A", first_send), _send("ENV-A", second_send)],
    )
    _write_jsonl(env_path, [_decision("ENV-A", "accepted", decided_at)])

    result = _summarize_approval_latency_24h(
        now=now,
        send_audit_path_override=str(send_path),
        envelope_path_override=str(env_path),
    )
    # Latency from second send → 30s, not from first send → 630s.
    assert result["decided"] == 1
    assert result["p50_seconds"] == 30.0
