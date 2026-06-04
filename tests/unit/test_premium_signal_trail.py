"""Premium-Signal Trail (2026-05-20 /goal).

Verifiziert die End-to-End-Status-Ableitung pro Pipeline-Stufe gegen 5
typische Pfade aus den echten 2026-05-11..18 Pi-Audit-Streams:

1. filled+closed   — BIRB 18.05. (4 TP-Tiers durchlaufen)
2. filled+open     — BAS 13.05. (filled, noch keine Close-Events)
3. bridge_rejected — OPG 11.05. (risk_gate_rejected)
4. pending_entry   — BILL 12.05. (entry never reached)
5. not_approved    — IRYS 12.05. 00:05 (Auto-Fill war noch nicht aktiv)
6. paper_rejected  — IRYS 12.05. 19:09 (long_sl_at_or_above_price)

Die Fixtures sind verkürzte Originals aus dem Pi, nur essentielle Felder.
"""

from __future__ import annotations

from app.observability.premium_signal_trail import (
    StageStatus,
    TrailEntry,
    build_orphan_completions,
    build_trail,
)

# ── Test-Fixtures (echte Pi-Audit-Daten, gekürzt) ─────────────────────────


def _origin_env(env_id: str, symbol: str, ts: str, **payload_extra) -> dict:
    source_uid = payload_extra.pop("source_uid", None)
    source_platform = payload_extra.pop("source_platform", None)
    payload = {
        "symbol": symbol.replace("/", ""),
        "display_symbol": symbol,
        "side": "buy",
        "direction": "long",
        "entry_type": "at",
        "entry_value": 1.0,
        "stop_loss": 0.95,
        "targets": [1.05, 1.10, 1.15, 1.20],
        "leverage": 10,
        "scale_factor": 1.0,
        "scale_resolved_at_emit": True,
        **payload_extra,
    }
    if source_uid is not None:
        payload["source_uid"] = source_uid
    if source_platform is not None:
        payload["source_platform"] = source_platform
    return {
        "timestamp_utc": ts,
        "event": "telegram_channel_envelope",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": "telegram_premium_channel",
        "envelope_id": env_id,
        "idempotency_key": f"idem-{env_id}",
        **({"source_uid": source_uid} if source_uid is not None else {}),
        **({"source_platform": source_platform} if source_platform is not None else {}),
        "payload": payload,
    }


def _approved_env(approved_env_id: str, origin_env_id: str, symbol: str, ts: str) -> dict:
    return {
        "timestamp_utc": ts,
        "event": "telegram_channel_approval",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": "telegram_premium_channel_approved",
        "envelope_id": approved_env_id,
        "idempotency_key": f"idem-app-{approved_env_id}",
        "origin_envelope_id": origin_env_id,
        "origin_source": "telegram_premium_channel",
        "approved_by": "auto-fill",
        "payload": {
            "display_symbol": symbol,
            "symbol": symbol.replace("/", ""),
            "side": "buy",
            "direction": "long",
        },
    }


def _bridge_filled(
    env_id: str,
    approved_env_id: str,
    symbol: str,
    ts: str,
    order_id: str = "ord_x",
) -> dict:
    return {
        "timestamp_utc": ts,
        "event": "operator_signal_bridge",
        "envelope_id": approved_env_id,
        "correlation_id": env_id,
        "stage": "filled",
        "source": "telegram_premium_channel_approved",
        "audit_reason": "paper_order_filled",
        "symbol": symbol,
        "order_id": order_id,
        "fill_price": 1.0,
        "quantity": 100.0,
        "lifecycle_state": "POSITION_OPEN",
    }


def _bridge_rejected_risk(env_id: str, approved_env_id: str, symbol: str, ts: str) -> dict:
    return {
        "timestamp_utc": ts,
        "event": "operator_signal_bridge",
        "envelope_id": approved_env_id,
        "correlation_id": env_id,
        "stage": "rejected_risk",
        "source": "telegram_premium_channel_approved",
        "audit_reason": "risk_gate_rejected",
        "symbol": symbol,
        "lifecycle_state": "REJECTED_INVALID_SIGNAL",
    }


def _bridge_pending(env_id: str, approved_env_id: str, symbol: str, ts: str) -> dict:
    return {
        "timestamp_utc": ts,
        "event": "operator_signal_bridge",
        "envelope_id": approved_env_id,
        "correlation_id": env_id,
        "stage": "pending",
        "source": "telegram_premium_channel_approved",
        "audit_reason": "entry_not_reached",
        "symbol": symbol,
    }


def _paper_order_filled(
    order_id: str, env_id: str, approved_env_id: str, symbol: str, ts: str
) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": ts,
        "order_id": order_id,
        "symbol": symbol,
        "side": "buy",
        "status": "filled",
        "correlation_id": env_id,
        "idempotency_key": f"opbridge:{approved_env_id}",
        "fill_price": 1.0,
        "quantity": 100.0,
    }


def _paper_position_closed(
    order_id: str, env_id: str, symbol: str, ts: str, *, reason: str, trade_pnl_usd: float
) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "order_id": order_id,
        "symbol": symbol,
        "correlation_id": env_id,
        "reason": reason,
        "trade_pnl_usd": trade_pnl_usd,
    }


def _paper_rejected_invalid_sl(order_id: str, env_id: str, symbol: str, ts: str) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_rejected_invalid_sl",
        "timestamp_utc": ts,
        "order_id": order_id,
        "symbol": symbol,
        "side": "buy",
        "correlation_id": env_id,
        "reason": "long_sl_at_or_above_price",
        "stop_loss": 0.0523,
        "current_price": 0.05153,
    }


# ── Tests ─────────────────────────────────────────────────────────────────


def _stage(trail: TrailEntry, name: str) -> StageStatus:
    matching = [s for s in trail.stages if s.name == name]
    assert matching, f"stage {name!r} missing"
    return matching[0]


def test_filled_and_closed_path():
    """BIRB-Pfad: parsed → envelope → approved → bridge filled → paper closed (tp_tier)."""
    env_id = "ENV-20260518191745-63eb541e"
    approved_id = "ENV-20260518191745-9e0a99ad"
    order_id = "ord_0801bc2893f0"
    sym = "BIRB/USDT"

    envelopes = [
        _origin_env(env_id, sym, "2026-05-18T19:17:45+00:00", scale_factor=100000.0),
        _approved_env(approved_id, env_id, sym, "2026-05-18T19:17:45.95+00:00"),
    ]
    bridge = [_bridge_filled(env_id, approved_id, sym, "2026-05-18T19:17:52+00:00", order_id)]
    paper = [
        _paper_order_filled(order_id, env_id, approved_id, sym, "2026-05-18T19:17:52+00:00"),
        _paper_position_closed(
            order_id,
            env_id,
            sym,
            "2026-05-18T21:38:26+00:00",
            reason="tp_tier",
            trade_pnl_usd=5.5,
        ),
        _paper_position_closed(
            order_id,
            env_id,
            sym,
            "2026-05-19T04:03:27+00:00",
            reason="tp_tier",
            trade_pnl_usd=5.2,
        ),
    ]

    trail = build_trail(envelope_records=envelopes, bridge_records=bridge, paper_records=paper)
    assert len(trail) == 1
    entry = trail[0]
    assert entry.symbol == "BIRB/USDT"
    # 2026-06-04: State-Machine zerlegt CLOSED → CLOSED_TP (tp_tier-Close mit PnL).
    assert entry.overall == "CLOSED_TP"
    assert entry.is_open is False
    assert entry.paper_position_state == "POSITION_CLOSED"
    assert entry.paper_close_reason == "tp_tier"
    assert entry.realized_pnl_usd == 10.7  # 5.5 + 5.2
    assert entry.paper_order_id == order_id
    assert entry.quantity == 100.0
    assert _stage(entry, "parsed").ok
    assert _stage(entry, "envelope").ok
    assert _stage(entry, "approved").ok
    assert _stage(entry, "bridge").ok
    assert _stage(entry, "paper").ok
    assert _stage(entry, "closed").ok
    assert entry.next_action_hint == "none"


def test_trail_exposes_source_identity():
    origin = _origin_env(
        "ENV-A",
        "NIGHT/USDT",
        "2026-05-30T13:43:52+00:00",
        source_uid="telegram:-1001275462917:23878",
        source_platform="telegram",
    )
    entries = build_trail(
        envelope_records=[origin],
        bridge_records=[],
        paper_records=[],
        limit=10,
    )
    assert entries[0].source_uid == "telegram:-1001275462917:23878"
    assert entries[0].source_platform == "telegram"
    assert entries[0].to_dict()["source_uid"] == "telegram:-1001275462917:23878"


def test_filled_and_open_path():
    """BAS-Pfad: filled, kein position_closed event — position is still open."""
    env_id = "ENV-bas-origin"
    approved_id = "ENV-bas-approved"
    order_id = "ord_bas"
    sym = "BAS/USDT"

    envelopes = [
        _origin_env(env_id, sym, "2026-05-13T22:23:21+00:00"),
        _approved_env(approved_id, env_id, sym, "2026-05-13T22:23:22+00:00"),
    ]
    bridge = [_bridge_filled(env_id, approved_id, sym, "2026-05-14T08:41:21+00:00", order_id)]
    paper = [_paper_order_filled(order_id, env_id, approved_id, sym, "2026-05-14T08:41:21+00:00")]

    [entry] = build_trail(envelope_records=envelopes, bridge_records=bridge, paper_records=paper)
    assert entry.overall == "OPEN"
    assert entry.is_open is True
    assert entry.paper_position_state == "POSITION_OPEN"
    assert entry.realized_pnl_usd is None
    assert entry.next_action_hint == "monitor"
    assert _stage(entry, "paper").ok
    assert not _stage(entry, "closed").ok  # nicht geschlossen


def test_bridge_rejected_risk_path():
    """OPG-Pfad: parsed → envelope → approved → bridge rejected (risk_gate)."""
    env_id = "ENV-opg-origin"
    approved_id = "ENV-opg-approved"
    sym = "OPG/USDT"

    envelopes = [
        _origin_env(env_id, sym, "2026-05-11T20:44:11+00:00"),
        _approved_env(approved_id, env_id, sym, "2026-05-11T20:44:12+00:00"),
    ]
    bridge = [_bridge_rejected_risk(env_id, approved_id, sym, "2026-05-11T20:45:00+00:00")]

    [entry] = build_trail(envelope_records=envelopes, bridge_records=bridge, paper_records=[])
    assert entry.overall == "BRIDGE_REJECTED"
    assert entry.is_open is False
    assert entry.paper_position_state is None
    assert _stage(entry, "bridge").ok is False
    assert _stage(entry, "bridge").reason == "risk_gate_rejected"
    assert entry.next_action_hint == "review_reason"


def test_entry_mode_disabled_path():
    """RC-2 (2026-06-04): stage=rejected_entry_mode → overall=ENTRY_DISABLED.

    Vorher fiel dieser Stage durch alle Klassifikations-Mengen → Bridge-Pill
    "Not picked up", overall=UNKNOWN. Der globale Kill-Switch muss als
    eigener, sichtbarer State erscheinen (kein Erfolg, kein 'Unklar')."""
    env_id = "ENV-apr-origin"
    approved_id = "ENV-apr-approved"
    sym = "APR/USDT"

    envelopes = [
        _origin_env(env_id, sym, "2026-06-04T01:40:00+00:00"),
        _approved_env(approved_id, env_id, sym, "2026-06-04T01:40:01+00:00"),
    ]
    bridge = [
        {
            "timestamp_utc": "2026-06-04T01:41:00+00:00",
            "event": "operator_signal_bridge",
            "envelope_id": approved_id,
            "correlation_id": env_id,
            "stage": "rejected_entry_mode",
            "source": "telegram_premium_channel_approved",
            "audit_reason": "entry_mode_disabled",
            "symbol": sym,
            "lifecycle_state": "REJECTED_INVALID_SIGNAL",
        }
    ]

    [entry] = build_trail(envelope_records=envelopes, bridge_records=bridge, paper_records=[])
    assert entry.overall == "ENTRY_DISABLED"
    assert entry.is_open is False
    assert entry.paper_position_state is None
    assert _stage(entry, "bridge").ok is False
    assert _stage(entry, "bridge").label == "Entry disabled"
    assert entry.next_action_hint == "entry_disabled_global"


def test_premium_paper_execution_disabled_path():
    """Verify premium_paper_execution_disabled maps to ENTRY_DISABLED."""
    env_id = "ENV-apr-origin2"
    approved_id = "ENV-apr-approved2"
    sym = "APR/USDT"

    envelopes = [
        _origin_env(env_id, sym, "2026-06-04T01:40:00+00:00"),
        _approved_env(approved_id, env_id, sym, "2026-06-04T01:40:01+00:00"),
    ]
    bridge = [
        {
            "timestamp_utc": "2026-06-04T01:41:00+00:00",
            "event": "operator_signal_bridge",
            "envelope_id": approved_id,
            "correlation_id": env_id,
            "stage": "rejected_entry_mode",
            "source": "telegram_premium_channel_approved",
            "audit_reason": "premium_paper_execution_disabled",
            "symbol": sym,
            "lifecycle_state": "REJECTED_INVALID_SIGNAL",
        }
    ]

    [entry] = build_trail(envelope_records=envelopes, bridge_records=bridge, paper_records=[])
    assert entry.overall == "ENTRY_DISABLED"
    assert entry.is_open is False
    assert entry.paper_position_state is None
    assert _stage(entry, "bridge").ok is False
    assert _stage(entry, "bridge").label == "Entry disabled"
    assert entry.next_action_hint == "entry_disabled_global"


def test_pending_entry_path():
    """BILL-Pfad: parsed → envelope → approved → bridge pending (entry never reached)."""
    env_id = "ENV-bill-origin"
    approved_id = "ENV-bill-approved"
    sym = "BILL/USDT"

    envelopes = [
        _origin_env(env_id, sym, "2026-05-12T19:08:44+00:00"),
        _approved_env(approved_id, env_id, sym, "2026-05-12T19:08:45+00:00"),
    ]
    bridge = [
        _bridge_pending(env_id, approved_id, sym, "2026-05-12T20:51:38+00:00"),
        _bridge_pending(env_id, approved_id, sym, "2026-05-13T12:00:36+00:00"),
        _bridge_pending(env_id, approved_id, sym, "2026-05-13T12:02:02+00:00"),
    ]

    [entry] = build_trail(envelope_records=envelopes, bridge_records=bridge, paper_records=[])
    assert entry.overall == "PENDING_ENTRY"
    assert entry.is_open is False
    bridge_stage = _stage(entry, "bridge")
    assert bridge_stage.ok is False
    assert bridge_stage.label == "Pending entry"
    assert len(entry.bridge_history) == 3
    assert entry.next_action_hint == "wait_or_reprocess"


def test_not_approved_path():
    """IRYS 00:05-Pfad: parsed → envelope → KEIN approved → kein Bridge-event."""
    env_id = "ENV-irys-origin"
    sym = "IRYS/USDT"

    envelopes = [_origin_env(env_id, sym, "2026-05-12T00:05:36+00:00")]
    [entry] = build_trail(envelope_records=envelopes, bridge_records=[], paper_records=[])
    assert entry.overall == "NOT_APPROVED"
    assert entry.is_open is False
    assert _stage(entry, "approved").ok is False
    assert _stage(entry, "approved").reason == "no_approval_re_emit"
    assert _stage(entry, "bridge").ok is False
    assert _stage(entry, "bridge").reason == "no_bridge_event"
    assert entry.next_action_hint == "manual_fill"


def test_paper_rejected_invalid_sl_path():
    """IRYS 19:09-Pfad: bridge tries to fill, paper rejects (long_sl_at_or_above_price)."""
    env_id = "ENV-irys2-origin"
    approved_id = "ENV-irys2-approved"
    order_id = "ord_b53943f0e680"
    sym = "IRYS/USDT"

    envelopes = [
        _origin_env(
            env_id,
            sym,
            "2026-05-12T19:09:27+00:00",
            scale_factor=None,
            scale_unknown=True,
        ),
        _approved_env(approved_id, env_id, sym, "2026-05-12T19:09:28+00:00"),
    ]
    # Bridge schreibt KEINEN audit-record für invalid-sl (siehe Pi-Realität).
    # Wir testen den Pfad wo nur paper_engine den Reject schreibt.
    paper = [
        {
            "schema_version": "v2",
            "event_type": "order_created",
            "timestamp_utc": "2026-05-12T20:49:36+00:00",
            "order_id": order_id,
            "symbol": sym,
            "side": "buy",
            "correlation_id": env_id,
            "idempotency_key": f"opbridge:{approved_id}",
            "quantity": 24970.5,
            "limit_price": 0.05455,
            "stop_loss": 0.0523,
            "leverage": 10.0,
        },
        _paper_rejected_invalid_sl(order_id, env_id, sym, "2026-05-12T20:49:36+00:00"),
    ]

    [entry] = build_trail(envelope_records=envelopes, bridge_records=[], paper_records=paper)
    assert entry.overall == "PAPER_REJECTED"
    assert entry.paper_position_state == "REJECTED"
    paper_stage = _stage(entry, "paper")
    assert paper_stage.ok is False
    assert paper_stage.reason == "long_sl_at_or_above_price"
    assert entry.next_action_hint == "review_scale"
    assert entry.scale_unknown is True


def test_limit_caps_at_most_recent():
    """limit=N liefert die N JÜNGSTEN Envelopes nach timestamp_utc."""
    envelopes = []
    for i in range(5):
        envelopes.append(
            _origin_env(f"ENV-{i}", f"S{i}/USDT", f"2026-05-{10 + i:02d}T00:00:00+00:00")
        )
    trail = build_trail(envelope_records=envelopes, bridge_records=[], paper_records=[], limit=3)
    assert [t.envelope_id for t in trail] == ["ENV-4", "ENV-3", "ENV-2"]


def test_approval_only_records_dont_create_orphan_entries():
    """Ein _approved-Re-Emit ohne Original-Envelope (data drift) erzeugt KEINE Trail-Zeile."""
    approved_only = _approved_env(
        "ENV-orphan-approved", "ENV-missing-origin", "ORPHAN/USDT", "2026-05-18T12:00:00+00:00"
    )
    trail = build_trail(envelope_records=[approved_only], bridge_records=[], paper_records=[])
    assert trail == []


def _completion_audit(
    symbol: str,
    touch_price: float,
    ts: str,
    *,
    status: str = "orphan_no_match",
    reason: str = "no_open_position_for_symbol",
    raw_text: str | None = None,
) -> dict:
    return {
        "timestamp_utc": ts,
        "event": "target_completion_reconcile",
        "source_envelope_id": f"TGCOMPL-{symbol.replace('/', '')}",
        "symbol": symbol,
        "raw_text": raw_text or f"🎯#{symbol.replace('/', '')} touched {touch_price}",
        "touch_price": touch_price,
        "status": status,
        "reason": reason,
    }


def test_build_orphan_completions_filters_status_and_event():
    records = [
        _completion_audit("OPG/USDT", 3447.0, "2026-05-19T20:51:06+00:00"),
        # closed → not an orphan
        _completion_audit("BIRB/USDT", 13777.0, "2026-05-19T20:51:46+00:00", status="closed"),
        # other event entirely → ignore
        {"event": "some_other_event", "status": "orphan_no_match", "symbol": "X"},
        _completion_audit("IRYS/USDT", 7718.0, "2026-05-19T20:51:13+00:00"),
    ]
    orphans = build_orphan_completions(audit_records=records)
    assert [o.symbol for o in orphans] == ["IRYS/USDT", "OPG/USDT"]
    assert orphans[0].touch_price == 7718.0
    assert orphans[0].reason == "no_open_position_for_symbol"


def test_build_orphan_completions_newest_first_and_limit():
    records = [
        _completion_audit("A/USDT", 1.0, "2026-05-19T20:51:01+00:00"),
        _completion_audit("B/USDT", 2.0, "2026-05-19T20:51:02+00:00"),
        _completion_audit("C/USDT", 3.0, "2026-05-19T20:51:03+00:00"),
    ]
    orphans = build_orphan_completions(audit_records=records, limit=2)
    assert [o.symbol for o in orphans] == ["C/USDT", "B/USDT"]


def test_build_orphan_completions_skips_records_without_symbol():
    records = [
        {
            "event": "target_completion_reconcile",
            "status": "orphan_no_match",
            "symbol": "",
            "touch_price": 1.0,
            "timestamp_utc": "2026-05-19T20:51:01+00:00",
        },
        _completion_audit("OK/USDT", 1.0, "2026-05-19T20:51:02+00:00"),
    ]
    orphans = build_orphan_completions(audit_records=records)
    assert [o.symbol for o in orphans] == ["OK/USDT"]


def test_multiple_targets_passed_through():
    env_id = "ENV-target-test"
    sym = "TEST/USDT"
    envelopes = [
        _origin_env(env_id, sym, "2026-05-18T12:00:00+00:00"),
    ]
    [entry] = build_trail(envelope_records=envelopes, bridge_records=[], paper_records=[])
    assert entry.targets == [1.05, 1.10, 1.15, 1.20]
    assert entry.leverage == 10.0
    assert entry.entry_value == 1.0
    assert entry.stop_loss == 0.95
