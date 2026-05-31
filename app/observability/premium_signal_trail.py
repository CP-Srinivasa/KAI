"""End-to-End Premium-Signal Trail (2026-05-20 /goal).

Verbindet 4 voneinander getrennte JSONL-Audit-Streams zu einem
operatorzentrierten Trail pro Premium-Signal:

1. ``telegram_channel_raw.jsonl``       — Parser-Outcome (parsed / not_a_signal / target_completion)
2. ``telegram_message_envelope.jsonl``   — Envelope (accepted) + Approval-Re-Emit
3. ``bridge_pending_orders.jsonl``       — Bridge-Stage (pending / filled / rejected_* / skipped)
4. ``paper_execution_audit.jsonl``       — Paper-Engine (created / rejected / filled / closed)

Hintergrund: Operator-Wahrnehmung "External grün, aber nicht im Portfolio"
hat in der Praxis 4 verschiedene legitime Ursachen, die im Dashboard
bisher nicht sichtbar waren:

- ``rejected_risk``        — risk_gate (max_open_positions, daily_loss, kill_switch)
- ``entry_not_reached``    — pending bis Markt den Operator-Entry trifft
- ``order_rejected_invalid_sl`` — Scale-Drift, SL liegt für LONG über Spot
- ``closed (tp_tier / stop_loss)`` — Position wurde sauber geschlossen, ist
                              nicht mehr "open" aber im Portfolio-State sichtbar

Pure helpers (``build_trail``) sind IO-frei und gegen die echten
2026-05-11..18 Pi-Audit-Daten getestet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.observability.premium_signal_analytics import (
    SignalAnalytics,
    annotate_source_quality,
    derive_signal_analytics,
)

logger = logging.getLogger(__name__)


# Stage-Status-Schlüssel für die UI-Lanes
STAGE_PARSED = "parsed"
STAGE_ENVELOPE = "envelope"
STAGE_APPROVED = "approved"
STAGE_BRIDGE = "bridge"
STAGE_PAPER = "paper"
STAGE_CLOSED = "closed"

# Bridge-Stages die als "filled and complete" zählen
_BRIDGE_FILLED_STAGES = frozenset({"filled", "filled_duplicate_suppressed"})

# Bridge-Stages die als "rejected"  zählen
_BRIDGE_REJECT_STAGES = frozenset(
    {
        "rejected_risk",
        "rejected_size",
        "rejected_incomplete",
        "rejected_fill",
        "rejected_position_exists",
        "rejected_short_unsupported",
        "rejected_scale_review",  # 2026-05-21 IRYS-Bug-Härtung
    }
)

_BRIDGE_PENDING_STAGES = frozenset({"pending"})
_BRIDGE_SKIPPED_STAGES = frozenset({"skipped_source"})
_BRIDGE_EXPIRED_STAGES = frozenset({"expired"})

# Paper-engine-Events die als Position-Close zählen
_PAPER_CLOSE_EVENTS = frozenset({"position_closed", "order_rejected_invalid_sl"})


@dataclass
class StageStatus:
    """UI-Lane-Status für eine Pipeline-Stufe."""

    name: str
    ok: bool
    label: str
    ts: str | None = None
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "ok": self.ok,
            "label": self.label,
        }
        if self.ts is not None:
            out["ts"] = self.ts
        if self.reason is not None:
            out["reason"] = self.reason
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class TrailEntry:
    """End-to-End-Trail für ein Premium-Signal."""

    envelope_id: str
    source_uid: str | None
    source_platform: str | None
    symbol: str
    received_at: str | None
    direction: str | None
    side: str | None
    entry_value: float | None
    stop_loss: float | None
    targets: list[float]
    leverage: float | None
    scale_factor: float | None
    scale_unknown: bool
    stages: list[StageStatus]
    overall: str
    is_open: bool
    realized_pnl_usd: float | None
    next_action_hint: str
    approved_envelope_id: str | None = None
    bridge_history: list[dict[str, Any]] = field(default_factory=list)
    paper_order_id: str | None = None
    paper_position_state: str | None = None
    paper_close_reason: str | None = None
    quantity: float | None = None
    analytics: SignalAnalytics | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "source_uid": self.source_uid,
            "source_platform": self.source_platform,
            "symbol": self.symbol,
            "received_at": self.received_at,
            "direction": self.direction,
            "side": self.side,
            "entry_value": self.entry_value,
            "stop_loss": self.stop_loss,
            "targets": list(self.targets),
            "leverage": self.leverage,
            "scale_factor": self.scale_factor,
            "scale_unknown": self.scale_unknown,
            "stages": [s.to_dict() for s in self.stages],
            "overall": self.overall,
            "is_open": self.is_open,
            "realized_pnl_usd": self.realized_pnl_usd,
            "next_action_hint": self.next_action_hint,
            "approved_envelope_id": self.approved_envelope_id,
            "bridge_history": list(self.bridge_history),
            "paper_order_id": self.paper_order_id,
            "paper_position_state": self.paper_position_state,
            "paper_close_reason": self.paper_close_reason,
            "quantity": self.quantity,
            "analytics": self.analytics.to_dict() if self.analytics is not None else None,
        }


def _payload(env: dict[str, Any]) -> dict[str, Any]:
    p = env.get("payload")
    return p if isinstance(p, dict) else {}


def _safe_float(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _safe_str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _signal_envelopes(
    envelope_records: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    """Sortiere envelope-records nach Original vs. Approved-Re-Emit.

    Returns:
    - originals: ``{envelope_id: env_record}`` für Original-Signal-Envelopes
                 (source != "_approved")
    - approved:  ``{origin_envelope_id: approved_env_record}`` für
                 Approval-Re-Emits (source endet auf "_approved")
    - approved_id_by_origin: ``{origin_envelope_id: approved_envelope_id}``
    """
    originals: dict[str, dict[str, Any]] = {}
    approved_by_origin: dict[str, dict[str, Any]] = {}
    approved_id_by_origin: dict[str, str] = {}

    for rec in envelope_records:
        if rec.get("message_type") != "signal":
            continue
        if rec.get("stage") != "accepted" or rec.get("status") != "ok":
            continue
        env_id = rec.get("envelope_id")
        if not isinstance(env_id, str):
            continue
        source = rec.get("source")
        source_str = source if isinstance(source, str) else ""
        if source_str.endswith("_approved"):
            origin = rec.get("origin_envelope_id")
            if isinstance(origin, str) and origin:
                approved_by_origin[origin] = rec
                approved_id_by_origin[origin] = env_id
        else:
            originals[env_id] = rec
    return originals, approved_by_origin, approved_id_by_origin


def _bridge_history_for_envelope(
    bridge_records: list[dict[str, Any]],
    *,
    origin_envelope_id: str,
    approved_envelope_id: str | None,
) -> list[dict[str, Any]]:
    """Alle Bridge-Audit-Events für ein Origin- oder Approved-Envelope.

    Bridge benutzt ``correlation_id == origin_envelope_id`` durchgehend,
    aber ``envelope_id`` ist das Approved-Re-Emit-Envelope für gefillte
    Records. Wir matchen über beide Wege, deduplizieren über ts+stage.
    """
    history: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for rec in bridge_records:
        corr = rec.get("correlation_id")
        env = rec.get("envelope_id")
        match = False
        if isinstance(corr, str) and corr == origin_envelope_id:
            match = True
        elif approved_envelope_id and isinstance(env, str) and env == approved_envelope_id:
            match = True
        if not match:
            continue
        ts_raw = rec.get("timestamp_utc")
        stage_raw = rec.get("stage")
        ts = ts_raw if isinstance(ts_raw, str) else None
        stage = stage_raw if isinstance(stage_raw, str) else None
        key = (ts, stage)
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {
            "ts": ts,
            "stage": stage,
            "audit_reason": rec.get("audit_reason"),
        }
        if "lifecycle_state" in rec:
            entry["lifecycle_state"] = rec.get("lifecycle_state")
        if "order_id" in rec:
            entry["order_id"] = rec.get("order_id")
        if "fill_price" in rec:
            entry["fill_price"] = rec.get("fill_price")
        if "quantity" in rec:
            entry["quantity"] = rec.get("quantity")
        history.append(entry)
    history.sort(key=lambda r: r.get("ts") or "")
    return history


def _paper_events_for_envelope(
    paper_records: list[dict[str, Any]],
    *,
    origin_envelope_id: str,
    approved_envelope_id: str | None,
) -> list[dict[str, Any]]:
    """Paper-engine-Events die zur initialen Eröffnung gehören.

    Match-Strategien (in dieser Reihenfolge):
    1. ``correlation_id`` enthält origin_envelope_id
    2. ``idempotency_key`` enthält ``opbridge:<approved_envelope_id>``

    Pre-V4.1 TP-Tier-Sells haben ``correlation_id=""`` — die werden über
    ``_attach_tp_tier_sells`` symbol+timestamp-basiert nachgezogen.
    """
    out: list[dict[str, Any]] = []
    needle_idem = f"opbridge:{approved_envelope_id}" if approved_envelope_id else None
    for rec in paper_records:
        corr = rec.get("correlation_id")
        idem = rec.get("idempotency_key")
        match = False
        if isinstance(corr, str) and corr == origin_envelope_id:
            match = True
        elif needle_idem and isinstance(idem, str) and idem == needle_idem:
            match = True
        if match:
            out.append(rec)
    out.sort(key=lambda r: r.get("timestamp_utc") or "")
    return out


def _legacy_close_reason(paper_events: list[dict[str, Any]]) -> str:
    """Leite den Close-Trigger für pre-V4.1 close-Pfade aus idempotency_keys ab.

    Pre-V4.1 paper-engine emittiert keine ``position_closed`` events — der
    konkrete Trigger steckt im ``idempotency_key`` der zugehörigen
    sell-side ``order_created``-Records:

    - ``tp_tier_<symbol>_<ts>_<price>``  → "tp_tier"
    - ``stop_loss_<symbol>_<ts>``        → "stop_loss"
    - ``repair-close:<symbol>:<ts>``     → "manual"

    Fallback: "tp_tier_or_sl_legacy" — wenn das idempotency_key ein
    unbekanntes Pattern hat (z.B. bei zukünftigen Close-Triggern).
    """
    for ev in paper_events:
        if ev.get("side") != "sell":
            continue
        if (ev.get("event_type") or ev.get("event")) != "order_created":
            continue
        idem = ev.get("idempotency_key")
        if not isinstance(idem, str):
            continue
        if idem.startswith("tp_tier_"):
            return "tp_tier"
        if idem.startswith("stop_loss_"):
            return "stop_loss"
        if idem.startswith("repair-close:"):
            return "manual"
    return "tp_tier_or_sl_legacy"


def _attach_tp_tier_sells(
    base_events: list[dict[str, Any]],
    paper_records: list[dict[str, Any]],
    *,
    symbol: str,
) -> list[dict[str, Any]]:
    """Pre-V4.1 TP-tier-sells über symbol+buy-fill-ts an die Buy-Events anhängen.

    Pre-V4.1 paper_engine emittierte sell-side ``order_filled`` Events für
    TP-Tiers mit ``correlation_id=""``. Verlinkung läuft über
    ``idempotency_key="tp_tier_<symbol>_<buy_fill_ts>_<tp_price>"``.

    Wir suchen den buy-fill-Timestamp aus ``base_events``, scannen dann
    ``paper_records`` nach sell-events deren idempotency_key
    ``tp_tier_{symbol}_{buy_fill_ts}`` enthält und fügen sie dem Trail an.

    Fallback (NEW 2026-05-20): wenn KEINE idempotency-Key-Match vorliegt
    aber symbol+sell-side ts ist NACH dem buy-fill-ts UND vor dem nächsten
    buy-fill für dasselbe symbol — als "no-correlation TP-tier" zuordnen.
    Konservativ, akzeptiert false negatives bei lückenhaftem Audit.
    """
    # The TP-tier sell idempotency_key embeds the FILL timestamp
    # (paper_engine.filled_at), not the audit-emit timestamp_utc — they
    # differ by microseconds. Pick filled_at when present, else fall back.
    buy_fill_ts: str | None = None
    for ev in base_events:
        ev_type = ev.get("event_type") or ev.get("event")
        if (ev_type == "order_filled" or ev.get("status") == "filled") and ev.get("side") == "buy":
            filled_at_raw = ev.get("filled_at")
            ts_raw = ev.get("timestamp_utc")
            if isinstance(filled_at_raw, str):
                buy_fill_ts = filled_at_raw
            elif isinstance(ts_raw, str):
                buy_fill_ts = ts_raw
            if buy_fill_ts:
                break
    if buy_fill_ts is None:
        return base_events

    # Close-Pattern (pre-V4.1 paper-engine schreibt sell-side order_created
    # mit einem der drei Pattern, je nach Trigger):
    # - TP-Tier-Trigger:     "tp_tier_<symbol>_<buy_fill_ts>_<tp_price>"
    # - Manual Repair-Close: "repair-close:<symbol>:<unix_ts>"
    # - Stop-Loss-Trigger:   "stop_loss_<symbol>_<buy_fill_ts>"
    # Idempotency-Key auf order_filled-Side ist None, deshalb Pass 2
    # über order_id-Match die zugehörigen order_filled-Records anhängen.
    tp_tier_needle = f"tp_tier_{symbol}_{buy_fill_ts}"
    sl_needle = f"stop_loss_{symbol}_{buy_fill_ts}"
    repair_needle = f"repair-close:{symbol}:"

    close_order_ids: set[str] = set()
    attached: list[dict[str, Any]] = list(base_events)
    # Pass 1: close-side order_created records via idempotency_key match.
    for rec in paper_records:
        if rec.get("symbol") != symbol:
            continue
        if rec.get("side") != "sell":
            continue
        idem = rec.get("idempotency_key")
        if not isinstance(idem, str):
            continue
        matches_pattern = (
            tp_tier_needle in idem or sl_needle in idem or idem.startswith(repair_needle)
        )
        if matches_pattern:
            attached.append(rec)
            oid = rec.get("order_id")
            if isinstance(oid, str) and oid:
                close_order_ids.add(oid)
    # Pass 2: order_filled sells deren order_id zu einem der Close-Orders
    # gehört (order_filled in pre-V4.1 hat keinen idempotency_key).
    if close_order_ids:
        for rec in paper_records:
            if rec.get("symbol") != symbol or rec.get("side") != "sell":
                continue
            oid = rec.get("order_id")
            if not isinstance(oid, str) or oid not in close_order_ids:
                continue
            if rec in attached:
                continue
            attached.append(rec)
    attached.sort(key=lambda r: r.get("timestamp_utc") or "")
    return attached


_DeriveResult = tuple[
    list[StageStatus],
    str,
    bool,
    str | None,
    str | None,
    str | None,
    float | None,
    float | None,
]


def _derive_stages(
    *,
    origin_env: dict[str, Any],
    approved_env: dict[str, Any] | None,
    bridge_history: list[dict[str, Any]],
    paper_events: list[dict[str, Any]],
) -> _DeriveResult:
    """Leitet UI-Stage-Status, overall-Label und next_action_hint ab.

    Returns:
    - stages
    - overall (OPEN | CLOSED | BRIDGE_REJECTED | PAPER_REJECTED | PENDING_ENTRY |
               NOT_APPROVED | SOURCE_SKIPPED | EXPIRED | UNKNOWN)
    - is_open
    - paper_order_id
    - paper_position_state
    - paper_close_reason
    - realized_pnl_usd  (NUR aus position_closed.trade_pnl_usd)
    - quantity
    """
    stages: list[StageStatus] = []

    # 1. parsed (envelope-existence beweist parsed)
    stages.append(
        StageStatus(
            name=STAGE_PARSED,
            ok=True,
            label="Parsed",
            ts=origin_env.get("timestamp_utc"),
        )
    )

    # 2. envelope (origin-envelope, always present when this trail-entry exists)
    stages.append(
        StageStatus(
            name=STAGE_ENVELOPE,
            ok=True,
            label="Envelope",
            ts=origin_env.get("timestamp_utc"),
            detail={"envelope_id": origin_env.get("envelope_id")},
        )
    )

    # 3. approved
    if approved_env is not None:
        stages.append(
            StageStatus(
                name=STAGE_APPROVED,
                ok=True,
                label="Approved",
                ts=approved_env.get("timestamp_utc"),
                detail={
                    "approved_by": approved_env.get("approved_by"),
                    "approved_envelope_id": approved_env.get("envelope_id"),
                },
            )
        )
    else:
        stages.append(
            StageStatus(
                name=STAGE_APPROVED,
                ok=False,
                label="Not approved",
                reason="no_approval_re_emit",
            )
        )

    # 4. bridge — wähle den repräsentativsten Stage (filled > rejected > skipped > pending > none)
    bridge_stage: str | None = None
    bridge_reason: str | None = None
    bridge_ts: str | None = None
    for entry in bridge_history:
        s = entry.get("stage")
        if s in _BRIDGE_FILLED_STAGES:
            bridge_stage = "filled"
            bridge_reason = entry.get("audit_reason")
            bridge_ts = entry.get("ts")
            break
    if bridge_stage is None:
        for entry in bridge_history:
            s = entry.get("stage")
            if s in _BRIDGE_REJECT_STAGES:
                bridge_stage = s
                bridge_reason = entry.get("audit_reason")
                bridge_ts = entry.get("ts")
                break
    if bridge_stage is None:
        for entry in bridge_history:
            s = entry.get("stage")
            if s in _BRIDGE_SKIPPED_STAGES:
                bridge_stage = s
                bridge_reason = entry.get("audit_reason")
                bridge_ts = entry.get("ts")
                break
    if bridge_stage is None:
        for entry in bridge_history:
            s = entry.get("stage")
            if s in _BRIDGE_EXPIRED_STAGES:
                bridge_stage = s
                bridge_reason = entry.get("audit_reason")
                bridge_ts = entry.get("ts")
                break
    if bridge_stage is None:
        for entry in bridge_history:
            s = entry.get("stage")
            if s in _BRIDGE_PENDING_STAGES:
                bridge_stage = s
                bridge_reason = entry.get("audit_reason")
                bridge_ts = entry.get("ts")
                break

    if bridge_stage in _BRIDGE_FILLED_STAGES:
        stages.append(
            StageStatus(
                name=STAGE_BRIDGE,
                ok=True,
                label="Filled",
                ts=bridge_ts,
                reason=bridge_reason,
            )
        )
    elif bridge_stage in _BRIDGE_REJECT_STAGES:
        stages.append(
            StageStatus(
                name=STAGE_BRIDGE,
                ok=False,
                label="Rejected",
                ts=bridge_ts,
                reason=bridge_reason or bridge_stage,
                detail={"bridge_stage": bridge_stage},
            )
        )
    elif bridge_stage in _BRIDGE_SKIPPED_STAGES:
        stages.append(
            StageStatus(
                name=STAGE_BRIDGE,
                ok=False,
                label="Source skipped",
                ts=bridge_ts,
                reason=bridge_reason or "source_not_allowlisted",
            )
        )
    elif bridge_stage in _BRIDGE_EXPIRED_STAGES:
        stages.append(
            StageStatus(
                name=STAGE_BRIDGE,
                ok=False,
                label="Expired",
                ts=bridge_ts,
                reason=bridge_reason or "ttl_expired",
            )
        )
    elif bridge_stage in _BRIDGE_PENDING_STAGES:
        stages.append(
            StageStatus(
                name=STAGE_BRIDGE,
                ok=False,
                label="Pending entry",
                ts=bridge_ts,
                reason=bridge_reason or "entry_not_reached",
            )
        )
    else:
        stages.append(
            StageStatus(
                name=STAGE_BRIDGE,
                ok=False,
                label="Not picked up",
                reason="no_bridge_event",
            )
        )

    # 5. paper-engine
    paper_order_id: str | None = None
    paper_position_state: str | None = None
    paper_close_reason: str | None = None
    realized_pnl_usd: float | None = None
    quantity: float | None = None
    paper_filled_ts: str | None = None
    paper_rejected_reason: str | None = None
    is_open = False

    # Two pass: erst alle buy-fills (open the position), dann sells (TP-tier
    # close) oder explizite position_closed events (V4.1+). Wenn position_closed
    # events da sind, hat das Priorität für trade_pnl_usd. Pre-V4.1 paper-engine
    # emittiert nur sell-side order_filled mit kumulativem realized_pnl_usd —
    # wir können dort keine per-trade PnL ableiten (siehe Memory
    # paper_audit_pnl_field_semantics) und lassen realized_pnl_usd=None.
    buy_fills = 0
    sell_fills = 0
    last_sell_ts: str | None = None
    has_position_closed = any(
        (ev.get("event_type") or ev.get("event")) == "position_closed" for ev in paper_events
    )

    for ev in paper_events:
        event_type = ev.get("event_type") or ev.get("event")
        # Order-Identität + Quantity aus dem ersten Event ziehen das sie hat
        # (order_created hat sie immer, order_filled fast immer, position_closed
        # selten — aber Pi-Realität ist heterogen genug, dass blind nur auf
        # order_created zu warten in synthetischen Tests scheitert).
        if not paper_order_id:
            order_id_raw = ev.get("order_id")
            if isinstance(order_id_raw, str) and order_id_raw:
                paper_order_id = order_id_raw
        if quantity is None:
            qty_candidate = _safe_float(ev.get("quantity"))
            if qty_candidate is not None and qty_candidate > 0:
                quantity = qty_candidate
        side = ev.get("side")
        is_fill = event_type == "order_filled" or ev.get("status") == "filled"
        if is_fill and side == "buy":
            buy_fills += 1
            paper_filled_ts = ev.get("timestamp_utc")
        elif is_fill and side == "sell":
            sell_fills += 1
            last_sell_ts = ev.get("timestamp_utc")
        if event_type == "position_opened":
            paper_position_state = "POSITION_OPEN"
        if event_type and event_type.startswith("order_rejected"):
            paper_position_state = "REJECTED"
            paper_rejected_reason = ev.get("reason")
        if event_type == "position_closed":
            paper_position_state = "POSITION_CLOSED"
            paper_close_reason = ev.get("reason")
            # NEO-P-101-r2: trade_pnl_usd ist per-trade PnL, NICHT der
            # legacy kumulative realized_pnl_usd-Alias auf dem fill-Record.
            trade_pnl = _safe_float(ev.get("trade_pnl_usd"))
            if trade_pnl is not None:
                realized_pnl_usd = (realized_pnl_usd or 0.0) + trade_pnl

    # Synthese: was sagt die Audit-Trail-Spur? Drei Cases:
    # 1. position_closed events vorhanden → V4.1+, position_closed-Pfad gilt
    # 2. buy + sell fills, kein position_closed → pre-V4.1 close via TP/SL
    # 3. nur buy fills → noch open
    if paper_position_state not in {"REJECTED", "POSITION_CLOSED"}:
        if buy_fills > 0 and sell_fills > 0 and not has_position_closed:
            # Pre-V4.1 paper-engine close. Der konkrete Close-Trigger wird
            # aus dem idempotency_key der order_created sell-side abgeleitet.
            # trade_pnl_usd ist nicht ableitbar — die sell-events tragen nur
            # den legacy kumulativen realized_pnl_usd-Alias.
            paper_position_state = "POSITION_CLOSED"
            paper_close_reason = _legacy_close_reason(paper_events)
            paper_filled_ts = last_sell_ts or paper_filled_ts
        elif buy_fills > 0:
            paper_position_state = "POSITION_OPEN"

    is_open = paper_position_state == "POSITION_OPEN"

    if paper_position_state == "POSITION_OPEN" and is_open:
        stages.append(
            StageStatus(
                name=STAGE_PAPER,
                ok=True,
                label="Position open",
                ts=paper_filled_ts,
                detail={"order_id": paper_order_id, "quantity": quantity},
            )
        )
    elif paper_position_state == "POSITION_CLOSED":
        stages.append(
            StageStatus(
                name=STAGE_PAPER,
                ok=True,
                label="Position closed",
                ts=paper_filled_ts,
                reason=paper_close_reason,
                detail={"order_id": paper_order_id, "quantity": quantity},
            )
        )
    elif paper_position_state == "REJECTED":
        stages.append(
            StageStatus(
                name=STAGE_PAPER,
                ok=False,
                label="Paper rejected",
                reason=paper_rejected_reason or "order_rejected",
                detail={"order_id": paper_order_id},
            )
        )
    elif bridge_stage in _BRIDGE_FILLED_STAGES:
        # Bridge sagt filled, paper hat keinen Event — unsicher
        stages.append(
            StageStatus(
                name=STAGE_PAPER,
                ok=False,
                label="Paper event missing",
                reason="bridge_filled_but_paper_silent",
            )
        )
    else:
        stages.append(
            StageStatus(
                name=STAGE_PAPER,
                ok=False,
                label="Not opened",
                reason=bridge_reason or bridge_stage or "no_bridge_progress",
            )
        )

    # 6. closed (Status-Stufe — wenn paper_position_state == POSITION_CLOSED ist
    # die Position vollständig geschlossen oder durch TP/SL gegangen)
    if paper_position_state == "POSITION_CLOSED":
        stages.append(
            StageStatus(
                name=STAGE_CLOSED,
                ok=True,
                label=f"Closed ({paper_close_reason or 'n/a'})",
                reason=paper_close_reason,
                detail={"realized_pnl_usd": realized_pnl_usd},
            )
        )
    elif is_open:
        stages.append(
            StageStatus(
                name=STAGE_CLOSED,
                ok=False,
                label="Still open",
            )
        )
    else:
        # Position wurde nie eröffnet — Closed-Stage ist N/A
        stages.append(
            StageStatus(
                name=STAGE_CLOSED,
                ok=False,
                label="—",
                reason="position_never_opened",
            )
        )

    # Overall-Label ableiten
    overall: str
    if paper_position_state == "POSITION_OPEN":
        overall = "OPEN"
    elif paper_position_state == "POSITION_CLOSED":
        overall = "CLOSED"
    elif paper_position_state == "REJECTED":
        overall = "PAPER_REJECTED"
    elif bridge_stage in _BRIDGE_REJECT_STAGES:
        overall = "BRIDGE_REJECTED"
    elif bridge_stage in _BRIDGE_SKIPPED_STAGES:
        overall = "SOURCE_SKIPPED"
    elif bridge_stage in _BRIDGE_EXPIRED_STAGES:
        overall = "EXPIRED"
    elif bridge_stage in _BRIDGE_PENDING_STAGES:
        overall = "PENDING_ENTRY"
    elif approved_env is None:
        overall = "NOT_APPROVED"
    else:
        overall = "UNKNOWN"

    return (
        stages,
        overall,
        is_open,
        paper_order_id,
        paper_position_state,
        paper_close_reason,
        realized_pnl_usd,
        quantity,
    )


def _next_action_hint(
    *,
    overall: str,
    approved_env: dict[str, Any] | None,
    paper_position_state: str | None,
) -> str:
    """Operator-orientierte Empfehlung welcher Button gebraucht wird."""
    if overall == "NOT_APPROVED" or approved_env is None and overall != "OPEN":
        return "manual_fill"
    if overall == "PENDING_ENTRY":
        return "wait_or_reprocess"
    if overall == "BRIDGE_REJECTED":
        return "review_reason"
    if overall == "PAPER_REJECTED":
        return "review_scale"
    if overall == "EXPIRED":
        return "expired_review"
    if overall == "SOURCE_SKIPPED":
        return "review_allowlist"
    if overall == "OPEN":
        return "monitor"
    if overall == "CLOSED":
        return "none"
    return "none"


def build_trail(
    *,
    raw_records: list[dict[str, Any]] | None = None,
    envelope_records: list[dict[str, Any]],
    bridge_records: list[dict[str, Any]],
    paper_records: list[dict[str, Any]],
    limit: int = 20,
) -> list[TrailEntry]:
    """End-to-End-Trail für die jüngsten ``limit`` Premium-Signale.

    Joint die 4 Audit-Streams pro Original-Envelope-ID. Approval-Re-Emit
    Envelopes werden als Sub-Stage referenziert, erzeugen aber keinen
    eigenen Trail-Eintrag.

    Pure function — kein IO, kein settings-Read. Tests mocken die Listen.
    """
    del raw_records  # parser-outcomes sind im envelope-record bereits implizit "parsed"

    originals, approved_by_origin, approved_id_by_origin = _signal_envelopes(envelope_records)

    # Sortiere Original-Envelopes nach Empfangszeit (neueste zuerst)
    ordered = sorted(
        originals.values(),
        key=lambda r: r.get("timestamp_utc") or "",
        reverse=True,
    )

    trail: list[TrailEntry] = []
    for env in ordered[:limit]:
        env_id = env.get("envelope_id")
        if not isinstance(env_id, str):
            continue
        approved = approved_by_origin.get(env_id)
        approved_env_id = approved_id_by_origin.get(env_id)
        bridge_history = _bridge_history_for_envelope(
            bridge_records,
            origin_envelope_id=env_id,
            approved_envelope_id=approved_env_id,
        )
        base_paper_events = _paper_events_for_envelope(
            paper_records,
            origin_envelope_id=env_id,
            approved_envelope_id=approved_env_id,
        )

        payload = _payload(env)
        symbol = _safe_str(payload.get("display_symbol")) or _safe_str(payload.get("symbol")) or "?"
        # Pre-V4.1 TP-Tier-Sells haben correlation_id="" — über symbol+
        # buy-fill-ts nachziehen. V4.1+ position_closed-Events haben
        # eigene correlation_id und sind bereits in base_paper_events.
        paper_events = _attach_tp_tier_sells(
            base_paper_events,
            paper_records,
            symbol=symbol,
        )
        targets_raw = payload.get("targets")
        targets: list[float] = []
        if isinstance(targets_raw, list):
            for t in targets_raw:
                f = _safe_float(t)
                if f is not None:
                    targets.append(f)

        (
            stages,
            overall,
            is_open,
            paper_order_id,
            paper_position_state,
            paper_close_reason,
            realized_pnl_usd,
            quantity,
        ) = _derive_stages(
            origin_env=env,
            approved_env=approved,
            bridge_history=bridge_history,
            paper_events=paper_events,
        )

        entry = TrailEntry(
            envelope_id=env_id,
            source_uid=_safe_str(env.get("source_uid")) or _safe_str(payload.get("source_uid")),
            source_platform=_safe_str(env.get("source_platform"))
            or _safe_str(payload.get("source_platform")),
            symbol=symbol,
            received_at=_safe_str(env.get("timestamp_utc")),
            direction=_safe_str(payload.get("direction")),
            side=_safe_str(payload.get("side")),
            entry_value=_safe_float(payload.get("entry_value")),
            stop_loss=_safe_float(payload.get("stop_loss")),
            targets=targets,
            leverage=_safe_float(payload.get("leverage")),
            scale_factor=_safe_float(payload.get("scale_factor")),
            scale_unknown=bool(payload.get("scale_unknown")),
            stages=stages,
            overall=overall,
            is_open=is_open,
            realized_pnl_usd=realized_pnl_usd,
            next_action_hint=_next_action_hint(
                overall=overall,
                approved_env=approved,
                paper_position_state=paper_position_state,
            ),
            approved_envelope_id=approved_env_id,
            bridge_history=bridge_history,
            paper_order_id=paper_order_id,
            paper_position_state=paper_position_state,
            paper_close_reason=paper_close_reason,
            quantity=quantity,
        )
        entry.analytics = derive_signal_analytics(
            payload=payload,
            source=_safe_str(env.get("source")),
            received_at=entry.received_at,
            overall=overall,
            realized_pnl_usd=realized_pnl_usd,
            paper_events=paper_events,
            bridge_history=bridge_history,
            paper_close_reason=paper_close_reason,
            scale_unknown=entry.scale_unknown,
        )
        trail.append(entry)

    # Zweiter Pass: Source-Quality über das gesamte Trail-Fenster aggregieren
    # (eine einzelne Zeile kann keine Quelle bewerten). Mutiert analytics
    # in-place und baut die analysis_hints mit dem Quality-Status neu auf.
    annotate_source_quality([(e, e.analytics) for e in trail if e.analytics is not None])

    return trail


@dataclass
class OrphanCompletion:
    """🎯-Completion-Meldung ohne passende offene Position.

    Wurzel 2026-05-19/20: Operator-Auftrag /goal explicit fordert sichtbare
    orphans statt stiller Ignorierung. Reconciler schreibt
    ``target_completion_audit.jsonl`` mit ``status="orphan_no_match"`` —
    die Trail-UI zeigt sie als eigene Liste unter dem Envelope-Trail.
    """

    timestamp_utc: str | None
    symbol: str
    touch_price: float | None
    reason: str | None
    source_envelope_id: str | None
    raw_text: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "symbol": self.symbol,
            "touch_price": self.touch_price,
            "reason": self.reason,
            "source_envelope_id": self.source_envelope_id,
            "raw_text": self.raw_text,
        }


def build_orphan_completions(
    *,
    audit_records: list[dict[str, Any]],
    limit: int = 20,
) -> list[OrphanCompletion]:
    """Filtert ``target_completion_reconcile``-Records auf ``orphan_no_match``.

    Newest first nach ``timestamp_utc``. Liefert max ``limit`` Einträge.
    Pure — kein IO. Caller liefert die JSONL-Records via ``_read_jsonl``.
    """
    orphans: list[OrphanCompletion] = []
    for rec in audit_records:
        if rec.get("event") != "target_completion_reconcile":
            continue
        if rec.get("status") != "orphan_no_match":
            continue
        symbol_raw = rec.get("symbol")
        if not isinstance(symbol_raw, str) or not symbol_raw:
            continue
        orphans.append(
            OrphanCompletion(
                timestamp_utc=_safe_str(rec.get("timestamp_utc")),
                symbol=symbol_raw,
                touch_price=_safe_float(rec.get("touch_price")),
                reason=_safe_str(rec.get("reason")),
                source_envelope_id=_safe_str(rec.get("source_envelope_id")),
                raw_text=_safe_str(rec.get("raw_text")),
            )
        )
    orphans.sort(key=lambda o: o.timestamp_utc or "", reverse=True)
    return orphans[:limit]


__all__ = [
    "OrphanCompletion",
    "StageStatus",
    "TrailEntry",
    "build_orphan_completions",
    "build_trail",
]
