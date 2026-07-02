"""V2 (P0): per-symbol post-stop cooldown helper.

Root-cause (NEO-F-202): no per-symbol cooldown after a stop-out. The same
symbols get re-entered and re-stopped within hours, each round-trip bleeding
~1.2% in fees. The robust existing source for "last stop time per symbol" is
the paper-execution audit JSONL (`position_closed` events with reason=stop) —
no new persistence is introduced.

Contract under test (behaviour):
- cooldown_minutes <= 0 disables the gate (returns not-in-cooldown always).
- a symbol stopped within the window is in cooldown.
- a symbol whose last stop is older than the window is NOT in cooldown.
- only reason=stop counts (a `take` close does not start a cooldown).
- the most-recent stop wins when a symbol has several.
- unknown symbol / missing or empty audit file => not in cooldown (fail-open
  on read, because a hard fail here would deadlock the loop — and a missing
  cooldown only loses a guardrail, it does not risk a worse-than-baseline trade).
- malformed lines are skipped, not fatal.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.risk.post_stop_cooldown import is_symbol_in_post_stop_cooldown


def _write_audit(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )


def _stop_event(symbol: str, ts: datetime) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts.isoformat(),
        "symbol": symbol,
        "reason": "stop",
    }


def _now() -> datetime:
    return datetime.now(UTC)


# --- disabled ---


def test_zero_window_disables_gate(tmp_path):
    audit = tmp_path / "paper.jsonl"
    _write_audit(audit, [_stop_event("BTC/USDT", _now())])
    assert not is_symbol_in_post_stop_cooldown(
        "BTC/USDT", cooldown_minutes=0, audit_path=audit, now=_now()
    )


def test_negative_window_disables_gate(tmp_path):
    audit = tmp_path / "paper.jsonl"
    _write_audit(audit, [_stop_event("BTC/USDT", _now())])
    assert not is_symbol_in_post_stop_cooldown(
        "BTC/USDT", cooldown_minutes=-5, audit_path=audit, now=_now()
    )


# --- normal case ---


def test_recent_stop_is_in_cooldown(tmp_path):
    now = _now()
    audit = tmp_path / "paper.jsonl"
    _write_audit(audit, [_stop_event("ETH/USDT", now - timedelta(minutes=30))])
    assert is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )


def test_old_stop_not_in_cooldown(tmp_path):
    now = _now()
    audit = tmp_path / "paper.jsonl"
    _write_audit(audit, [_stop_event("ETH/USDT", now - timedelta(minutes=200))])
    assert not is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )


def test_other_symbol_not_affected(tmp_path):
    now = _now()
    audit = tmp_path / "paper.jsonl"
    _write_audit(audit, [_stop_event("ETH/USDT", now - timedelta(minutes=5))])
    assert not is_symbol_in_post_stop_cooldown(
        "BTC/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )


# --- boundary ---


def test_stop_exactly_at_window_edge_not_in_cooldown(tmp_path):
    """Exactly at the window boundary: cooldown has elapsed (strict <)."""
    now = _now()
    audit = tmp_path / "paper.jsonl"
    _write_audit(audit, [_stop_event("ETH/USDT", now - timedelta(minutes=180))])
    assert not is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )


def test_most_recent_stop_wins(tmp_path):
    now = _now()
    audit = tmp_path / "paper.jsonl"
    _write_audit(
        audit,
        [
            _stop_event("ETH/USDT", now - timedelta(minutes=300)),  # old
            _stop_event("ETH/USDT", now - timedelta(minutes=10)),  # recent
        ],
    )
    assert is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )


# --- semantics: only stop closes start a cooldown ---


def test_take_profit_close_does_not_start_cooldown(tmp_path):
    now = _now()
    audit = tmp_path / "paper.jsonl"
    take_event = _stop_event("ETH/USDT", now - timedelta(minutes=5))
    take_event["reason"] = "take"
    _write_audit(audit, [take_event])
    assert not is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )


def test_non_position_closed_event_ignored(tmp_path):
    now = _now()
    audit = tmp_path / "paper.jsonl"
    _write_audit(
        audit,
        [
            {
                "event_type": "order_created",
                "timestamp_utc": (now - timedelta(minutes=5)).isoformat(),
                "symbol": "ETH/USDT",
            }
        ],
    )
    assert not is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )


# --- error / robustness (fail-open on read) ---


def test_missing_file_not_in_cooldown(tmp_path):
    audit = tmp_path / "does_not_exist.jsonl"
    assert not is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=_now()
    )


def test_empty_file_not_in_cooldown(tmp_path):
    audit = tmp_path / "paper.jsonl"
    audit.write_text("", encoding="utf-8")
    assert not is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=_now()
    )


def test_malformed_lines_skipped(tmp_path):
    now = _now()
    audit = tmp_path / "paper.jsonl"
    good = json.dumps(_stop_event("ETH/USDT", now - timedelta(minutes=10)))
    audit.write_text(
        "not json\n" + good + "\n{partial\n",
        encoding="utf-8",
    )
    assert is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )


def test_event_without_timestamp_skipped(tmp_path):
    now = _now()
    audit = tmp_path / "paper.jsonl"
    bad = {"event_type": "position_closed", "symbol": "ETH/USDT", "reason": "stop"}
    _write_audit(audit, [bad])
    assert not is_symbol_in_post_stop_cooldown(
        "ETH/USDT", cooldown_minutes=180, audit_path=audit, now=now
    )
