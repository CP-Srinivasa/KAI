"""Premium-Signal Operator-Actions API.

2026-05-12 Sprint E (per Operator-Auftrag "Premium Telegram Signals End-to-End
Execution Fix" Sektion 10): 5 Dashboard-Buttons + idempotente Backend-Endpoints.

Endpoints
---------
- POST /api/premium-signals/manual-fill {envelope_id, idempotency_key?}
    Force the envelope through the approval+bridge path. Operator-Override
    für nicht-approved Premium-Signale.

- POST /api/premium-signals/reprocess {envelope_id?, idempotency_key?}
    Run a bridge tick. envelope_id optional — falls gegeben, wird der erste
    Tick auf diese envelope-id-spezifische Verarbeitung beschränkt (Klick im
    UI für "diesen einen pending envelope nochmal versuchen").

- POST /api/premium-signals/reconcile-target-completion
       {symbol, touch_price?, idempotency_key?}
    Manueller Reconcile wenn 🎯-Meldung nicht aus dem Channel kommt. Operator
    weiß z.B. dass Position außerhalb-Channel geschlossen wurde.

- POST /api/premium-signals/position-repair
       {symbol, action, new_stop_loss?, new_take_profit?, idempotency_key?}
    Manuelle Position-Modifikation. action ∈ {"close", "adjust"}.

- GET /api/premium-signals/pending-envelopes
    Liste der envelopes mit stage != terminal — Datenquelle für die Button-UI.

Idempotency
-----------
Jeder Body unterstützt `idempotency_key`. Process-lokaler Cache (max 256
Einträge, sliding-window) verhindert doppelte Aktion bei UI-Doppelklick.
Cross-Process (Pi-Cluster) nicht abgesichert — bei Multi-Instanz-Deploy nötig.

Auth
----
CF-Access-Email-Allowlist via app/api/main.py middleware (gleicher Pfad wie
/operator/*). Hier keine zusätzlichen Guards — Pipe-Through.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.execution.envelope_to_paper_bridge import run_tick
from app.execution.target_completion_reconciler import reconcile_target_completion
from app.ingestion.telegram_channel_approval import (
    handle_signal_approval,
    load_envelope_by_id,
)
from app.ingestion.telegram_channel_parser import TargetCompletionEvent

logger = logging.getLogger(__name__)

_ENVELOPE_LOG = Path("artifacts/telegram_message_envelope.jsonl")
_BRIDGE_LOG = Path("artifacts/bridge_pending_orders.jsonl")
_PAPER_LOG = Path("artifacts/paper_execution_audit.jsonl")
_RAW_LOG = Path("artifacts/telegram_channel_raw.jsonl")
_TARGET_COMPLETION_LOG = Path("artifacts/target_completion_audit.jsonl")
_ACTION_AUDIT_LOG = Path("artifacts/premium_signal_actions.jsonl")

router = APIRouter(prefix="/api/premium-signals", tags=["premium-signals"])


# ── Idempotency-Cache (process-local) ──────────────────────────────────────


class _IdempotencyCache:
    """Sliding-window cache für POST-action-Idempotenz."""

    def __init__(self, max_size: int = 256) -> None:
        self._max = max_size
        self._lock = Lock()
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            v = self._cache.get(key)
            if v is not None:
                self._cache.move_to_end(key)
            return v

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            if len(self._cache) > self._max:
                self._cache.popitem(last=False)


_idempotency = _IdempotencyCache()


def _audit_action(action: str, body: dict[str, Any], outcome: dict[str, Any]) -> None:
    """Append an action audit record. Fail-soft: log + swallow OSError."""
    rec = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "event": "premium_signal_action",
        "action": action,
        "body": body,
        "outcome": outcome,
    }
    try:
        _ACTION_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _ACTION_AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("[premium-signals] audit write failed: %s", exc)


def _check_idempotency(key: str | None, action: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """Return cached outcome if key is present and matches; else None."""
    if not key:
        return None
    composite = f"{action}:{key}"
    cached = _idempotency.get(composite)
    if cached is None:
        return None
    # Conservative: only return cached when body matches. Different bodies for
    # the same idempotency_key are a programming error on the client side.
    if cached.get("_body") == body:
        return cached.get("_outcome")
    return None


def _store_idempotency(
    key: str | None, action: str, body: dict[str, Any], outcome: dict[str, Any]
) -> None:
    if not key:
        return
    composite = f"{action}:{key}"
    _idempotency.set(composite, {"_body": body, "_outcome": outcome})


# ── Request Models ────────────────────────────────────────────────────────


class ManualFillRequest(BaseModel):
    envelope_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)
    approved_by: str | None = Field(default=None, max_length=64)


class ReprocessRequest(BaseModel):
    envelope_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ReconcileRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    touch_price: float | None = Field(default=None, gt=0)
    idempotency_key: str | None = Field(default=None, max_length=128)


class PositionRepairRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    action: str = Field(pattern=r"^(close|adjust)$")
    new_stop_loss: float | None = Field(default=None, gt=0)
    new_take_profit: float | None = Field(default=None, gt=0)
    idempotency_key: str | None = Field(default=None, max_length=128)


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.post("/manual-fill")
async def manual_fill(req: ManualFillRequest) -> dict[str, Any]:
    """Force the envelope through approval → bridge pipeline.

    Operator-Override für nicht-approved Signale (z.B. IRYS 2026-05-12 02:05
    wo der Approval-TTL expired weil Operator nicht klickte). Idempotent via
    handle_signal_approval-dedup (origin_envelope_id-Check).
    """
    body = req.model_dump(exclude_none=True)
    cached = _check_idempotency(req.idempotency_key, "manual-fill", body)
    if cached is not None:
        return {**cached, "_idempotency_cached": True}

    if not load_envelope_by_id(_ENVELOPE_LOG, req.envelope_id):
        raise HTTPException(status_code=404, detail=f"envelope_id not found: {req.envelope_id}")

    outcome = handle_signal_approval(
        action="fill",
        envelope_id=req.envelope_id,
        envelope_log=_ENVELOPE_LOG,
        ttl_minutes=24 * 60,  # operator-override: ignore approval TTL
        approved_by=req.approved_by or "manual-dashboard",
    )
    result = {
        "status": outcome.status,
        "reason": outcome.reason,
        "new_envelope_id": outcome.new_envelope_id,
        "origin_envelope_id": outcome.origin_envelope_id,
    }
    _store_idempotency(req.idempotency_key, "manual-fill", body, result)
    _audit_action("manual-fill", body, result)
    return result


@router.post("/reprocess")
async def reprocess(req: ReprocessRequest) -> dict[str, Any]:
    """Trigger a fresh bridge tick.

    When ``envelope_id`` is given the tick is narrowed to that single pending
    envelope ("reprocess this one"); narrowing can only reduce the work a tick
    does, never fill something a full tick would not. Without ``envelope_id``
    it runs a full bridge tick (same as the cron).
    """
    body = req.model_dump(exclude_none=True)
    cached = _check_idempotency(req.idempotency_key, "reprocess", body)
    if cached is not None:
        return {**cached, "_idempotency_cached": True}

    try:
        tick_result = await run_tick(only_envelope_id=req.envelope_id)
        result = {"status": "ok", "tick": tick_result.to_dict()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[premium-signals] reprocess tick failed: %s", exc)
        result = {
            "status": "error",
            "reason": f"tick_exception:{type(exc).__name__}",
            "error": str(exc),
        }
    _store_idempotency(req.idempotency_key, "reprocess", body, result)
    _audit_action("reprocess", body, result)
    return result


@router.post("/reconcile-target-completion")
async def reconcile_completion(req: ReconcileRequest) -> dict[str, Any]:
    """Manual 🎯 all-TP-Reconcile wenn die Channel-Meldung nicht eintrifft.

    Source-envelope-id wird synthetisch erzeugt (RECON-MANUAL-<symbol>-<ts>)
    damit die Idempotency-Spur im target_completion_audit.jsonl konsistent ist.
    """
    body = req.model_dump(exclude_none=True)
    cached = _check_idempotency(req.idempotency_key, "reconcile", body)
    if cached is not None:
        return {**cached, "_idempotency_cached": True}

    symbol = req.symbol.strip().upper()
    display_symbol = symbol if "/" in symbol else _add_quote(symbol)
    internal = display_symbol.replace("/", "")
    synthetic_env_id = f"RECON-MANUAL-{internal}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    event = TargetCompletionEvent(
        symbol=internal,
        display_symbol=display_symbol,
        touch_price=req.touch_price,
        raw_text=f"manual_reconcile via operator dashboard at {datetime.now(UTC).isoformat()}",
    )
    outcome = reconcile_target_completion(event=event, source_envelope_id=synthetic_env_id)
    result = {
        "status": outcome.status,
        "reason": outcome.reason,
        "symbol": outcome.symbol,
        "touch_price": outcome.touch_price,
        "realized_pnl_usd": outcome.realized_pnl_usd,
    }
    _store_idempotency(req.idempotency_key, "reconcile", body, result)
    _audit_action("reconcile-target-completion", body, result)
    return result


@router.post("/position-repair")
async def position_repair(req: PositionRepairRequest) -> dict[str, Any]:
    """Manuelle Position-Modifikation. action="close" schließt zum aktuellen
    market-price. action="adjust" setzt neue SL/TP (mind. eines required).

    Sicherheits-Gates der bridge greifen nicht; das ist eine Operator-Override.
    Audit-Trail bleibt voll erhalten.
    """
    body = req.model_dump(exclude_none=True)
    cached = _check_idempotency(req.idempotency_key, "position-repair", body)
    if cached is not None:
        return {**cached, "_idempotency_cached": True}

    if req.action == "adjust" and req.new_stop_loss is None and req.new_take_profit is None:
        raise HTTPException(
            status_code=400, detail="adjust requires new_stop_loss or new_take_profit"
        )

    # 2026-05-14 P1 #7: Singleton statt new-per-request. Vorher hardcoded
    # initial_equity=10000.0 → unterschiedlich zu Bridge-Engine (die
    # settings.execution.paper_initial_equity las). Singleton vereinheitlicht.
    from app.execution.paper_engine_singleton import get_paper_engine

    eng = get_paper_engine()
    eng.rehydrate_from_audit()
    symbol_input = req.symbol.strip().upper()
    display_symbol = symbol_input if "/" in symbol_input else _add_quote(symbol_input)
    pos = eng.portfolio.positions.get(display_symbol)
    if pos is None:
        raise HTTPException(status_code=404, detail=f"no open paper position for {display_symbol}")

    if req.action == "close":
        # Manual close zum avg_entry_price wenn kein market-data — Operator
        # akzeptiert "Notfall-Close ohne Profit/Loss-Realisation". Bewusst
        # konservativ; alternativ würde man hier market-data-snapshot ziehen.
        #
        # 2026-05-16 V4.1: route via paper_engine.close_position() statt
        # rohem create_order + fill_order. Damit wird der vollständige
        # position_closed-Audit-Event emittiert (inkl. trade_pnl_usd /
        # fee_usd / position_side / reason="manual"). Vor dem Fix hat
        # position-repair NUR ein order_filled-sell geschrieben und damit
        # die Bayes-Posterior-Lernschleife (V4) blind für Premium-Trades
        # gemacht — Premium-Closes wurden klassifiziert als unsourced
        # und ohne PnL-Klassifikation übergangen.
        close_price = pos.avg_entry_price
        try:
            # close_position returns the close-side PaperFill (or None if the
            # position vanished / price invalid / idempotency dedup hit).
            # Its docstring (paper_engine.py:892-901) supersedes the stale
            # tuple-typed return annotation on the function signature.
            close_fill = eng.close_position(
                symbol=display_symbol,
                current_price=close_price,
                reason="manual",
            )
            if close_fill is None:
                result = {
                    "status": "noop",
                    "reason": "close_position_returned_none",
                    "symbol": display_symbol,
                    "close_price": close_price,
                }
            else:
                result = {
                    "status": "closed",
                    "symbol": display_symbol,
                    "close_price": close_fill.fill_price,
                    "quantity_closed": pos.quantity,
                    "trade_pnl_usd": close_fill.pnl_usd,
                    "fee_usd": close_fill.fee_usd,
                }
        except Exception as exc:  # noqa: BLE001
            result = {
                "status": "error",
                "reason": f"close_exception:{type(exc).__name__}",
                "error": str(exc),
            }
    else:  # adjust
        # PaperExecutionEngine hat kein public adjust-API; wir schreiben einen
        # position_adjusted audit-Record direkt damit der rehydrate die neue
        # SL/TP übernimmt. Im audit_replay ist position_adjusted bereits
        # behandelt (line 140+ in audit_replay.py).
        adjust_record: dict[str, Any] = {
            "event_type": "position_adjusted",
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "symbol": display_symbol,
        }
        if req.new_stop_loss is not None:
            adjust_record["stop_loss"] = req.new_stop_loss
        if req.new_take_profit is not None:
            adjust_record["take_profit"] = req.new_take_profit
        try:
            paper_audit = Path("artifacts/paper_execution_audit.jsonl")
            paper_audit.parent.mkdir(parents=True, exist_ok=True)
            with paper_audit.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(adjust_record, ensure_ascii=False) + "\n")
            result = {
                "status": "adjusted",
                "symbol": display_symbol,
                "new_stop_loss": req.new_stop_loss,
                "new_take_profit": req.new_take_profit,
            }
        except OSError as exc:
            result = {"status": "error", "reason": f"audit_write_failed:{exc}"}

    _store_idempotency(req.idempotency_key, "position-repair", body, result)
    _audit_action("position-repair", body, result)
    return result


@router.get("/pending-envelopes")
async def pending_envelopes(limit: int = 50) -> dict[str, Any]:
    """Liste der envelopes die noch keinen terminalen bridge-stage haben.

    Datenquelle für UI-Buttons: zeigt nur die envelopes, für die manual-fill
    oder reprocess sinnvoll sind. Caller filtert weiter clientseitig.
    """
    from app.execution.envelope_to_paper_bridge import (
        _TERMINAL_STAGES,
        _collect_pending_signals,
        _latest_bridge_stage_by_envelope,
        _read_jsonl,
    )

    envelopes = _read_jsonl(_ENVELOPE_LOG)
    bridge_records = _read_jsonl(_BRIDGE_LOG)
    bridge_stages = _latest_bridge_stage_by_envelope(bridge_records)
    pending = _collect_pending_signals(envelopes, bridge_stages)
    # Wir liefern eine kompakte Projection — keine vollen payloads damit der
    # UI nicht 2 MB JSON laden muss.
    rows = []
    for env in pending[-limit:]:
        env_id = env.get("envelope_id")
        if not isinstance(env_id, str):
            continue
        payload_raw = env.get("payload")
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        rows.append(
            {
                "envelope_id": env_id,
                "timestamp_utc": env.get("timestamp_utc"),
                "source": env.get("source"),
                "symbol": payload.get("display_symbol") or payload.get("symbol"),
                "direction": payload.get("direction"),
                "entry_value": payload.get("entry_value"),
                "stop_loss": payload.get("stop_loss"),
                "targets": payload.get("targets"),
                "leverage": payload.get("leverage"),
                "current_bridge_stage": bridge_stages.get(env_id),
            }
        )
    return {
        "count": len(rows),
        "terminal_stages": sorted(_TERMINAL_STAGES),
        "envelopes": rows,
    }


@router.get("/runtime")
async def runtime_status() -> dict[str, Any]:
    """Runtime truth for the premium pipeline safety switches.

    This endpoint is intentionally read-only and exposes the exact gates that
    explain why premium paper orders can be parsed/approved but still not open a
    position. Live execution remains disabled by default and separately gated.
    """
    settings = get_settings()
    entry_mode = settings.execution.entry_mode
    entry_blocks = not entry_mode.allows_risk_increasing_entry
    allowlist = [
        x.strip()
        for x in settings.execution.operator_signal_source_allowlist.split(",")
        if x.strip()
    ]
    allowlist_norm = {x.lower() for x in allowlist}
    premium_sources_allowed = any(
        x.startswith("telegram_premium_channel") or x.startswith("telegram") for x in allowlist_norm
    )
    blocking_reasons: list[str] = []
    if not settings.premium.paper_execution_enabled:
        blocking_reasons.append("premium_paper_execution_disabled")
    if not settings.execution.operator_signal_bridge_enabled:
        blocking_reasons.append("operator_signal_bridge_disabled")
    if entry_blocks:
        blocking_reasons.append(f"entry_mode={entry_mode.value}")
    if not premium_sources_allowed:
        blocking_reasons.append("telegram_premium_channel_not_allowlisted")
    classic_can_open_paper_positions = not blocking_reasons

    # Premium-Fastlane overlay (Goal 2026-06-05). When the fastlane is active it
    # OVERRIDES the classic premium-paper block for authentic premium signals on
    # a non-live route: the classic blocking_reasons remain visible (they are
    # still true for the classic path) but they are no longer the FINAL verdict.
    from app.execution.premium_fastlane import fastlane_status

    fl_status = fastlane_status(settings)
    fastlane_overrides = bool(fl_status["overrides_classic_block"])
    # The premium pipeline can open paper positions if EITHER the classic path is
    # clear OR the fastlane overrides the block.
    can_open_paper_positions = classic_can_open_paper_positions or fastlane_overrides

    if fastlane_overrides:
        warning = (
            "Premium Fastlane aktiv — Classic Execution ist blockiert "
            f"({', '.join(blocking_reasons) or 'keine'}); Fastlane Paper läuft "
            f"(Route: {fl_status['route']}); Live bleibt geschützt."
        )
    elif fl_status["enabled"] and not fl_status["active"]:
        warning = (
            "Premium Fastlane aus — "
            f"Grund: {fl_status['window_reason'] or 'inaktiv'}; "
            "Aktion: Config prüfen (PREMIUM_FASTLANE_*)."
        )
    elif blocking_reasons:
        warning = "Premium Paper Execution blockiert: " + ", ".join(blocking_reasons)
    else:
        warning = None

    return {
        "entry_mode": entry_mode.value,
        "entry_mode_allows_risk_increasing_entry": entry_mode.allows_risk_increasing_entry,
        "entry_mode_blocks_premium_paper": entry_blocks,
        "can_open_paper_positions": can_open_paper_positions,
        "classic_can_open_paper_positions": classic_can_open_paper_positions,
        "blocking_reasons": blocking_reasons,
        "premium_paper_execution_enabled": settings.premium.paper_execution_enabled,
        "premium_live_execution_enabled": settings.premium.live_execution_enabled,
        "premium_require_manual_approval_for_paper": (
            settings.premium.require_manual_approval_for_paper
        ),
        "premium_require_manual_approval_for_live": (
            settings.premium.require_manual_approval_for_live
        ),
        "operator_signal_bridge_enabled": settings.execution.operator_signal_bridge_enabled,
        "operator_signal_source_allowlist": allowlist,
        "premium_auto_fill_enabled": (settings.execution.operator_signal_premium_auto_fill_enabled),
        "live_execution_enabled": settings.execution.live_enabled,
        "execution_mode": settings.execution.mode.value,
        "premium_fastlane": fl_status,
        # Flat structured fields (Goal 2026-06-05 §5) so the Trail/UI never has to
        # infer Classic-Bridge behaviour when a Fastlane state exists.
        "premium_fastlane_enabled": fl_status["enabled"],
        "route": fl_status["route"],
        "premium_fastlane_paper_effective_enabled": bool(
            fl_status["enabled"]
            and fl_status["active"]
            and settings.premium.paper_execution_enabled
        ),
        "entry_mode_bypassed_for_fastlane_paper": bool(fl_status["overrides_classic_block"]),
        "bridge_enabled": settings.execution.operator_signal_bridge_enabled,
        "source_allowlisted": premium_sources_allowed,
        "live_protected": fl_status["live_protected"],
        "warning": warning,
    }


def _add_quote(symbol_upper: str) -> str:
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if symbol_upper.endswith(quote) and len(symbol_upper) > len(quote):
            return f"{symbol_upper[: -len(quote)]}/{quote}"
    return f"{symbol_upper}/USDT"


@router.get("/trail")
async def trail(limit: int = 20) -> dict[str, Any]:
    """End-to-End Premium-Signal-Trail (2026-05-20 /goal).

    Joint die 4 Audit-Streams (raw / envelope / bridge / paper) und liefert
    pro Premium-Signal-Envelope eine Trail-Zeile mit 6 Pipeline-Stages und
    klarem overall-Status. UI-Konsument: Premium-Signal-Trail-Komponente.

    Datenquelle nur lesend — alle Streams sind append-only. ``limit`` cap
    auf 100 damit der UI nicht 5 MB JSON laden muss.
    """
    from app.execution.envelope_to_paper_bridge import _read_jsonl
    from app.observability.premium_signal_trail import (
        build_orphan_completions,
        build_trail,
    )

    safe_limit = max(1, min(int(limit), 100))
    envelopes = _read_jsonl(_ENVELOPE_LOG)
    bridge_records = _read_jsonl(_BRIDGE_LOG)
    paper_records = _read_jsonl(_PAPER_LOG)
    completion_records = _read_jsonl(_TARGET_COMPLETION_LOG)

    entries = build_trail(
        envelope_records=envelopes,
        bridge_records=bridge_records,
        paper_records=paper_records,
        limit=safe_limit,
    )
    orphans = build_orphan_completions(
        audit_records=completion_records,
        limit=safe_limit,
    )
    return {
        "count": len(entries),
        "limit": safe_limit,
        "trail": [e.to_dict() for e in entries],
        "orphan_completions": [o.to_dict() for o in orphans],
    }


__all__ = ["router"]
