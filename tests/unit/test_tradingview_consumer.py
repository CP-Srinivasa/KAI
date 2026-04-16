"""Tests for TV-4 promoted-signal consumer (app.signals.tradingview_consumer).

Covers the four invariants:
- Flag-off no-op: disabled consumer writes nothing, returns [].
- Missing promoted log: enabled consumer returns [] (no error).
- Happy path: each well-formed promoted row becomes one audit row.
- Idempotency: replaying the consumer appends nothing new.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.settings import TradingViewSettings
from app.signals.tradingview_consumer import consume_promoted_signals


def _make_settings(
    tmp_path: Path,
    *,
    enabled: bool,
    promoted_name: str = "tv_promoted.jsonl",
    audit_name: str = "tv_audit.jsonl",
    require_token: bool = False,
) -> TradingViewSettings:
    kwargs: dict[str, object] = {
        "promoted_consumer_enabled": enabled,
        "promoted_signals_log": str(tmp_path / promoted_name),
        "promoted_signal_audit_log": str(tmp_path / audit_name),
        "webhook_auth_mode": "hmac",
    }
    if require_token:
        kwargs["webhook_auth_mode"] = "shared_token"
        kwargs["webhook_shared_token"] = "t"
    return TradingViewSettings(_env_file=None, **kwargs)


def _promoted_row(decision_id: str, *, symbol: str = "BTCUSDT") -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "timestamp_utc": "2026-04-16T10:00:00+00:00",
        "symbol": symbol,
        "market": "crypto",
        "venue": "paper",
        "mode": "paper",
        "direction": "long",
        "entry_price": 45000.0,
        "confidence_score": 0.75,
        "source_document_id": f"req_{decision_id}",
        "provenance": {
            "source": "tradingview_webhook",
            "version": "tv-3.1",
            "signal_path_id": "tv-path-1",
        },
    }


def _write_promoted(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_consumer_disabled_is_noop(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, enabled=False)
    promoted = Path(settings.promoted_signals_log)
    _write_promoted(promoted, [_promoted_row("dec_aaaa")])

    result = consume_promoted_signals(settings)

    assert result == []
    audit = Path(settings.promoted_signal_audit_log)
    assert not audit.exists(), "disabled consumer must not create audit file"


def test_consumer_missing_promoted_log_returns_empty(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, enabled=True)
    assert not Path(settings.promoted_signals_log).exists()

    result = consume_promoted_signals(settings)

    assert result == []
    assert not Path(settings.promoted_signal_audit_log).exists()


def test_consumer_happy_path_appends_audit_rows(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, enabled=True)
    promoted = Path(settings.promoted_signals_log)
    _write_promoted(
        promoted,
        [
            _promoted_row("dec_aaaa", symbol="BTCUSDT"),
            _promoted_row("dec_bbbb", symbol="ETHUSDT"),
        ],
    )

    result = consume_promoted_signals(settings, now_iso="2026-04-16T11:00:00+00:00")

    assert [c.decision_id for c in result] == ["dec_aaaa", "dec_bbbb"]
    assert all(c.consumed_at == "2026-04-16T11:00:00+00:00" for c in result)
    assert result[0].signal_path_id == "tv-path-1"

    audit = Path(settings.promoted_signal_audit_log)
    written = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert [r["decision_id"] for r in written] == ["dec_aaaa", "dec_bbbb"]
    assert written[0]["symbol"] == "BTCUSDT"
    assert written[0]["entry_price"] == 45000.0


def test_consumer_is_idempotent_on_replay(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, enabled=True)
    _write_promoted(Path(settings.promoted_signals_log), [_promoted_row("dec_aaaa")])

    first = consume_promoted_signals(settings)
    second = consume_promoted_signals(settings)

    assert len(first) == 1
    assert second == []
    audit = Path(settings.promoted_signal_audit_log)
    assert len(audit.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_consumer_picks_up_new_rows_added_after_first_run(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, enabled=True)
    promoted = Path(settings.promoted_signals_log)
    _write_promoted(promoted, [_promoted_row("dec_aaaa")])
    consume_promoted_signals(settings)

    with promoted.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_promoted_row("dec_bbbb", symbol="ETHUSDT")) + "\n")

    result = consume_promoted_signals(settings)

    assert [c.decision_id for c in result] == ["dec_bbbb"]
    audit_rows = Path(settings.promoted_signal_audit_log).read_text(encoding="utf-8").strip().splitlines()
    assert len(audit_rows) == 2


def test_consumer_skips_malformed_and_incomplete_rows(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, enabled=True)
    promoted = Path(settings.promoted_signals_log)
    good = _promoted_row("dec_good")
    missing_entry = _promoted_row("dec_no_price")
    del missing_entry["entry_price"]
    with promoted.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(good) + "\n")
        fh.write("not-json\n")
        fh.write(json.dumps(missing_entry) + "\n")
        fh.write(json.dumps({"only": "garbage"}) + "\n")

    result = consume_promoted_signals(settings)

    assert [c.decision_id for c in result] == ["dec_good"]


def test_consumer_settings_default_is_fail_closed() -> None:
    # Pure settings contract check: the flag must default to False so a
    # vanilla deployment cannot accidentally enable consumption.
    try:
        settings = TradingViewSettings(_env_file=None)
    except ValidationError:
        pytest.fail("TradingViewSettings default construction must succeed")
    assert settings.promoted_consumer_enabled is False
