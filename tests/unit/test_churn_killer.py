"""Sprint E (Goal 2026-06-01 §5): churn-killer behaviour contract.

Root-cause (real data): the loop re-enters the same loser minute-by-minute.
MATIC 4.25 re-entries/day, LINK 3.0, ETH 2.71. The existing post_stop_cooldown
only covers stop-outs of a single symbol. The churn-killer generalises this:

1. per-symbol cooldown after ANY risk-reducing close (stop AND take AND reversal),
2. loss-streak backoff (N consecutive losing closes -> longer window),
3. global rate limits (max trades/symbol/hour, max notional turnover/hour),
4. HARD INVARIANT: only risk-INCREASING entries are evaluated. Exits / stop-loss /
   take-profit / reductions are never passed to this gate and can never be blocked.

These tests pin BEHAVIOUR (what gets blocked / allowed), not implementation. The
authoritative source is the existing paper-execution audit JSONL — no new
persistence. Fail-OPEN on any read problem (a missing guardrail is strictly less
bad than deadlocking the loop).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.risk.churn_killer import (
    ChurnKillerConfig,
    ChurnVerdict,
    evaluate_churn_gate,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _write(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _close(symbol: str, ts: datetime, *, reason: str = "stop", pnl: float = -5.0) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts.isoformat(),
        "symbol": symbol,
        "reason": reason,
        "trade_pnl_usd": pnl,
    }


def _entry_fill(symbol: str, ts: datetime, *, price: float = 100.0, qty: float = 1.0) -> dict:
    """A risk-increasing entry fill (long buy)."""
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts.isoformat(),
        "symbol": symbol,
        "side": "buy",
        "position_side": "long",
        "fill_price": price,
        "filled_quantity": qty,
        "quantity": qty,
    }


def _exit_fill(symbol: str, ts: datetime, *, price: float = 100.0, qty: float = 1.0) -> dict:
    """A risk-reducing exit fill (long sell) — must NOT count as turnover/entry."""
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts.isoformat(),
        "symbol": symbol,
        "side": "sell",
        "position_side": "long",
        "fill_price": price,
        "filled_quantity": qty,
        "quantity": qty,
    }


def _cfg(**kw) -> ChurnKillerConfig:
    base = {
        "cooldown_minutes": 180,
        "loss_streak_threshold": 3,
        "loss_streak_multiplier": 2.0,
        "max_trades_per_symbol_per_hour": 0,
        "max_notional_turnover_per_hour": 0.0,
    }
    base.update(kw)
    return ChurnKillerConfig(**base)


# --------------------------------------------------------------------------
# §1 per-symbol cooldown after ANY risk-reducing close (not only stop)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["stop", "sl", "stop_loss", "take", "tp_hit", "reversal"])
def test_any_risk_reducing_close_starts_cooldown(tmp_path, reason):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(audit, [_close("ETH/USDT", now - timedelta(minutes=30), reason=reason, pnl=1.0)])
    v = evaluate_churn_gate("ETH/USDT", config=_cfg(), audit_path=audit, now=now)
    assert v.blocked
    assert v.reason == "post_stop_cooldown"


def test_take_profit_close_now_starts_cooldown_unlike_post_stop_base(tmp_path):
    """Regression vs the post_stop base: a `take` close MUST now cool down."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(audit, [_close("ETH/USDT", now - timedelta(minutes=10), reason="take", pnl=8.0)])
    assert evaluate_churn_gate("ETH/USDT", config=_cfg(), audit_path=audit, now=now).blocked


def test_old_close_not_in_cooldown(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(audit, [_close("ETH/USDT", now - timedelta(minutes=200), reason="take", pnl=1.0)])
    assert not evaluate_churn_gate("ETH/USDT", config=_cfg(), audit_path=audit, now=now).blocked


def test_close_at_window_edge_elapsed(tmp_path):
    """Strict `<`: a close exactly cooldown_minutes ago has elapsed."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(audit, [_close("ETH/USDT", now - timedelta(minutes=180), reason="stop", pnl=-1.0)])
    assert not evaluate_churn_gate("ETH/USDT", config=_cfg(), audit_path=audit, now=now).blocked


def test_other_symbol_not_affected_by_cooldown(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(audit, [_close("ETH/USDT", now - timedelta(minutes=5), reason="stop", pnl=-1.0)])
    assert not evaluate_churn_gate("BTC/USDT", config=_cfg(), audit_path=audit, now=now).blocked


# --------------------------------------------------------------------------
# §2 loss-streak backoff: N consecutive losing closes -> longer window
# --------------------------------------------------------------------------


def test_single_loss_uses_base_window(tmp_path):
    """1 loss < threshold: base window only. 100 min ago is OUTSIDE the 90-min
    base window, so NOT blocked."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(audit, [_close("MATIC/USDT", now - timedelta(minutes=100), reason="stop", pnl=-5.0)])
    v = evaluate_churn_gate(
        "MATIC/USDT", config=_cfg(cooldown_minutes=90), audit_path=audit, now=now
    )
    assert not v.blocked


def test_loss_streak_extends_window_blocks_what_base_would_allow(tmp_path):
    """3 consecutive losses with multiplier 2.0 -> 90*2=180-min window. The same
    last-close 100 min ago that a single loss would NOT block is now blocked."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _close("MATIC/USDT", now - timedelta(minutes=300), reason="stop", pnl=-5.0),
            _close("MATIC/USDT", now - timedelta(minutes=200), reason="stop", pnl=-5.0),
            _close("MATIC/USDT", now - timedelta(minutes=100), reason="stop", pnl=-5.0),
        ],
    )
    v = evaluate_churn_gate(
        "MATIC/USDT", config=_cfg(cooldown_minutes=90), audit_path=audit, now=now
    )
    assert v.blocked
    assert v.reason == "post_stop_cooldown"
    assert "loss_streak" in v.detail


def test_winning_close_breaks_loss_streak(tmp_path):
    """A winning close between losses resets the consecutive streak -> base window."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _close("MATIC/USDT", now - timedelta(minutes=300), reason="stop", pnl=-5.0),
            _close("MATIC/USDT", now - timedelta(minutes=250), reason="take", pnl=+9.0),
            _close("MATIC/USDT", now - timedelta(minutes=100), reason="stop", pnl=-5.0),
        ],
    )
    # Only 1 trailing loss -> base 90-min window -> 100 min ago is elapsed.
    v = evaluate_churn_gate(
        "MATIC/USDT", config=_cfg(cooldown_minutes=90), audit_path=audit, now=now
    )
    assert not v.blocked


def test_loss_streak_multiplier_one_is_inert(tmp_path):
    """multiplier 1.0: streak never extends the window (operator opt-out)."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _close("MATIC/USDT", now - timedelta(minutes=200), reason="stop", pnl=-5.0),
            _close("MATIC/USDT", now - timedelta(minutes=150), reason="stop", pnl=-5.0),
            _close("MATIC/USDT", now - timedelta(minutes=100), reason="stop", pnl=-5.0),
        ],
    )
    v = evaluate_churn_gate(
        "MATIC/USDT",
        config=_cfg(cooldown_minutes=90, loss_streak_multiplier=1.0),
        audit_path=audit,
        now=now,
    )
    assert not v.blocked


# --------------------------------------------------------------------------
# §3 global rate limits
# --------------------------------------------------------------------------


def test_max_trades_per_symbol_per_hour_blocks_n_plus_1(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _entry_fill("LINK/USDT", now - timedelta(minutes=50)),
            _entry_fill("LINK/USDT", now - timedelta(minutes=30)),
            _entry_fill("LINK/USDT", now - timedelta(minutes=10)),
        ],
    )
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_trades_per_symbol_per_hour=3),
        audit_path=audit,
        now=now,
    )
    assert v.blocked
    assert v.reason == "churn_limit"
    assert "trades_per_hour" in v.detail


def test_max_trades_per_symbol_per_hour_allows_n(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _entry_fill("LINK/USDT", now - timedelta(minutes=50)),
            _entry_fill("LINK/USDT", now - timedelta(minutes=10)),
        ],
    )
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_trades_per_symbol_per_hour=3),
        audit_path=audit,
        now=now,
    )
    assert not v.blocked


def test_trades_older_than_one_hour_do_not_count(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _entry_fill("LINK/USDT", now - timedelta(minutes=90)),
            _entry_fill("LINK/USDT", now - timedelta(minutes=80)),
            _entry_fill("LINK/USDT", now - timedelta(minutes=10)),
        ],
    )
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_trades_per_symbol_per_hour=2),
        audit_path=audit,
        now=now,
    )
    assert not v.blocked


def test_trade_count_is_per_symbol_not_global(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _entry_fill("BTC/USDT", now - timedelta(minutes=50)),
            _entry_fill("ETH/USDT", now - timedelta(minutes=40)),
            _entry_fill("LINK/USDT", now - timedelta(minutes=10)),
        ],
    )
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_trades_per_symbol_per_hour=1),
        audit_path=audit,
        now=now,
    )
    # LINK has exactly 1 entry in the last hour -> n+1 would be blocked, n allowed.
    # Here we attempt the 2nd LINK entry -> 1 existing >= limit 1 -> blocked.
    assert v.blocked


def test_max_notional_turnover_per_hour_blocks(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _entry_fill("ETH/USDT", now - timedelta(minutes=40), price=1000.0, qty=1.0),
            _entry_fill("BTC/USDT", now - timedelta(minutes=20), price=1000.0, qty=1.0),
        ],
    )
    # Global turnover = 2000 USD across all symbols in the last hour.
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_notional_turnover_per_hour=1500.0),
        audit_path=audit,
        now=now,
    )
    assert v.blocked
    assert v.reason == "churn_limit"
    assert "notional_turnover" in v.detail


def test_notional_turnover_under_limit_allows(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [_entry_fill("ETH/USDT", now - timedelta(minutes=40), price=500.0, qty=1.0)],
    )
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_notional_turnover_per_hour=1500.0),
        audit_path=audit,
        now=now,
    )
    assert not v.blocked


# --------------------------------------------------------------------------
# §4 HARD INVARIANT — exits / risk-reductions are never counted as turnover
# (they reduce risk; counting them would penalise de-risking).
# --------------------------------------------------------------------------


def test_exit_fills_do_not_count_toward_turnover(tmp_path):
    """A long *sell* (exit) must not inflate entry turnover and trigger a block."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _exit_fill("ETH/USDT", now - timedelta(minutes=30), price=10000.0, qty=1.0),
            _exit_fill("BTC/USDT", now - timedelta(minutes=20), price=10000.0, qty=1.0),
        ],
    )
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_notional_turnover_per_hour=1500.0),
        audit_path=audit,
        now=now,
    )
    assert not v.blocked


def test_exit_fills_do_not_count_toward_trade_count(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _exit_fill("LINK/USDT", now - timedelta(minutes=50)),
            _exit_fill("LINK/USDT", now - timedelta(minutes=30)),
            _exit_fill("LINK/USDT", now - timedelta(minutes=10)),
        ],
    )
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_trades_per_symbol_per_hour=1),
        audit_path=audit,
        now=now,
    )
    assert not v.blocked


def test_short_entry_sell_counts_short_exit_buy_does_not(tmp_path):
    """Direction-aware: short entry = sell (counts), short exit = buy (ignored)."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    short_entry = {
        "event_type": "order_filled",
        "timestamp_utc": (now - timedelta(minutes=10)).isoformat(),
        "symbol": "LINK/USDT",
        "side": "sell",
        "position_side": "short",
        "fill_price": 100.0,
        "filled_quantity": 1.0,
    }
    short_exit = {
        "event_type": "order_filled",
        "timestamp_utc": (now - timedelta(minutes=20)).isoformat(),
        "symbol": "LINK/USDT",
        "side": "buy",
        "position_side": "short",
        "fill_price": 100.0,
        "filled_quantity": 5.0,
    }
    _write(audit, [short_entry, short_exit])
    v = evaluate_churn_gate(
        "LINK/USDT",
        config=_cfg(max_trades_per_symbol_per_hour=1),
        audit_path=audit,
        now=now,
    )
    # exactly 1 short entry counts -> at limit -> next entry blocked
    assert v.blocked
    assert v.reason == "churn_limit"


# --------------------------------------------------------------------------
# disabled / zero config
# --------------------------------------------------------------------------


def test_all_zero_config_disables_everything(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _close("ETH/USDT", now - timedelta(minutes=1), reason="stop", pnl=-5.0),
            _entry_fill("ETH/USDT", now - timedelta(minutes=2)),
            _entry_fill("ETH/USDT", now - timedelta(minutes=3)),
        ],
    )
    cfg = ChurnKillerConfig(
        cooldown_minutes=0,
        loss_streak_threshold=0,
        loss_streak_multiplier=1.0,
        max_trades_per_symbol_per_hour=0,
        max_notional_turnover_per_hour=0.0,
    )
    assert not evaluate_churn_gate("ETH/USDT", config=cfg, audit_path=audit, now=now).blocked


def test_zero_cooldown_keeps_rate_limits_active(tmp_path):
    """Disabling cooldown must not disable the rate limits (independent gates)."""
    now = _now()
    audit = tmp_path / "a.jsonl"
    _write(
        audit,
        [
            _entry_fill("ETH/USDT", now - timedelta(minutes=10)),
            _entry_fill("ETH/USDT", now - timedelta(minutes=5)),
        ],
    )
    v = evaluate_churn_gate(
        "ETH/USDT",
        config=_cfg(cooldown_minutes=0, max_trades_per_symbol_per_hour=2),
        audit_path=audit,
        now=now,
    )
    assert v.blocked
    assert v.reason == "churn_limit"


# --------------------------------------------------------------------------
# fail-open on read error
# --------------------------------------------------------------------------


def test_missing_file_fails_open(tmp_path):
    audit = tmp_path / "nope.jsonl"
    v = evaluate_churn_gate(
        "ETH/USDT",
        config=_cfg(max_trades_per_symbol_per_hour=1, max_notional_turnover_per_hour=1.0),
        audit_path=audit,
        now=_now(),
    )
    assert not v.blocked


def test_empty_file_fails_open(tmp_path):
    audit = tmp_path / "a.jsonl"
    audit.write_text("", encoding="utf-8")
    assert not evaluate_churn_gate(
        "ETH/USDT", config=_cfg(max_trades_per_symbol_per_hour=1), audit_path=audit, now=_now()
    ).blocked


def test_malformed_lines_skipped(tmp_path):
    now = _now()
    audit = tmp_path / "a.jsonl"
    good = json.dumps(_close("ETH/USDT", now - timedelta(minutes=10), reason="stop", pnl=-1.0))
    audit.write_text("not json\n" + good + "\n{partial\n", encoding="utf-8")
    assert evaluate_churn_gate("ETH/USDT", config=_cfg(), audit_path=audit, now=now).blocked


def test_verdict_is_truthy_via_blocked_flag(tmp_path):
    """ChurnVerdict contract: .blocked is the authoritative boolean."""
    v = ChurnVerdict(blocked=False, reason=None, detail="")
    assert not v.blocked
    v2 = ChurnVerdict(blocked=True, reason="churn_limit", detail="trades_per_hour=4>3")
    assert v2.blocked and v2.reason == "churn_limit"
