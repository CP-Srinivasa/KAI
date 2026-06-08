from __future__ import annotations

from app.observability.premium_signal_dedupe import (
    RAW_APPROVED_DEDUPED_EVENT,
    dedupe_premium_signal_records,
    premium_signal_dedupe_key,
)


def _record(
    env_id: str,
    source: str,
    *,
    source_uid: str | None = "telegram:-1001:23878",
    message_id: int | None = 23878,
    origin_signal_id: str | None = None,
) -> dict:
    payload = {
        "display_symbol": "SKYAI/USDT",
        "side": "buy",
        "direction": "long",
        "entry_value": 0.40,
        "stop_loss": 0.30,
        "targets": [0.50, 0.60],
        "leverage": 10,
    }
    if source_uid is not None:
        payload["source_uid"] = source_uid
        payload["source_message_id"] = message_id
    if origin_signal_id is not None:
        payload["origin_signal_id"] = origin_signal_id
    return {
        "timestamp_utc": "2026-06-08T10:01:00+00:00",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": source,
        "envelope_id": env_id,
        **({"source_uid": source_uid} if source_uid is not None else {}),
        **({"message_id": message_id} if message_id is not None else {}),
        "payload": payload,
    }


def test_dedupe_key_prefers_origin_signal_id() -> None:
    rec = _record(
        "ENV-A",
        "telegram_premium_channel",
        source_uid="telegram:-1001:1",
        origin_signal_id="origin-123",
    )
    assert premium_signal_dedupe_key(rec) == "premium:0:origin-123"


def test_raw_and_approved_count_as_one_signal() -> None:
    raw = _record("ENV-RAW", "telegram_premium_channel")
    approved = _record("ENV-APPROVED", "telegram_premium_channel_approved")

    result = dedupe_premium_signal_records([raw, approved])

    assert result.duplicates == 1
    assert result.audit_event == RAW_APPROVED_DEDUPED_EVENT
    assert len(result.records) == 1
    assert result.records[0]["source"] == "telegram_premium_channel_approved"


def test_structural_fallback_groups_raw_and_approved_without_telegram_identity() -> None:
    raw = _record("ENV-RAW", "telegram_premium_channel", source_uid=None, message_id=None)
    approved = _record(
        "ENV-APPROVED",
        "telegram_premium_channel_approved",
        source_uid=None,
        message_id=None,
    )

    result = dedupe_premium_signal_records([raw, approved])

    assert result.duplicates == 1
    assert len(result.records) == 1
    assert result.records[0]["envelope_id"] == "ENV-APPROVED"
