"""Tests für NormalizedTradeSignal + 16-State-Lifecycle + Validator.

Spec: docs/architecture/signal_to_execution_gap_analysis_20260510.md
Operator-Auftrag (2026-05-10) Aufgabenpakete 3 + 5.

Testkategorien:
    A) Status-Enum + Transition-Matrix Sanity
    B) transition_to() — erlaubte vs verbotene Übergänge
    C) Convenience-Properties (primary_entry, has_range_entry, is_terminal)
    D) Validator — Pflicht-Felder
    E) Validator — Plausibility (LONG/SHORT geometry)
    F) Validator — Sizing-Pflicht
    G) make_correlation_id / is_valid_correlation_id
    H) new_signal Constructor
"""

from __future__ import annotations

import pytest

from app.execution.normalized_signal import (
    LIFECYCLE_TRANSITIONS,
    TERMINAL_STATES,
    IllegalLifecycleTransition,
    NormalizedTradeSignal,
    SignalStatus,
    is_valid_correlation_id,
    make_correlation_id,
    new_signal,
    validate,
)

# ── Helper ────────────────────────────────────────────────────────────────────


def _long_signal(**overrides) -> NormalizedTradeSignal:
    """Default valid LONG signal mit allen Pflicht-Feldern."""
    base = {
        "correlation_id": "SIG-TGCH-20260510120000-BTCUSDT",
        "source": "telegram_premium_channel",
        "symbol": "BTCUSDT",
        "display_symbol": "BTC/USDT",
        "side": "buy",
        "direction": "long",
        "entry_type": "range",
        "entry_value": None,
        "entry_min": 65000.0,
        "entry_max": 65500.0,
        "stop_loss": 64200.0,
        "targets": (66000.0, 67000.0, 68500.0),
        "leverage": 10,
        "margin_mode": "isolated",
        "margin_size_usd": None,
        "risk_allocation_pct": 0.05,
        "raw_text": "BTCUSDT LONG\nEntry 65000-65500\nSL 64200\nTargets 66000/67000/68500",
    }
    base.update(overrides)
    return new_signal(**base)  # type: ignore[arg-type]


def _short_signal(**overrides) -> NormalizedTradeSignal:
    base = {
        "correlation_id": "SIG-TGCH-20260510120000-ETHUSDT",
        "source": "telegram_premium_channel",
        "symbol": "ETHUSDT",
        "display_symbol": "ETH/USDT",
        "side": "sell",
        "direction": "short",
        "entry_type": "limit",
        "entry_value": 3500.0,
        "entry_min": None,
        "entry_max": None,
        "stop_loss": 3600.0,
        "targets": (3400.0, 3300.0, 3200.0),
        "leverage": 5,
        "margin_mode": "cross",
        "margin_size_usd": None,
        "risk_allocation_pct": 0.03,
    }
    base.update(overrides)
    return new_signal(**base)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# A) Status-Enum + Transition-Matrix Sanity
# ─────────────────────────────────────────────────────────────────────────────


def test_status_enum_has_all_16_states() -> None:
    """Operator-Auftrag listet 16 explizite States, UPPERCASE values."""
    expected = {
        "RECEIVED",
        "PARSED",
        "VALIDATED",
        "REJECTED_INVALID_SIGNAL",
        "WAITING_FOR_ENTRY",
        "ENTRY_TRIGGERED",
        "ORDER_BUILDING",
        "ORDER_SUBMITTED",
        "ORDER_ACCEPTED",
        "POSITION_OPEN",
        "PARTIAL_TP_HIT",
        "TP_HIT",
        "SL_HIT",
        "EXPIRED",
        "CANCELLED",
        "FAILED",
    }
    actual = {s.value for s in SignalStatus}
    assert actual == expected
    assert len(SignalStatus) == 16


def test_terminal_states_are_complete() -> None:
    expected_terminals = {
        SignalStatus.REJECTED_INVALID_SIGNAL,
        SignalStatus.TP_HIT,
        SignalStatus.SL_HIT,
        SignalStatus.EXPIRED,
        SignalStatus.CANCELLED,
        SignalStatus.FAILED,
    }
    assert TERMINAL_STATES == expected_terminals


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for terminal in TERMINAL_STATES:
        assert LIFECYCLE_TRANSITIONS.get(terminal, frozenset()) == frozenset()


def test_transition_matrix_covers_all_states() -> None:
    """Jeder State (auch Terminal) muss in der Matrix als Key existieren."""
    for status in SignalStatus:
        assert status in LIFECYCLE_TRANSITIONS, f"missing transition entry for {status.value}"


def test_transition_matrix_targets_are_valid_states() -> None:
    """Kein Transition-Target darf ein nicht-existierender State sein."""
    all_states = set(SignalStatus)
    for from_state, allowed in LIFECYCLE_TRANSITIONS.items():
        for to_state in allowed:
            assert to_state in all_states, f"unknown target state {to_state} from {from_state}"


# ─────────────────────────────────────────────────────────────────────────────
# B) transition_to() — erlaubte vs verbotene Übergänge
# ─────────────────────────────────────────────────────────────────────────────


def test_legal_transition_increments_history() -> None:
    s = _long_signal()
    assert s.status == SignalStatus.PARSED
    assert s.status_history == ()

    s2 = s.transition_to(
        SignalStatus.VALIDATED, actor="SignalValidator", reason="all_fields_present"
    )
    assert s2.status == SignalStatus.VALIDATED
    assert len(s2.status_history) == 1
    transition = s2.status_history[0]
    assert transition.from_status == SignalStatus.PARSED
    assert transition.to_status == SignalStatus.VALIDATED
    assert transition.actor == "SignalValidator"
    assert transition.reason == "all_fields_present"
    assert transition.timestamp_utc  # non-empty


def test_legal_full_long_lifecycle() -> None:
    """Vollständiger erfolgreicher Long-Lifecycle bis TP_HIT."""
    s = _long_signal()
    chain = [
        (SignalStatus.VALIDATED, "SignalValidator", "ok"),
        (SignalStatus.WAITING_FOR_ENTRY, "EntryWatcher", "registered"),
        (SignalStatus.ENTRY_TRIGGERED, "EntryWatcher", "price_in_range"),
        (SignalStatus.ORDER_BUILDING, "PaperEngine", "building"),
        (SignalStatus.ORDER_SUBMITTED, "PaperEngine", "submitted"),
        (SignalStatus.ORDER_ACCEPTED, "PaperEngine", "accepted"),
        (SignalStatus.POSITION_OPEN, "PaperEngine", "filled"),
        (SignalStatus.PARTIAL_TP_HIT, "PaperEngine", "tp1_hit"),
        (SignalStatus.PARTIAL_TP_HIT, "PaperEngine", "tp2_hit"),
        (SignalStatus.TP_HIT, "PaperEngine", "tp3_hit_close_position"),
    ]
    for to_state, actor, reason in chain:
        s = s.transition_to(to_state, actor=actor, reason=reason)
    assert s.status == SignalStatus.TP_HIT
    assert s.is_terminal
    assert len(s.status_history) == len(chain)
    assert s.audit_reason == "tp3_hit_close_position"


def test_illegal_transition_raises() -> None:
    s = _long_signal()  # status = PARSED
    # PARSED → POSITION_OPEN ist ein verbotener Sprung (skip 6 states)
    with pytest.raises(IllegalLifecycleTransition) as exc_info:
        s.transition_to(SignalStatus.POSITION_OPEN, actor="Test", reason="forced")
    assert "transition not allowed" in str(exc_info.value)
    assert "PARSED → POSITION_OPEN" in str(exc_info.value)
    assert "correlation_id=" in str(exc_info.value)


def test_terminal_state_rejects_further_transitions() -> None:
    s = _long_signal()
    s = s.transition_to(SignalStatus.VALIDATED, actor="x", reason="x")
    s = s.transition_to(SignalStatus.CANCELLED, actor="Operator", reason="manual_cancel")
    assert s.is_terminal
    with pytest.raises(IllegalLifecycleTransition):
        s.transition_to(SignalStatus.WAITING_FOR_ENTRY, actor="x", reason="x")


def test_immutability_of_signal_on_transition() -> None:
    s = _long_signal()
    s2 = s.transition_to(SignalStatus.VALIDATED, actor="x", reason="x")
    # Ursprünglicher Signal ist unverändert
    assert s.status == SignalStatus.PARSED
    assert s.status_history == ()
    # Neue Instanz hat die History
    assert s2 is not s
    assert len(s2.status_history) == 1


def test_audit_reason_set_on_terminal_only() -> None:
    s = _long_signal()
    s2 = s.transition_to(SignalStatus.VALIDATED, actor="x", reason="non_terminal_reason")
    assert s2.audit_reason is None  # VALIDATED is not terminal

    s3 = s2.transition_to(SignalStatus.CANCELLED, actor="Operator", reason="user_cancel")
    assert s3.audit_reason == "user_cancel"  # CANCELLED is terminal


def test_rejected_invalid_signal_path_short() -> None:
    """Invalid-Signal-Pfad: PARSED → REJECTED_INVALID_SIGNAL terminiert."""
    s = _long_signal()
    s2 = s.transition_to(
        SignalStatus.REJECTED_INVALID_SIGNAL,
        actor="SignalValidator",
        reason="stop_loss_missing",
    )
    assert s2.is_terminal
    assert s2.audit_reason == "stop_loss_missing"


def test_waiting_to_expired_transition() -> None:
    """TTL-Expiry-Pfad: WAITING_FOR_ENTRY → EXPIRED."""
    s = _long_signal()
    s = s.transition_to(SignalStatus.VALIDATED, actor="x", reason="x")
    s = s.transition_to(SignalStatus.WAITING_FOR_ENTRY, actor="x", reason="x")
    s = s.transition_to(SignalStatus.EXPIRED, actor="EntryWatcher", reason="ttl_24h_exceeded")
    assert s.status == SignalStatus.EXPIRED
    assert s.is_terminal


def test_order_submitted_to_rejected_for_exchange_reject() -> None:
    """Exchange-Reject-Pfad: ORDER_SUBMITTED → REJECTED_INVALID_SIGNAL."""
    s = _long_signal()
    chain = [
        SignalStatus.VALIDATED,
        SignalStatus.WAITING_FOR_ENTRY,
        SignalStatus.ENTRY_TRIGGERED,
        SignalStatus.ORDER_BUILDING,
        SignalStatus.ORDER_SUBMITTED,
    ]
    for st in chain:
        s = s.transition_to(st, actor="x", reason="x")
    s = s.transition_to(
        SignalStatus.REJECTED_INVALID_SIGNAL,
        actor="ExchangeAdapter",
        reason="insufficient_balance",
    )
    assert s.is_terminal


# ─────────────────────────────────────────────────────────────────────────────
# C) Convenience-Properties
# ─────────────────────────────────────────────────────────────────────────────


def test_primary_entry_for_range() -> None:
    s = _long_signal(entry_type="range", entry_min=100.0, entry_max=110.0, entry_value=None)
    assert s.primary_entry == 105.0
    assert s.has_range_entry


def test_primary_entry_for_limit() -> None:
    s = _long_signal(entry_type="limit", entry_value=100.0, entry_min=None, entry_max=None)
    assert s.primary_entry == 100.0
    assert not s.has_range_entry


def test_primary_entry_for_market_returns_none() -> None:
    s = _long_signal(entry_type="market", entry_value=None, entry_min=None, entry_max=None)
    assert s.primary_entry is None


def test_is_terminal_initially_false() -> None:
    assert not _long_signal().is_terminal


# ─────────────────────────────────────────────────────────────────────────────
# D) Validator — Pflicht-Felder
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_accepts_complete_long_signal() -> None:
    result = validate(_long_signal())
    assert result.is_valid
    assert result.rejected_reason is None


def test_validator_accepts_complete_short_signal() -> None:
    result = validate(_short_signal())
    assert result.is_valid


def test_validator_rejects_missing_symbol() -> None:
    s = _long_signal(symbol="")
    result = validate(s)
    assert not result.is_valid
    assert "symbol_missing" in result.rejected_reason


def test_validator_rejects_invalid_side() -> None:
    s = _long_signal()
    s = NormalizedTradeSignal(**{**s.__dict__, "side": "BUY"})  # type: ignore[arg-type]
    result = validate(s)
    assert not result.is_valid
    assert "side_invalid" in result.rejected_reason


def test_validator_rejects_direction_side_mismatch() -> None:
    s = _long_signal()
    s = NormalizedTradeSignal(**{**s.__dict__, "side": "sell"})  # type: ignore[arg-type]
    result = validate(s)
    assert not result.is_valid
    assert "direction_side_mismatch" in result.rejected_reason


def test_validator_rejects_missing_stop_loss() -> None:
    s = _long_signal()
    s = NormalizedTradeSignal(**{**s.__dict__, "stop_loss": 0.0})
    result = validate(s)
    assert not result.is_valid
    assert "stop_loss_missing_or_invalid" in result.rejected_reason


def test_validator_rejects_no_targets() -> None:
    s = _long_signal(targets=())
    result = validate(s)
    assert not result.is_valid
    assert "targets_missing" in result.rejected_reason


def test_validator_rejects_invalid_target() -> None:
    s = _long_signal(targets=(66000.0, -1.0, 68000.0))
    result = validate(s)
    assert not result.is_valid
    assert "target_1_invalid" in result.rejected_reason


def test_validator_rejects_range_entry_without_min_max() -> None:
    s = _long_signal(entry_type="range", entry_min=None, entry_max=None, entry_value=None)
    result = validate(s)
    assert not result.is_valid
    assert "range_entry_requires_min_and_max" in result.rejected_reason


def test_validator_rejects_limit_entry_without_value() -> None:
    s = _long_signal(entry_type="limit", entry_value=None, entry_min=None, entry_max=None)
    result = validate(s)
    assert not result.is_valid
    assert "limit_entry_requires_value" in result.rejected_reason


def test_validator_warns_on_market_entry_with_explicit_price() -> None:
    s = _long_signal(entry_type="market", entry_value=65000.0, entry_min=None, entry_max=None)
    result = validate(s)
    assert result.is_valid  # warnings sind keine rejections
    assert "market_entry_with_explicit_price" in result.warnings


# ─────────────────────────────────────────────────────────────────────────────
# E) Validator — Plausibility (LONG/SHORT Geometrie)
# ─────────────────────────────────────────────────────────────────────────────


def test_long_sl_above_entry_rejected() -> None:
    """LONG: SL muss UNTER dem Entry liegen."""
    s = _long_signal(stop_loss=66000.0)  # Entry-Mid = 65250, SL höher = falsch
    result = validate(s)
    assert not result.is_valid
    assert "long_sl_above_entry" in result.rejected_reason


def test_long_target_below_entry_rejected() -> None:
    """LONG: Targets müssen ÜBER dem Entry liegen."""
    s = _long_signal(targets=(60000.0, 67000.0))
    result = validate(s)
    assert not result.is_valid
    assert "long_target_below_entry" in result.rejected_reason


def test_short_sl_below_entry_rejected() -> None:
    """SHORT: SL muss ÜBER dem Entry liegen."""
    s = _short_signal(stop_loss=3400.0)  # Entry=3500, SL niedriger = falsch
    result = validate(s)
    assert not result.is_valid
    assert "short_sl_below_entry" in result.rejected_reason


def test_short_target_above_entry_rejected() -> None:
    """SHORT: Targets müssen UNTER dem Entry liegen."""
    s = _short_signal(targets=(3400.0, 3700.0))
    result = validate(s)
    assert not result.is_valid
    assert "short_target_above_entry" in result.rejected_reason


def test_long_sl_at_entry_boundary_rejected() -> None:
    """LONG: SL == Entry ist auch unzulässig (Tie-break: rejected)."""
    s = _long_signal(stop_loss=65250.0)  # Entry-Mid = 65250 exactly
    result = validate(s)
    assert not result.is_valid


# ─────────────────────────────────────────────────────────────────────────────
# F) Validator — Sizing-Pflicht (Aufgabenpaket 5)
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_rejects_no_sizing_at_all() -> None:
    s = _long_signal(leverage=None, margin_size_usd=None, risk_allocation_pct=None)
    result = validate(s)
    assert not result.is_valid
    assert "sizing_missing" in result.rejected_reason


def test_validator_accepts_only_leverage() -> None:
    s = _long_signal(leverage=5, margin_size_usd=None, risk_allocation_pct=None)
    result = validate(s)
    assert result.is_valid


def test_validator_accepts_only_margin_size_usd() -> None:
    s = _long_signal(leverage=None, margin_size_usd=200.0, risk_allocation_pct=None)
    result = validate(s)
    assert result.is_valid


def test_validator_accepts_only_risk_allocation_pct() -> None:
    s = _long_signal(leverage=None, margin_size_usd=None, risk_allocation_pct=0.05)
    result = validate(s)
    assert result.is_valid


def test_validator_warns_on_unusual_leverage() -> None:
    s = _long_signal(leverage=200)  # > 125x
    result = validate(s)
    assert result.is_valid
    assert any("leverage_unusual" in w for w in result.warnings)


def test_validator_warns_on_unusual_risk_allocation() -> None:
    s = _long_signal(risk_allocation_pct=2.0)  # 200% des Equity
    result = validate(s)
    assert result.is_valid
    assert any("risk_allocation_unusual" in w for w in result.warnings)


# ─────────────────────────────────────────────────────────────────────────────
# G) correlation_id helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_make_correlation_id_format() -> None:
    cid = make_correlation_id(source_tag="TGCH", symbol="BTC/USDT")
    assert cid.startswith("SIG-TGCH-")
    assert cid.endswith("-BTCUSDT")
    assert is_valid_correlation_id(cid)


def test_make_correlation_id_strips_special_chars() -> None:
    cid = make_correlation_id(source_tag="tg-ch!", symbol="btc/usdt")
    assert "TGCH" in cid
    assert "BTCUSDT" in cid


def test_is_valid_correlation_id_rejects_malformed() -> None:
    assert not is_valid_correlation_id("invalid")
    assert not is_valid_correlation_id("SIG-TGCH-no-timestamp")
    assert not is_valid_correlation_id("")
    assert not is_valid_correlation_id("SIG-TGCH-123-BTCUSDT")  # too short ts


def test_is_valid_correlation_id_accepts_well_formed() -> None:
    assert is_valid_correlation_id("SIG-TGCH-20260510120000-BTCUSDT")
    assert is_valid_correlation_id("SIG-DASH-20260510120000-ETHUSDT")
    assert is_valid_correlation_id("SIG-TV-20260510120000-SOLUSDT")


# ─────────────────────────────────────────────────────────────────────────────
# H) new_signal Constructor
# ─────────────────────────────────────────────────────────────────────────────


def test_new_signal_sets_parsed_at_utc() -> None:
    s = _long_signal()
    assert s.parsed_at_utc  # non-empty ISO timestamp
    assert "T" in s.parsed_at_utc  # ISO-format hat T zwischen Datum + Zeit


def test_new_signal_default_status_is_parsed() -> None:
    s = _long_signal()
    assert s.status == SignalStatus.PARSED


def test_new_signal_default_status_history_is_empty() -> None:
    s = _long_signal()
    assert s.status_history == ()


def test_new_signal_targets_become_immutable_tuple() -> None:
    s = new_signal(
        correlation_id="SIG-TGCH-20260510120000-BTCUSDT",
        source="x",
        symbol="BTCUSDT",
        side="buy",
        direction="long",
        entry_type="limit",
        entry_value=100.0,
        stop_loss=95.0,
        targets=[105.0, 110.0],  # list input
        leverage=1,
    )
    assert isinstance(s.targets, tuple)
    assert s.targets == (105.0, 110.0)


def test_new_signal_rejects_empty_correlation_id() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        new_signal(
            correlation_id="",
            source="x",
            symbol="BTCUSDT",
            side="buy",
            direction="long",
            entry_type="limit",
            entry_value=100.0,
            stop_loss=95.0,
            targets=(105.0,),
            leverage=1,
        )


def test_new_signal_rejects_empty_source() -> None:
    with pytest.raises(ValueError, match="source"):
        new_signal(
            correlation_id="SIG-X-20260510120000-X",
            source="",
            symbol="BTCUSDT",
            side="buy",
            direction="long",
            entry_type="limit",
            entry_value=100.0,
            stop_loss=95.0,
            targets=(105.0,),
            leverage=1,
        )


def test_new_signal_display_symbol_defaults_to_symbol() -> None:
    s = new_signal(
        correlation_id="SIG-X-20260510120000-X",
        source="x",
        symbol="BTCUSDT",
        side="buy",
        direction="long",
        entry_type="limit",
        entry_value=100.0,
        stop_loss=95.0,
        targets=(105.0,),
        leverage=1,
    )
    assert s.display_symbol == "BTCUSDT"


# ─────────────────────────────────────────────────────────────────────────────
# I) Integration: ParsedSignal-Like Roundtrip (Operator-Beispiel)
# ─────────────────────────────────────────────────────────────────────────────


def test_operator_example_btc_long_full_lifecycle() -> None:
    """Genau das Beispiel-Signal aus dem Operator-Auftrag.

    BTCUSDT LONG
    Entry: 65000 - 65500
    Leverage: 10x
    Margin: 5%
    Stop Loss: 64200
    Targets: 66000 / 67000 / 68500
    """
    s = new_signal(
        correlation_id="SIG-TGCH-20260510120000-BTCUSDT",
        source="telegram_premium_channel",
        symbol="BTCUSDT",
        display_symbol="BTC/USDT",
        side="buy",
        direction="long",
        entry_type="range",
        entry_min=65000.0,
        entry_max=65500.0,
        stop_loss=64200.0,
        targets=(66000.0, 67000.0, 68500.0),
        leverage=10,
        margin_mode="isolated",
        risk_allocation_pct=0.05,  # "Margin: 5%" → 5% Equity-Allocation
        raw_text=(
            "BTCUSDT LONG\nEntry: 65000 - 65500\nLeverage: 10x\n"
            "Margin: 5%\nStop Loss: 64200\nTargets: 66000 / 67000 / 68500"
        ),
    )

    # Validator akzeptiert
    result = validate(s)
    assert result.is_valid
    assert result.rejected_reason is None

    # Lifecycle bis ENTRY_TRIGGERED durchlaufen
    s = s.transition_to(SignalStatus.VALIDATED, actor="SignalValidator", reason="ok")
    s = s.transition_to(
        SignalStatus.WAITING_FOR_ENTRY, actor="EntryWatcher", reason="awaiting_entry_65000_65500"
    )
    s = s.transition_to(
        SignalStatus.ENTRY_TRIGGERED, actor="EntryWatcher", reason="price_65250_in_range"
    )

    # Entry-Trigger entry-mid = 65250
    assert s.primary_entry == 65250.0
    assert s.has_range_entry
    assert s.status == SignalStatus.ENTRY_TRIGGERED
    assert len(s.status_history) == 3
    assert s.status_history[0].actor == "SignalValidator"
    assert s.status_history[-1].reason == "price_65250_in_range"


def test_operator_example_eth_short_full_lifecycle() -> None:
    """SHORT-Pendant zum Operator-LONG-Beispiel — Sprint-B-Bug-#1 Akzeptanz.

        ETHUSDT SHORT
        Entry: 3500
        Leverage: 5x
        Margin: 3%
        Stop Loss: 3600
        Targets: 3400 / 3300 / 3200

    SHORT-Geometrie: SL > Entry > Targets (gespiegelt zu LONG).
    Bridge + paper_engine akzeptieren das jetzt nativ via
    ``side="sell"`` + ``position_side="short"`` (Codex-Commit b005f43,
    paper_engine V25 SHORT-Support, Reconcile 2026-05-10).
    """
    s = new_signal(
        correlation_id="SIG-TGCH-20260510143000-ETHUSDT",
        source="telegram_premium_channel",
        symbol="ETHUSDT",
        display_symbol="ETH/USDT",
        side="sell",
        direction="short",
        entry_type="limit",
        entry_value=3500.0,
        stop_loss=3600.0,
        targets=(3400.0, 3300.0, 3200.0),
        leverage=5,
        margin_mode="isolated",
        risk_allocation_pct=0.03,
        raw_text=(
            "ETHUSDT SHORT\nEntry: 3500\nLeverage: 5x\n"
            "Margin: 3%\nStop Loss: 3600\nTargets: 3400 / 3300 / 3200"
        ),
    )

    # Validator akzeptiert SHORT mit korrekter SL>Entry>Targets-Geometrie
    result = validate(s)
    assert result.is_valid
    assert result.rejected_reason is None

    # Lifecycle bis POSITION_OPEN durchlaufen
    s = s.transition_to(SignalStatus.VALIDATED, actor="SignalValidator", reason="ok")
    s = s.transition_to(
        SignalStatus.ENTRY_TRIGGERED, actor="EntryWatcher", reason="price_at_3500_limit"
    )
    s = s.transition_to(SignalStatus.ORDER_BUILDING, actor="PaperEngine", reason="building")
    s = s.transition_to(SignalStatus.ORDER_SUBMITTED, actor="PaperEngine", reason="sell_short")
    s = s.transition_to(SignalStatus.ORDER_ACCEPTED, actor="PaperEngine", reason="accepted")
    s = s.transition_to(SignalStatus.POSITION_OPEN, actor="PaperEngine", reason="filled")

    assert s.status == SignalStatus.POSITION_OPEN
    assert s.direction == "short"
    assert s.side == "sell"
    assert s.primary_entry == 3500.0
    # SHORT-Geometrie: SL über Entry, Targets unter Entry
    assert s.stop_loss > s.primary_entry
    assert all(t < s.primary_entry for t in s.targets)
    assert len(s.status_history) == 6


def test_validator_rejects_short_signal_with_long_geometry() -> None:
    """Pflicht: SHORT mit LONG-Geometrie (SL unter Entry) wird abgelehnt.

    Dies verhindert dass ein falsch-getaggtes Signal (SHORT direction, aber
    SL unter Entry) durchrutscht und im Paper-Engine eine illegal-orderierte
    SHORT-Position erzeugt.
    """
    s = _short_signal(
        stop_loss=3400.0,  # < Entry 3500 — falsch für SHORT
        targets=(3300.0,),
    )
    result = validate(s)
    assert not result.is_valid
    assert "short_sl_below_entry" in result.rejected_reason
