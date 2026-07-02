"""Premium-fastlane backfill / reprocess (Goal 2026-06-05 §7/§8/§16).

Pins: reprocess past a prior terminal stage; non-fatal scale-hint → pending;
TTL ignored for paper backfill; idempotent re-run (no double position); no live
order; the four-symbol batch produces only pending/open.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import app.execution.envelope_to_paper_bridge as bridge
from app.execution.envelope_to_paper_bridge import backfill_run


@pytest.fixture
def tmp_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(bridge, "_ENVELOPE_LOG", tmp_path / "telegram_message_envelope.jsonl")
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", tmp_path / "bridge_pending_orders.jsonl")
    monkeypatch.setattr(bridge, "_PAPER_AUDIT_LOG", tmp_path / "paper_execution_audit.jsonl")
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _env(
    *,
    env_id: str,
    symbol: str,
    entry_type: str,
    entry: float,
    sl: float,
    targets: list[float],
    msg_id: int,
    ts: str | None = None,
) -> dict[str, Any]:
    return {
        "envelope_id": env_id,
        "stage": "accepted",
        "status": "ok",
        "message_type": "signal",
        "source": "telegram_premium_channel_approved",
        "source_uid": f"telegram:-1001:{msg_id}",
        "timestamp_utc": ts or datetime.now(UTC).isoformat(),
        "payload": {
            "direction": "long",
            "side": "buy",
            "symbol": symbol.replace("/", ""),
            "display_symbol": symbol,
            "entry_type": entry_type,
            "entry_value": entry,
            "stop_loss": sl,
            "targets": targets,
            "leverage": 10,
            "source_uid": f"telegram:-1001:{msg_id}",
            "source_chat_id": -1001,
            "source_message_id": msg_id,
        },
    }


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    # Issue #181: bypasses are fail-closed; backfill routes premium (not on the
    # classic allowlist) via the explicitly-armed source-allowlist bypass, and
    # fills at-entry signals via the two-flag entry-mode override.
    monkeypatch.setenv("PREMIUM_FASTLANE_BYPASS_SOURCE_ALLOWLIST", "true")
    monkeypatch.setenv("PREMIUM_FASTLANE_BYPASS_ENTRY_MODE_FOR_PAPER", "true")
    monkeypatch.setenv("PREMIUM_FASTLANE_ALLOW_ENTRY_MODE_DISABLED_OVERRIDE", "true")


def _price(value: float):
    async def _p(_symbol: str) -> float:
        return value

    return _p


@pytest.mark.asyncio
async def test_reprocess_past_terminal_scale_review_to_pending(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLO-like: SL above current spot is a MARKET reason → fastlane keeps it
    pending instead of terminally rejecting; works even though a prior terminal
    rejected_scale_review exists."""
    _enable(monkeypatch)
    env = _env(
        env_id="ENV-CLO",
        symbol="CLO/USDT",
        entry_type="above",
        entry=16860,
        sl=16185,
        targets=[16945, 17030, 17110, 17195],
        msg_id=23887,
    )
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", env)
    # prior terminal stage from the pre-fastlane run:
    _write(
        tmp_artifacts / "bridge_pending_orders.jsonl",
        {
            "envelope_id": "ENV-CLO",
            "stage": "rejected_scale_review",
            "reason": "long_sl_at_or_above_spot",
            "timestamp_utc": "2026-06-05T09:00:00+00:00",
        },
    )

    result = await backfill_run(symbols=["CLO/USDT"], price_provider=_price(0.15995))

    assert result.rejected_size == 0, result.to_dict()
    assert result.newly_pending == 1, result.to_dict()
    records = _read(tmp_artifacts / "bridge_pending_orders.jsonl")
    last = records[-1]
    assert last["stage"] == "pending"
    assert last["scale_hint"] == "long_sl_at_or_above_spot"
    assert last["order_intent"]["symbol"] == "CLO/USDT"


@pytest.mark.asyncio
async def test_structural_scale_error_still_terminal(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine scale collapse (SL above ENTRY) stays a hard reject even in
    fastlane — it is a structural geometry error, not a market condition."""
    _enable(monkeypatch)
    # SL > entry after any scale → long_sl_at_or_above_entry (structural).
    env = _env(
        env_id="ENV-BAD",
        symbol="BAD/USDT",
        entry_type="at",
        entry=100,
        sl=120,
        targets=[130],
        msg_id=999,
    )
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", env)
    result = await backfill_run(symbols=["BAD/USDT"], price_provider=_price(100.0))
    assert result.rejected_size == 1, result.to_dict()
    assert result.newly_pending == 0


@pytest.mark.asyncio
async def test_ttl_ignored_for_paper_backfill(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    old_ts = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    env = _env(
        env_id="ENV-OLD",
        symbol="BEAT/USDT",
        entry_type="at",
        entry=1.6810,
        sl=1.6130,
        targets=[1.6895, 1.6980],
        msg_id=23888,
        ts=old_ts,
    )
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", env)
    # spot above entry → pending (not expired because ignore_ttl default True)
    result = await backfill_run(symbols=["BEAT/USDT"], price_provider=_price(1.7451))
    assert result.expired == 0, result.to_dict()
    assert result.newly_pending == 1, result.to_dict()
    # ttl-backfill-allowed audit present
    stages = [r.get("event") for r in _read(tmp_artifacts / "bridge_pending_orders.jsonl")]
    assert "premium_fastlane_ttl_expired_but_backfill_allowed_for_paper" in stages


@pytest.mark.asyncio
async def test_backfill_skips_signal_older_than_max_age(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A4: even with ignore_ttl, a signal older than backfill_max_age_hours is
    NOT re-injected — filling at a long-stale entry price would distort the
    canonical paper edge. It is skipped, counted as expired, and audited."""
    _enable(monkeypatch)
    monkeypatch.setenv("PREMIUM_FASTLANE_BACKFILL_MAX_AGE_HOURS", "24")
    old_ts = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    env = _env(
        env_id="ENV-ANCIENT",
        symbol="BEAT/USDT",
        entry_type="at",
        entry=1.6810,
        sl=1.6130,
        targets=[1.6895, 1.6980],
        msg_id=23999,
        ts=old_ts,
    )
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", env)
    result = await backfill_run(symbols=["BEAT/USDT"], price_provider=_price(1.7451))
    assert result.newly_pending == 0, result.to_dict()
    assert result.filled == 0, result.to_dict()
    assert result.expired == 1, result.to_dict()
    events = [r.get("event") for r in _read(tmp_artifacts / "bridge_pending_orders.jsonl")]
    assert "premium_fastlane_backfill_skipped_too_old" in events


@pytest.mark.asyncio
async def test_run_tick_only_envelope_id_narrows_scan(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A3: reprocess with envelope_id narrows the bridge tick to that single
    pending envelope (truth: the UI 'reprocess this one' now does what it says).
    Narrowing can only reduce the scanned set, never widen it."""
    from app.execution.envelope_to_paper_bridge import run_tick

    _enable(monkeypatch)
    for env_id, symbol, msg in (
        ("ENV-ONE", "ZZZ/USDT", 701),
        ("ENV-TWO", "YYY/USDT", 702),
    ):
        _write(
            tmp_artifacts / "telegram_message_envelope.jsonl",
            _env(
                env_id=env_id,
                symbol=symbol,
                entry_type="at",
                entry=10.0,
                sl=9.0,
                targets=[11.0],
                msg_id=msg,
            ),
        )

    targeted = await run_tick(only_envelope_id="ENV-ONE", price_provider=_price(10.0))
    assert targeted.envelopes_scanned == 1, targeted.to_dict()

    missing = await run_tick(only_envelope_id="DOES-NOT-EXIST", price_provider=_price(10.0))
    assert missing.envelopes_scanned == 0, missing.to_dict()


@pytest.mark.asyncio
async def test_backfill_fill_is_idempotent(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    # at-entry, spot at entry, SL below spot → fills.
    env = _env(
        env_id="ENV-FILL",
        symbol="ZZZ/USDT",
        entry_type="at",
        entry=10.0,
        sl=9.0,
        targets=[11.0, 12.0],
        msg_id=555,
    )
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", env)

    r1 = await backfill_run(symbols=["ZZZ/USDT"], price_provider=_price(10.0))
    assert r1.filled == 1, r1.to_dict()

    r2 = await backfill_run(symbols=["ZZZ/USDT"], price_provider=_price(10.0))
    assert r2.filled == 0, r2.to_dict()
    assert r2.rejected_position_exists == 1, r2.to_dict()
    # only one real fill in the paper audit
    paper = _read(tmp_artifacts / "artifacts" / "paper_execution_audit.jsonl")
    fills = [r for r in paper if r.get("event_type") == "order_filled"]
    assert len(fills) == 1


@pytest.mark.asyncio
async def test_no_live_order_ever(tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    env = _env(
        env_id="ENV-LIVE",
        symbol="ZZZ/USDT",
        entry_type="at",
        entry=10.0,
        sl=9.0,
        targets=[11.0],
        msg_id=556,
    )
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", env)
    await backfill_run(symbols=["ZZZ/USDT"], price_provider=_price(10.0))
    paper = tmp_artifacts / "artifacts" / "paper_execution_audit.jsonl"
    text = paper.read_text() if paper.exists() else ""
    assert '"venue": "live"' not in text and '"live": true' not in text


@pytest.mark.asyncio
async def test_four_signal_batch_all_pending_or_open(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four missed signals, with a representative spot each, all reach
    pending/open — none terminally scale-rejected, no live, no errors."""
    _enable(monkeypatch)
    envs = [
        _env(
            env_id="ENV-TAC",
            symbol="TAC/USDT",
            entry_type="above",
            entry=19000,
            sl=18240,
            targets=[19095, 19190, 19285, 19380],
            msg_id=23886,
        ),
        _env(
            env_id="ENV-CLO",
            symbol="CLO/USDT",
            entry_type="above",
            entry=16860,
            sl=16185,
            targets=[16945, 17030, 17110, 17195],
            msg_id=23887,
        ),
        _env(
            env_id="ENV-BEAT",
            symbol="BEAT/USDT",
            entry_type="at",
            entry=1.6810,
            sl=1.6130,
            targets=[1.6895, 1.6980, 1.7060, 1.7145],
            msg_id=23888,
        ),
        _env(
            env_id="ENV-4",
            symbol="4/USDT",
            entry_type="at",
            entry=9440,
            sl=9060,
            targets=[9485, 9535, 9580, 9630],
            msg_id=23889,
        ),
    ]
    for e in envs:
        _write(tmp_artifacts / "telegram_message_envelope.jsonl", e)

    spots = {"TAC/USDT": 0.018455, "CLO/USDT": 0.15995, "BEAT/USDT": 1.7451, "4/USDT": 0.008983}

    async def price(symbol: str) -> float:
        return spots[symbol]

    result = await backfill_run(symbols=list(spots), price_provider=price)
    d = result.to_dict()
    assert d["envelopes_scanned"] == 4, d
    assert d["rejected_size"] == 0, d  # no terminal scale-review
    assert d["rejected_entry_mode"] == 0, d
    assert (d["filled"] + d["newly_pending"] + d["re_pending"]) == 4, d
    assert len(d["errors"]) == 0, d
    # All four route via the fastlane (premium source not on the classic
    # allowlist) → allowlist bypass fired 4×. The entry_mode bypass only fires
    # at fill time (Gate 5b); these four are pending, so it is correctly 0.
    assert d["fastlane_bypassed_allowlist"] == 4, d
    assert d["fastlane_bypassed_entry_mode"] == 0, d
