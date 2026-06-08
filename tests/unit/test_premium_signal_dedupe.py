"""Raw+Approved dedupe — SKYAI double-source must count as ONE signal."""

from __future__ import annotations

from app.observability.premium_dedupe import (
    compute_dedup_key,
    deduped_count,
    dedupe_premium_signals,
    normalized_raw_hash,
)

# Real SKYAI 2026-06-06 fixture: raw envelope + approval re-emit share signal_id,
# source_uid and message_id; only envelope_id differs (ENV-TG vs ENV-APP).
RAW_ENVELOPE = {
    "event": "telegram_channel_envelope",
    "source": "telegram_premium_channel",
    "envelope_id": "ENV-TG-001275462917-23891-467716b9",
    "source_uid": "telegram:-1001275462917:23891",
    "message_id": 23891,
    "chat_id": -1001275462917,
    "payload": {
        "signal_id": "SIG-TGCH-467716B9182A-SKYAIUSDT",
        "source": "telegram_premium_channel",
        "symbol": "SKYAIUSDT",
        "display_symbol": "SKYAI/USDT",
        "side": "buy",
        "direction": "long",
        "entry_value": 24800.0,
        "stop_loss": 23800.0,
        "targets": [24925.0, 25050.0, 25170.0, 25295.0],
        "leverage": 10,
        "source_uid": "telegram:-1001275462917:23891",
        "source_message_id": 23891,
        "timestamp_utc": "2026-06-06T15:33:29.174105+00:00",
    },
}

APPROVED_ENVELOPE = {
    "event": "telegram_channel_approval",
    "source": "telegram_premium_channel_approved",
    "envelope_id": "ENV-APP-telegram-1001275462917-23891-c022e39e",
    "origin_envelope_id": "ENV-TG-001275462917-23891-467716b9",
    "source_uid": "telegram:-1001275462917:23891",
    "message_id": 23891,
    "chat_id": -1001275462917,
    "payload": {
        "signal_id": "SIG-TGCH-467716B9182A-SKYAIUSDT",
        "source": "telegram_premium_channel_approved",
        "symbol": "SKYAIUSDT",
        "display_symbol": "SKYAI/USDT",
        "side": "buy",
        "direction": "long",
        "entry_value": 24800.0,
        "stop_loss": 23800.0,
        "targets": [24925.0, 25050.0, 25170.0, 25295.0],
        "leverage": 10,
        "source_uid": "telegram:-1001275462917:23891",
        "source_message_id": 23891,
        "timestamp_utc": "2026-06-06T15:33:29.608284+00:00",
    },
}


def test_raw_and_approved_share_dedup_key() -> None:
    assert compute_dedup_key(RAW_ENVELOPE) == compute_dedup_key(APPROVED_ENVELOPE)
    # strongest key is origin_signal_id
    assert compute_dedup_key(RAW_ENVELOPE) == "sig:SIG-TGCH-467716B9182A-SKYAIUSDT"


def test_raw_plus_approved_count_as_one_signal() -> None:
    assert deduped_count([RAW_ENVELOPE, APPROVED_ENVELOPE]) == 1


def test_group_is_double_sourced_with_both_events() -> None:
    groups = dedupe_premium_signals([RAW_ENVELOPE, APPROVED_ENVELOPE])
    assert len(groups) == 1
    g = groups[0]
    assert g.is_double_sourced is True
    assert g.raw_event is not None
    assert g.approved_event is not None
    # canonical = approved (the actionable one)
    assert g.canonical["source"] == "telegram_premium_channel_approved"
    summary = g.to_summary()
    assert summary["double_sourced"] is True
    assert summary["event_count"] == 2


def test_distinct_signals_not_merged() -> None:
    other = dict(APPROVED_ENVELOPE)
    other_payload = dict(APPROVED_ENVELOPE["payload"])
    other_payload["signal_id"] = "SIG-TGCH-OTHER-BTCUSDT"
    other["payload"] = other_payload
    other["source_uid"] = "telegram:-1001275462917:99999"
    other["message_id"] = 99999
    assert deduped_count([RAW_ENVELOPE, APPROVED_ENVELOPE, other]) == 2


def test_fallback_key_uses_source_uid_then_message_then_hash() -> None:
    no_sig = {
        "source": "telegram_premium_channel",
        "source_uid": "telegram:-100:5",
        "payload": {"symbol": "X/USDT", "side": "long", "entry_value": 1.0},
    }
    assert compute_dedup_key(no_sig) == "uid:telegram:-100:5"

    no_uid = {
        "source": "telegram_premium_channel",
        "message_id": 7,
        "chat_id": -100,
        "payload": {"symbol": "X/USDT", "side": "long", "entry_value": 1.0},
    }
    assert compute_dedup_key(no_uid) == "msg:-100:7"

    bare = {"source": "telegram_premium_channel", "payload": {"symbol": "X/USDT", "side": "long", "entry_value": 1.0}}
    assert compute_dedup_key(bare).startswith("raw:")


def test_normalized_raw_hash_stable_and_symbol_insensitive_to_slash() -> None:
    a = normalized_raw_hash({"payload": {"symbol": "SKYAIUSDT", "side": "buy", "entry_value": 24800.0}})
    b = normalized_raw_hash({"payload": {"display_symbol": "SKYAI/USDT", "side": "buy", "entry_value": 24800.0}})
    assert a == b
