"""KYT (Know Your Transaction) unit tests.

Covers the decision matrix (allow/warn/hold/block/manual_review), unknown risk,
provider failure (conservative fallback), explicit screening flags (sanction/
mixer/bridge/etc.), behavioural detection (round-tripping = the MATIC signature),
historical lookback, audit log, SENTR/Neo alerting, and the non-breaking gate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.security.kyt.audit import emit_agent_alerts, pseudonymize_address, write_assessment
from app.security.kyt.engine import KytEngine
from app.security.kyt.gate import screen_order
from app.security.kyt.models import (
    KytCheckPhase,
    KytDecision,
    KytFlag,
    KytReasonCode,
    KytRiskLevel,
    TransactionContext,
)
from app.security.kyt.providers import LocalListProvider, NullProvider
from app.security.kyt.rules import KytRules, default_rules


def _ctx(symbol: str | None = "BTC/USDT", **kw) -> TransactionContext:
    return TransactionContext(
        tx_id=kw.pop("tx_id", "tx1"),
        phase=kw.pop("phase", KytCheckPhase.PRE_TRANSACTION),
        symbol=symbol,
        venue=kw.pop("venue", "bybit"),
        side=kw.pop("side", "buy"),
        quantity=kw.pop("quantity", 0.1),
        entry_price=kw.pop("entry_price", 70000.0),
        notional_usd=kw.pop("notional_usd", 7000.0),
        **kw,
    )


class _RaisingProvider:
    name = "boom"

    def screen(self, context):  # noqa: ANN001
        raise RuntimeError("provider down")


class _FlagProvider:
    """Returns a fixed flag — simulates external screening hits."""

    def __init__(self, code: KytReasonCode, level: KytRiskLevel) -> None:
        self._code = code
        self._level = level
        self.name = "fake_ext"

    def screen(self, context):  # noqa: ANN001
        return [KytFlag(self._code, self._level, "simulated", self.name)]


# --- decision matrix -------------------------------------------------------


def test_clean_order_allows() -> None:
    eng = KytEngine([LocalListProvider()])
    a = eng.assess(_ctx("BTC/USDT", venue="bybit"), history=[])
    assert a.decision == KytDecision.ALLOW
    assert a.risk_level in (KytRiskLevel.LOW, KytRiskLevel.UNKNOWN)
    assert not a.decision.blocks_execution


def test_privacy_coin_high_routes_to_manual_review() -> None:
    eng = KytEngine([LocalListProvider()])
    a = eng.assess(_ctx("XMR/USDT"), history=[])
    assert a.risk_level == KytRiskLevel.HIGH
    assert a.decision == KytDecision.MANUAL_REVIEW
    assert a.decision.blocks_execution
    assert KytReasonCode.PRIVACY_COIN in a.reason_codes


def test_blocklisted_symbol_critical_blocks() -> None:
    rules = KytRules(blocklisted_symbols={"SCAM": KytRiskLevel.CRITICAL})
    eng = KytEngine([LocalListProvider(rules)], rules=rules)
    a = eng.assess(_ctx("SCAM/USDT"), history=[])
    assert a.decision == KytDecision.BLOCK
    assert a.risk_level == KytRiskLevel.CRITICAL


def test_medium_venue_warns() -> None:
    eng = KytEngine([LocalListProvider()])
    # BitMEX carries MEDIUM data-quality risk by default.
    a = eng.assess(_ctx("BTC/USDT", venue="bitmex"), history=[])
    assert a.decision == KytDecision.WARN
    assert a.risk_level == KytRiskLevel.MEDIUM


def test_unknown_only_allows_but_flags() -> None:
    eng = KytEngine([NullProvider()], behavioral_enabled=False)
    a = eng.assess(_ctx("BTC/USDT", wallet_address="0xabc"), history=[])
    assert a.decision == KytDecision.ALLOW  # do not block on pure unknown
    assert a.risk_level == KytRiskLevel.UNKNOWN
    assert KytReasonCode.INSUFFICIENT_DATA in a.reason_codes


# --- provider failure / conservative fallback ------------------------------


def test_provider_failure_exchange_order_warns() -> None:
    eng = KytEngine([_RaisingProvider()], behavioral_enabled=False, fail_mode="conservative")
    a = eng.assess(_ctx("BTC/USDT"), history=[])
    assert a.decision == KytDecision.WARN
    assert KytReasonCode.PROVIDER_UNAVAILABLE in a.reason_codes


def test_provider_failure_with_address_holds() -> None:
    eng = KytEngine([_RaisingProvider()], behavioral_enabled=False, fail_mode="conservative")
    a = eng.assess(_ctx("BTC/USDT", wallet_address="0xdeadbeef"), history=[])
    assert a.decision == KytDecision.HOLD
    assert a.decision.blocks_execution


# --- explicit external screening flags -------------------------------------


def test_sanction_hit_blocks() -> None:
    eng = KytEngine([_FlagProvider(KytReasonCode.SANCTIONED_ENTITY, KytRiskLevel.CRITICAL)])
    a = eng.assess(_ctx("ETH/USDT"), history=[])
    assert a.decision == KytDecision.BLOCK


def test_mixer_high_manual_review() -> None:
    eng = KytEngine([_FlagProvider(KytReasonCode.MIXER_EXPOSURE, KytRiskLevel.HIGH)])
    a = eng.assess(_ctx("ETH/USDT"), history=[])
    assert a.decision == KytDecision.MANUAL_REVIEW
    assert KytReasonCode.MIXER_EXPOSURE in a.reason_codes


def test_bridge_medium_warns() -> None:
    eng = KytEngine([_FlagProvider(KytReasonCode.BRIDGE_ABUSE, KytRiskLevel.MEDIUM)])
    a = eng.assess(_ctx("ETH/USDT"), history=[])
    assert a.decision == KytDecision.WARN


# --- missing data ----------------------------------------------------------


def test_missing_symbol_low_completeness() -> None:
    eng = KytEngine([LocalListProvider()], behavioral_enabled=False)
    a = eng.assess(_ctx(symbol=None, venue=None), history=[])
    assert a.data_completeness < 0.5
    assert a.decision == KytDecision.ALLOW  # nothing assessable, no false block


# --- behavioural: round-tripping (the MATIC signature) ---------------------


def test_round_tripping_flagged_high() -> None:
    now = datetime.now(UTC)
    history = [
        {
            "timestamp_utc": (now - timedelta(minutes=m)).isoformat(),
            "symbol": "MATIC/USDT",
            "side": "sell",
            "quantity": 1000.0,
            "fill_price": 0.4,
        }
        for m in (5, 15, 25, 35)
    ]
    eng = KytEngine([LocalListProvider()])
    a = eng.assess(
        _ctx("MATIC/USDT", venue="bitmex", timestamp_utc=now.isoformat()), history=history
    )
    assert KytReasonCode.ROUND_TRIPPING in a.reason_codes
    assert a.risk_level == KytRiskLevel.HIGH
    assert a.decision == KytDecision.MANUAL_REVIEW


def test_empty_history_marks_unknown_behaviour() -> None:
    eng = KytEngine([LocalListProvider()])
    a = eng.assess(_ctx("BTC/USDT"), history=[])
    assert KytReasonCode.INSUFFICIENT_DATA in a.reason_codes


# --- historical lookback ---------------------------------------------------


def test_historical_lookback_rescans_batch() -> None:
    eng = KytEngine([LocalListProvider()], behavioral_enabled=False)
    txs = [_ctx("XMR/USDT", tx_id="a"), _ctx("BTC/USDT", tx_id="b")]
    results = eng.historical_lookback(txs)
    assert len(results) == 2
    assert results[0].decision == KytDecision.MANUAL_REVIEW
    assert results[1].decision == KytDecision.ALLOW


# --- audit + agent alerting ------------------------------------------------


def test_audit_write_persists_record(tmp_path: Path) -> None:
    eng = KytEngine([LocalListProvider()], behavioral_enabled=False)
    ctx = _ctx("XMR/USDT", wallet_address="0xsecret")
    a = eng.assess(ctx, history=[])
    audit = tmp_path / "kyt.jsonl"
    write_assessment(a, ctx, audit_path=audit)
    rows = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    # privacy: raw address never persisted, only a pseudonym
    assert rows[0]["context"]["wallet_pseudonym"].startswith("addr_")
    assert "0xsecret" not in audit.read_text()


def test_pseudonymize_is_stable_and_non_reversible() -> None:
    p1 = pseudonymize_address("0xABC")
    p2 = pseudonymize_address("0xabc")
    assert p1 == p2  # case-insensitive, stable
    assert "0xabc" not in (p1 or "")
    assert pseudonymize_address(None) is None


def test_sentr_alert_on_block_neo_on_high(tmp_path: Path) -> None:
    eng = KytEngine([_FlagProvider(KytReasonCode.SANCTIONED_ENTITY, KytRiskLevel.CRITICAL)])
    ctx = _ctx("ETH/USDT")
    a = eng.assess(ctx, history=[])
    alerted = emit_agent_alerts(a, ctx, agent_dir=tmp_path)
    assert "sentr" in alerted and "neo" in alerted
    sentr = (tmp_path / "sentr" / "findings.jsonl").read_text()
    assert "kyt_block" in sentr and "P0" in sentr


def test_allow_emits_no_alert(tmp_path: Path) -> None:
    eng = KytEngine([LocalListProvider()], behavioral_enabled=False)
    ctx = _ctx("BTC/USDT")
    a = eng.assess(ctx, history=[])
    assert emit_agent_alerts(a, ctx, agent_dir=tmp_path) == []
    assert not (tmp_path / "sentr").exists()


# --- non-breaking gate -----------------------------------------------------


def test_screen_order_returns_none_when_disabled() -> None:
    # Default settings: kyt.enabled is False → gate is a no-op.
    result = screen_order(
        tx_id="tx1",
        symbol="XMR/USDT",
        venue="bybit",
        side="buy",
        quantity=1.0,
        entry_price=150.0,
    )
    assert result is None


def test_default_rules_classify_known_assets() -> None:
    rules = default_rules()
    assert rules.symbol_classification("XMR/USDT")[0] == KytRiskLevel.HIGH
    assert rules.symbol_classification("MATIC/USDT")[0] == KytRiskLevel.MEDIUM
    assert rules.symbol_classification("BTC/USDT") is None
    assert rules.venue_classification("bitmex") == KytRiskLevel.MEDIUM
