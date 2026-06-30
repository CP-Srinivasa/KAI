"""Operator-Signal-Bridge — envelope-JSONL to paper_engine.

Bridges the gap between accepted signal envelopes (dashboard paste and
telegram-bot handoff) and actual paper-order fills. Before this worker, a
pasted [SIGNAL] block was audited as ``accepted|ok`` and then vanished:
no downstream consumer existed.

Operator 1:1 semantics:
- Entry / SL / TP1 come from the operator verbatim.
- KAIs own SignalGenerator is NOT invoked.
- Risk-Engine gates still apply (kill-switch, daily-loss, max-positions).
- Position size is computed via Risk-Engine (max_risk_per_trade_pct).
- Channel-stated leverage and margin/risk allocation are carried into the
  ExecutableOrderIntent/audit contract. Paper sizing remains risk-engine-owned.
- Entry-type: range/limit/trigger-style. Range entries fill only when the
  current price is inside the operator range; otherwise the envelope stays
  ``pending`` and is re-checked next tick.
- TTL: after ``ttl_hours`` (default 24) an unfilled envelope is expired.
- Take-profit: TP1 only (``targets[0]``). Staged exits are out of scope.

Fail-closed:
- ``operator_signal_bridge_enabled=False`` (default) -> tick() is a no-op.
- Source not in allowlist -> skipped with audit, no fill.
- Missing entry / stop_loss / targets -> rejected at gate.
- Short/sell signals -> paper short positions via the same ExecutableOrderIntent
  contract (side=SELL, position_side=short).

Audit trails:
- ``artifacts/bridge_pending_orders.jsonl`` — append-only per-envelope
  event log with stages ``pending`` / ``filled`` / ``expired`` /
  ``rejected_*`` / ``skipped_source``.
- ``artifacts/paper_execution_audit.jsonl`` — standard paper_engine
  events when a fill happens (re-used, no new schema).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.enums import EntryMode
from app.core.settings import get_settings
from app.execution.entry_policy import (
    EntryRoute,
    check_route_limits,
    resolve_entry_policy,
)
from app.execution.intent_builder import build_executable_intent as _build_executable_intent
from app.execution.intent_builder import entry_bounds as _entry_bounds
from app.execution.intent_builder import float_or_none as _float
from app.execution.models import (
    IllegalLifecycleTransition,
    OrderLifecycleState,
    make_lifecycle_transition,
)
from app.execution.paper_engine import DuplicateOrderError
from app.execution.paper_engine_singleton import get_paper_engine
from app.execution.premium_fastlane import (
    FastlaneDecision,
    fastlane_entry_mode_override,
    premium_paper_entry_disabled_override,
    resolve_leverage,
    resolve_notional,
    should_route_premium_fastlane,
)
from app.execution.scale_resolver import detect_scale_factor as _detect_scale_factor
from app.market_data.service import get_market_data_snapshot
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.risk.reason_codes import ExecutionBlockerCode

if TYPE_CHECKING:
    from app.execution.paper_engine import PaperExecutionEngine

logger = logging.getLogger(__name__)

_ENVELOPE_LOG = Path("artifacts/telegram_message_envelope.jsonl")
_BRIDGE_LOG = Path("artifacts/bridge_pending_orders.jsonl")
_PAPER_AUDIT_LOG = Path("artifacts/paper_execution_audit.jsonl")

# Terminal stages — envelopes that have reached any of these are done.
# ``rejected_short_unsupported`` is kept in the set for **historical**
# compatibility — pre-V25 envelopes audited with this stage stay terminal
# in replay tools. Live code does not emit it any more (Sprint-B-Bug-#1
# 2026-05-10: short-pfad ist nun nativer paper_engine-Pfad via
# position_side="short" + side="sell" durchgereicht).
_TERMINAL_STAGES = frozenset(
    {
        "filled",
        # 2026-05-12 Sprint C: cross-process Race-Guard. Envelope wurde
        # bereits von einer parallelen run_tick()-Instanz gefüllt; KEIN
        # neuer Fill-Versuch nötig.
        "filled_duplicate_suppressed",
        "expired",
        "rejected_risk",
        "rejected_size",
        "rejected_incomplete",
        "rejected_short_unsupported",  # historical pre-V25 envelopes only
        "rejected_fill",
        "rejected_position_exists",
        "rejected_scale_review",  # 2026-05-21 IRYS-Bug-Härtung
        "skipped_source",
    }
)


@dataclass
class BridgeTickResult:
    enabled: bool
    envelopes_scanned: int = 0
    newly_pending: int = 0
    re_pending: int = 0
    filled: int = 0
    expired: int = 0
    skipped_source: int = 0
    rejected_risk: int = 0
    rejected_size: int = 0
    rejected_entry_mode: int = 0
    rejected_incomplete: int = 0
    # Historical counter — pre-V25 path. Active code never increments it any
    # more (SHORT signals open as native paper short via paper_engine).
    rejected_short: int = 0
    rejected_fill: int = 0
    rejected_position_exists: int = 0
    no_market_data: int = 0
    # Premium-Fastlane (Goal 2026-06-05): count signals routed via the fastlane
    # bypass so the dashboard can distinguish classic fills from fastlane fills.
    fastlane_routed: int = 0
    fastlane_bypassed_allowlist: int = 0
    fastlane_bypassed_entry_mode: int = 0
    # Issue #181: the fastlane wanted to bypass entry_mode=disabled but the
    # two-flag override was not armed → kill-switch held (fail-closed).
    fastlane_entry_mode_override_refused: int = 0
    # Pfad-3 (2026-06-10): a CLASSIC premium signal opened paper while
    # entry_mode=disabled via the premium-paper decoupling override (autonomous
    # loop untouched); and the fail-closed refusal when it was not fully armed.
    premium_paper_entry_disabled_bypassed: int = 0
    premium_paper_entry_disabled_refused: int = 0
    # Sprint S3 (#181): refusals from the explicit limited-paper-mode entry
    # policy — route not open in the active mode / contradiction (fail-closed),
    # and route-volume-limit refusals (#181 §5).
    route_policy_rejected: int = 0
    route_limit_rejected: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "envelopes_scanned": self.envelopes_scanned,
            "newly_pending": self.newly_pending,
            "re_pending": self.re_pending,
            "filled": self.filled,
            "expired": self.expired,
            "skipped_source": self.skipped_source,
            "rejected_risk": self.rejected_risk,
            "rejected_size": self.rejected_size,
            "rejected_entry_mode": self.rejected_entry_mode,
            "rejected_incomplete": self.rejected_incomplete,
            "rejected_short": self.rejected_short,
            "rejected_fill": self.rejected_fill,
            "rejected_position_exists": self.rejected_position_exists,
            "no_market_data": self.no_market_data,
            "fastlane_routed": self.fastlane_routed,
            "fastlane_bypassed_allowlist": self.fastlane_bypassed_allowlist,
            "fastlane_bypassed_entry_mode": self.fastlane_bypassed_entry_mode,
            "fastlane_entry_mode_override_refused": self.fastlane_entry_mode_override_refused,
            "premium_paper_entry_disabled_bypassed": self.premium_paper_entry_disabled_bypassed,
            "premium_paper_entry_disabled_refused": self.premium_paper_entry_disabled_refused,
            "route_policy_rejected": self.route_policy_rejected,
            "route_limit_rejected": self.route_limit_rejected,
            "errors": list(self.errors),
        }


def _parse_allowlist(raw: str) -> frozenset[str]:
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip())


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read JSONL with mid-file tolerance and reader-vs-writer retry on the
    last line. Delegates to :func:`app.storage.jsonl_io.read_jsonl_tolerant`
    since D-194 (NEO-F-META-20260424-029). The outer ``try``/``except``
    below still swallows ``OSError`` on the off-chance of a transient
    filesystem error that the utility does not cover (e.g. permission
    flipping during a deploy)."""
    from app.storage.jsonl_io import read_jsonl_tolerant

    try:
        return list(read_jsonl_tolerant(path))
    except OSError as exc:
        logger.warning("[bridge] read %s failed: %s", path, exc)
        return []


def _append_bridge_audit(record: dict[str, object]) -> None:
    _BRIDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _BRIDGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("[bridge] audit write failed: %s", exc)
        return
    try:
        from app.observability.premium_event_store import record_bridge_decision

        record_bridge_decision(record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[bridge] event-store write failed: %s", exc)


def _bridge_history_for_correlation(correlation_id: str) -> list[dict[str, object]]:
    """Prior bridge records for one signal (oldest→newest), for V-1 stabilization."""
    if not correlation_id:
        return []
    out: list[dict[str, object]] = []
    for rec in _read_jsonl(_BRIDGE_LOG):
        cid = str(rec.get("correlation_id") or "")
        if cid == correlation_id:
            out.append(rec)
    return out


def _latest_bridge_stage_by_envelope(
    records: list[dict[str, object]],
) -> dict[str, str]:
    """Return {envelope_id: latest_stage} from bridge audit records."""
    out: dict[str, str] = {}
    for rec in records:
        env_id = rec.get("envelope_id")
        stage = rec.get("stage")
        if not isinstance(env_id, str) or not isinstance(stage, str):
            continue
        out[env_id] = stage
    return out


def _collect_pending_signals(
    envelope_records: list[dict[str, object]],
    bridge_stages: dict[str, str],
) -> list[dict[str, object]]:
    """Return envelope records needing a bridge decision (no terminal stage)."""
    pending: list[dict[str, object]] = []
    for rec in envelope_records:
        stage = rec.get("stage")
        status = rec.get("status")
        msg_type = rec.get("message_type")
        env_id = rec.get("envelope_id")
        if not isinstance(env_id, str):
            continue
        if stage != "accepted" or status != "ok" or msg_type != "signal":
            continue
        current_bridge_stage = bridge_stages.get(env_id)
        if current_bridge_stage in _TERMINAL_STAGES:
            continue
        pending.append(rec)
    return pending


def _extract_source(envelope: dict[str, object]) -> str:
    """Derive a normalized source tag from an envelope record.

    Dashboard pastes emit ``source="dashboard"``. Telegram-bot handoffs
    emit ``source="structured_text"`` (parser class) or voice/natural
    language. We map both to a small, stable vocabulary so the allowlist
    stays legible.
    """
    raw = envelope.get("source")
    if not isinstance(raw, str):
        return "unknown"
    normalized = raw.strip().lower()
    if normalized == "dashboard":
        return "dashboard"
    if normalized in {"structured_text", "natural_language", "voice"}:
        return "telegram"
    return normalized or "unknown"


def _payload(envelope: dict[str, object]) -> dict[str, object]:
    payload = envelope.get("payload")
    return payload if isinstance(payload, dict) else {}


def _resolve_entry_price(payload: dict[str, object]) -> float | None:
    """Single representative entry price for limit-check.

    - entry_type=market / limit / stop_limit: use entry_value.
    - entry_type=range: use midpoint(entry_min, entry_max).
    """
    entry_type = payload.get("entry_type")
    if entry_type == "range":
        emin = _float(payload.get("entry_min"))
        emax = _float(payload.get("entry_max"))
        if emin is not None and emax is not None and emax > emin > 0:
            return (emin + emax) / 2
        return None
    return _float(payload.get("entry_value"))


def _within_tolerance(
    *,
    current_price: float,
    target_price: float,
    tolerance_pct: float,
    side: str,
) -> bool:
    """A buy fills when spot is at or (slightly) below the operator entry.
    Symmetric for sell (not supported in v1 but future-proofed)."""
    if target_price <= 0 or current_price <= 0:
        return False
    tol = target_price * (tolerance_pct / 100.0)
    if side == "buy":
        # Accept: current_price <= target + tol  (fill at or near entry)
        return current_price <= target_price + tol
    # sell (short entry)
    return current_price >= target_price - tol


def _entry_condition_met(
    *,
    payload: dict[str, object],
    current_price: float,
    target_price: float,
    tolerance_pct: float,
    side: str,
) -> bool:
    """Return True when the market price activates the signal entry rule."""
    if current_price <= 0 or target_price <= 0:
        return False
    entry_type = str(payload.get("entry_type") or "").lower()
    if entry_type == "range":
        entry_min, entry_max = _entry_bounds(payload)
        if entry_min is None or entry_max is None:
            return False
        return entry_min <= current_price <= entry_max
    if entry_type in {"above", "breakout_above"}:
        tol = target_price * (tolerance_pct / 100.0)
        return current_price >= target_price - tol
    if entry_type in {"below", "breakdown_below"}:
        tol = target_price * (tolerance_pct / 100.0)
        return current_price <= target_price + tol
    return _within_tolerance(
        current_price=current_price,
        target_price=target_price,
        tolerance_pct=tolerance_pct,
        side=side,
    )


def _ttl_exceeded(
    envelope_timestamp_utc: str | None, ttl_hours: int, now: datetime | None = None
) -> bool:
    if not envelope_timestamp_utc:
        return False
    try:
        ts = datetime.fromisoformat(envelope_timestamp_utc)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    now_utc = now or datetime.now(UTC)
    return (now_utc - ts) > timedelta(hours=ttl_hours)


def _canonical_symbol(payload: dict[str, object]) -> str:
    """Prefer display_symbol ("BTC/USDT") over bare symbol ("BTCUSDT")."""
    display = payload.get("display_symbol")
    if isinstance(display, str) and display.strip():
        return display.strip().upper()
    raw = payload.get("symbol")
    if isinstance(raw, str) and raw.strip():
        s = raw.strip().upper()
        if "/" in s:
            return s
        for quote in ("USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH"):
            if s.endswith(quote) and len(s) > len(quote):
                return f"{s[: -len(quote)]}/{quote}"
        return f"{s}/USDT"
    return ""


def _build_risk_limits() -> RiskLimits:
    s = get_settings()
    r = s.risk
    return RiskLimits(
        initial_equity=r.initial_equity,
        max_risk_per_trade_pct=r.max_risk_per_trade_pct,
        max_daily_loss_pct=r.max_daily_loss_pct,
        max_total_drawdown_pct=r.max_total_drawdown_pct,
        max_open_positions=r.max_open_positions,
        max_leverage=r.max_leverage,
        require_stop_loss=r.require_stop_loss,
        allow_averaging_down=r.allow_averaging_down,
        allow_martingale=r.allow_martingale,
        kill_switch_enabled=r.kill_switch_enabled,
        min_signal_confidence=r.min_signal_confidence,
        min_signal_confluence_count=r.min_signal_confluence_count,
        min_notional_usd=r.min_notional_usd,
        # round_trip_fee_pct is threaded so net-edge diagnostics use the real
        # (CostModel-derived) cost. min_sl_cost_multiple is intentionally NOT
        # passed here (stays 0.0/off on the bridge path — unchanged behaviour).
        round_trip_fee_pct=r.round_trip_fee_pct,
        # Sprint 2026-06-02 reward/risk gates — all default-OFF in Settings.
        min_rr=r.min_rr,
        min_avg_rr=r.min_avg_rr,
        max_signal_risk_pct=r.max_signal_risk_pct,
        max_leveraged_risk_pct=r.max_leveraged_risk_pct,
        min_net_edge_bps=r.min_net_edge_bps,
        min_target_distance_pct=r.min_target_distance_pct,
        gates_mode=r.gates_mode,
    )


def _audit_base(
    *, envelope_id: str, stage: str, source: str, envelope: dict[str, object]
) -> dict[str, object]:
    correlation_id = str(
        envelope.get("origin_envelope_id")
        or envelope.get("trace_id")
        or envelope.get("envelope_id")
        or envelope_id
    )
    idem = envelope.get("idempotency_key")
    payload = _payload(envelope)
    source_uid = envelope.get("source_uid") or payload.get("source_uid")
    source_platform = envelope.get("source_platform") or payload.get("source_platform")
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "event": "operator_signal_bridge",
        "envelope_id": envelope_id,
        "correlation_id": correlation_id,
        "stage": stage,
        "source": source,
        "origin_envelope_stage": envelope.get("stage"),
        "origin_envelope_timestamp": envelope.get("timestamp_utc"),
        **({"idempotency_key": idem} if isinstance(idem, str) and idem else {}),
        **({"source_uid": source_uid} if isinstance(source_uid, str) and source_uid else {}),
        **(
            {"source_platform": source_platform}
            if isinstance(source_platform, str) and source_platform
            else {}
        ),
    }


def _lifecycle_events(
    *,
    correlation_id: str,
    states: list[OrderLifecycleState],
    reason: str,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for from_state, to_state in zip(states, states[1:], strict=False):
        events.append(
            make_lifecycle_transition(
                correlation_id=correlation_id,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
            ).to_dict()
        )
    return events


def _attach_lifecycle(
    rec: dict[str, object],
    *,
    final_state: OrderLifecycleState,
    states: list[OrderLifecycleState],
    reason: str,
) -> None:
    correlation_id = str(rec.get("correlation_id") or rec.get("envelope_id") or "")
    rec["lifecycle_state"] = final_state.value
    rec["audit_reason"] = reason
    # Resilienz (2026-06-26): die lifecycle_events-Annotation ist Audit-Metadaten —
    # sie darf den Hochfrequenz-Entry-Watch NIEMALS crashen. Ein einzelner Satz mit
    # illegaler Sequenz warf vorher IllegalLifecycleTransition, brach den ganzen
    # Watcher-Lauf ab (exit 1) und ließ — via Restart=always + StartLimit 10/5min —
    # kai-entry-watch.service dauerhaft `failed`. Stattdessen degradieren: loggen +
    # Marker setzen (Modellierungs-Bug bleibt sichtbar), Dienst läuft weiter.
    try:
        rec["lifecycle_events"] = _lifecycle_events(
            correlation_id=correlation_id,
            states=states,
            reason=reason,
        )
    except IllegalLifecycleTransition as exc:
        logger.warning(
            "[bridge] illegale Lifecycle-Sequenz für correlation_id=%s (%s) — "
            "lifecycle_events leer + Marker, Watcher läuft weiter",
            correlation_id,
            exc,
        )
        rec["lifecycle_events"] = []
        rec["lifecycle_events_error"] = str(exc)


async def _fetch_price(symbol: str) -> float | None:
    """Resolve current spot price for ``symbol`` (e.g. 'BTC/USDT').

    V25-D (2026-05-05): default provider switched from coingecko to
    'fallback' (Bybit → CoinGecko → Mock). The bridge sees Bybit-Futures
    symbols from the premium channel (SWARMS, GIGGLE, 1000LUNC, …) that
    CoinGecko's spot aggregation does not list. Bybit V5 covers these
    natively. Operators can override via OPERATOR_SIGNAL_AUTO_RUN_PROVIDER.
    """
    settings = get_settings()
    provider = (
        settings.operator.signal_auto_run_provider
        if hasattr(settings.operator, "signal_auto_run_provider")
        else "fallback"
    )
    if not provider or provider == "coingecko":
        provider = "fallback"
    snap = await get_market_data_snapshot(symbol=symbol, provider=provider)
    if not snap.available or snap.is_stale:
        return None
    return snap.price


def _apply_scale(payload: dict[str, object], factor: float) -> None:
    """In-place rescale of entry/sl/targets by 1/factor. No-op when factor=1."""
    if factor <= 0 or factor == 1.0:
        return
    for key in ("entry_value", "entry_min", "entry_max", "stop_loss"):
        v = payload.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            payload[key] = float(v) / factor
    raw_targets = payload.get("targets")
    if isinstance(raw_targets, list):
        scaled: list[float] = []
        for t in raw_targets:
            if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
                scaled.append(float(t) / factor)
        payload["targets"] = scaled


PriceProvider = Callable[[str], Awaitable[float | None]]


async def run_tick(
    *,
    price_provider: PriceProvider | None = None,
    only_envelope_id: str | None = None,
) -> BridgeTickResult:
    """One bridge tick: scan new envelopes, re-check pending ones, fill/expire.

    ``only_envelope_id`` narrows the pending set to that single envelope (the
    "reprocess this one" click) — it can only reduce a tick's work, never widen
    it; ``None`` (the cron default) scans the full pending set unchanged.
    """
    settings = get_settings()
    if not settings.execution.operator_signal_bridge_enabled:
        return BridgeTickResult(enabled=False)

    result = BridgeTickResult(enabled=True)

    allowlist = _parse_allowlist(settings.execution.operator_signal_source_allowlist)
    ttl_hours = settings.execution.operator_signal_ttl_hours
    tolerance_pct = settings.execution.operator_signal_entry_tolerance_pct

    envelope_records = _read_jsonl(_ENVELOPE_LOG)
    bridge_records = _read_jsonl(_BRIDGE_LOG)
    bridge_stages = _latest_bridge_stage_by_envelope(bridge_records)
    pending_signals = _collect_pending_signals(envelope_records, bridge_stages)
    if only_envelope_id is not None:
        pending_signals = [
            rec for rec in pending_signals if rec.get("envelope_id") == only_envelope_id
        ]
    result.envelopes_scanned = len(pending_signals)

    if not pending_signals:
        return result

    # 2026-05-14 P1 #7: Singleton statt new+rehydrate-per-tick. rehydrate
    # bleibt zwingend — sonst sieht der FastAPI-Prozess Cron-Process-Writes
    # nicht. Spart Konstruktor-Kosten + macht initial_equity/fee/slippage
    # konsistent über alle Konsumenten (vorher hatten Reconciler + /adjust
    # hardcoded 10000.0, jetzt kommt der Wert aus settings.execution).
    engine = get_paper_engine()
    engine.rehydrate_from_audit()
    risk = RiskEngine(_build_risk_limits())

    for envelope in pending_signals:
        await _process_one(
            envelope=envelope,
            engine=engine,
            risk=risk,
            allowlist=allowlist,
            ttl_hours=ttl_hours,
            tolerance_pct=tolerance_pct,
            result=result,
            price_provider=price_provider,
        )

    return result


def _select_backfill_envelopes(
    envelope_records: list[dict[str, object]],
    *,
    symbols: frozenset[str] | None = None,
    date: str | None = None,
    envelope_ids: frozenset[str] | None = None,
) -> list[dict[str, object]]:
    """Pick premium signal envelopes for a retrospective backfill, regardless of
    any prior terminal bridge stage (unlike ``_collect_pending_signals``).

    Selection: accepted/ok signal envelopes. When ``envelope_ids`` is given it
    wins. Otherwise filter by ``symbols`` (display_symbol, upper) and/or ``date``
    (``timestamp_utc`` ISO date prefix). Per (display_symbol) the latest
    ``*_approved`` envelope wins, else the latest raw one — so a signal that was
    auto-approved is backfilled exactly once.
    """
    sym_set = {s.strip().upper() for s in symbols} if symbols else None
    candidates: list[dict[str, object]] = []
    for rec in envelope_records:
        if rec.get("message_type") != "signal" or rec.get("stage") != "accepted":
            continue
        if rec.get("status") != "ok":
            continue
        env_id = rec.get("envelope_id")
        if not isinstance(env_id, str):
            continue
        if envelope_ids is not None:
            if env_id in envelope_ids:
                candidates.append(rec)
            continue
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        disp = str(payload.get("display_symbol") or payload.get("symbol") or "").upper()
        if sym_set is not None and disp not in sym_set:
            continue
        ts = rec.get("timestamp_utc")
        if date is not None and not (isinstance(ts, str) and ts.startswith(date)):
            continue
        candidates.append(rec)

    if envelope_ids is not None:
        return candidates

    # Dedup per display_symbol: prefer the latest ``*_approved`` envelope.
    best: dict[str, dict[str, object]] = {}
    for rec in candidates:
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        disp = str(payload.get("display_symbol") or payload.get("symbol") or "").upper()
        src = str(rec.get("source") or "")
        prev = best.get(disp)
        if prev is None:
            best[disp] = rec
            continue
        prev_src = str(prev.get("source") or "")
        prev_approved = prev_src.endswith("_approved")
        cur_approved = src.endswith("_approved")
        # Prefer approved over raw; among same approval-state prefer the later ts.
        if (cur_approved and not prev_approved) or (
            cur_approved == prev_approved
            and str(rec.get("timestamp_utc") or "") >= str(prev.get("timestamp_utc") or "")
        ):
            best[disp] = rec
    return list(best.values())


async def backfill_run(
    *,
    symbols: list[str] | None = None,
    date: str | None = None,
    envelope_ids: list[str] | None = None,
    ignore_ttl: bool | None = None,
    price_provider: PriceProvider | None = None,
) -> BridgeTickResult:
    """Retrospectively (re)process selected premium envelopes through the bridge
    — even ones with a prior terminal stage (Goal 2026-06-05 §7).

    Idempotent: the paper-engine ``idempotency_key=opbridge:<envelope_id>`` plus
    the per-symbol ``position_exists`` guard prevent a second fill; a re-run that
    yields ``pending`` just rewrites the (non-terminal) pending record. ``route``
    is always paper here (this is the paper bridge); live is never reachable.
    """
    settings = get_settings()
    if not settings.execution.operator_signal_bridge_enabled:
        return BridgeTickResult(enabled=False)
    if ignore_ttl is None:
        ignore_ttl = settings.premium_fastlane.backfill_ignore_ttl_for_paper

    result = BridgeTickResult(enabled=True)
    allowlist = _parse_allowlist(settings.execution.operator_signal_source_allowlist)
    ttl_hours = settings.execution.operator_signal_ttl_hours
    tolerance_pct = settings.execution.operator_signal_entry_tolerance_pct

    envelope_records = _read_jsonl(_ENVELOPE_LOG)
    selected = _select_backfill_envelopes(
        envelope_records,
        symbols=frozenset(symbols) if symbols else None,
        date=date,
        envelope_ids=frozenset(envelope_ids) if envelope_ids else None,
    )
    result.envelopes_scanned = len(selected)
    if not selected:
        return result

    engine = get_paper_engine()
    engine.rehydrate_from_audit()
    risk = RiskEngine(_build_risk_limits())

    backfill_max_age_hours = settings.premium_fastlane.backfill_max_age_hours
    for envelope in selected:
        await _process_one(
            envelope=envelope,
            engine=engine,
            risk=risk,
            allowlist=allowlist,
            ttl_hours=ttl_hours,
            tolerance_pct=tolerance_pct,
            result=result,
            price_provider=price_provider,
            ignore_ttl=ignore_ttl,
            backfill_max_age_hours=backfill_max_age_hours,
        )

    return result


async def _process_one(
    *,
    envelope: dict[str, object],
    engine: PaperExecutionEngine,
    risk: RiskEngine,
    allowlist: frozenset[str],
    ttl_hours: int,
    tolerance_pct: float,
    result: BridgeTickResult,
    price_provider: PriceProvider | None = None,
    ignore_ttl: bool = False,
    backfill_max_age_hours: int = 0,
) -> None:
    envelope_id = str(envelope.get("envelope_id") or "")
    source = _extract_source(envelope)
    base = lambda stage: _audit_base(  # noqa: E731
        envelope_id=envelope_id, stage=stage, source=source, envelope=envelope
    )
    correlation_id = str(
        envelope.get("origin_envelope_id")
        or envelope.get("trace_id")
        or envelope.get("envelope_id")
        or envelope_id
    )

    # Premium-Fastlane decision (Goal 2026-06-05). Pure, settings-driven. When
    # routable it authorises (a) the allowlist bypass and (b) the entry_mode /
    # premium-paper bypass below — for authentic premium-telegram signals on a
    # non-live route ONLY. Every other guard (schema, scale, geometry, dup,
    # notional, qty>0) is unchanged. Live is never reachable from this paper
    # bridge, and the decision additionally carries ``live_protected``.
    fl_decision: FastlaneDecision = should_route_premium_fastlane(envelope, get_settings())
    fl_routable = fl_decision.is_routable

    # Gate 1: allowlist
    if source not in allowlist:
        if fl_routable and "source_allowlist" in fl_decision.bypassed_gates:
            rec = base("fastlane_allowlist_bypassed")
            rec["event"] = "premium_fastlane_allowlist_bypassed"
            rec["fastlane"] = fl_decision.to_dict()
            rec["allowlist"] = sorted(allowlist)
            _append_bridge_audit(rec)
            result.fastlane_bypassed_allowlist += 1
        else:
            rec = base("skipped_source")
            rec["allowlist"] = sorted(allowlist)
            _attach_lifecycle(
                rec,
                final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
                states=[
                    OrderLifecycleState.RECEIVED,
                    OrderLifecycleState.REJECTED_INVALID_SIGNAL,
                ],
                reason="source_not_allowlisted",
            )
            _append_bridge_audit(rec)
            result.skipped_source += 1
            return

    # Gate 2: TTL. For premium-fastlane PAPER backfill (Goal 2026-06-05 §8) the
    # operator may re-create a retrospective paper/pending record for a signal
    # that has aged past the live TTL — ``ignore_ttl`` skips this gate and the
    # backfill audits ``ttl_expired_but_backfill_allowed_for_paper``. Live is
    # never affected (this is the paper bridge). Normal live ticks keep TTL.
    ts_raw = envelope.get("timestamp_utc")
    ts_str = ts_raw if isinstance(ts_raw, str) else None
    if ignore_ttl and _ttl_exceeded(ts_str, ttl_hours):
        # Hard age cap: even an ignore_ttl backfill must not re-inject a signal
        # older than backfill_max_age_hours — filling at a long-stale entry price
        # would distort the canonical paper edge (A4). 0 disables the cap.
        if backfill_max_age_hours > 0 and _ttl_exceeded(ts_str, backfill_max_age_hours):
            rec = base("backfill_skipped_too_old")
            rec["event"] = "premium_fastlane_backfill_skipped_too_old"
            rec["ttl_hours"] = ttl_hours
            rec["backfill_max_age_hours"] = backfill_max_age_hours
            rec["origin_timestamp"] = ts_str
            _attach_lifecycle(
                rec,
                final_state=OrderLifecycleState.EXPIRED,
                states=[
                    OrderLifecycleState.RECEIVED,
                    OrderLifecycleState.PARSED,
                    OrderLifecycleState.VALIDATED,
                    OrderLifecycleState.EXPIRED,
                ],
                reason="backfill_age_exceeds_max",
            )
            _append_bridge_audit(rec)
            result.expired += 1
            return
        ttl_note = base("fastlane_ttl_backfill_allowed")
        ttl_note["event"] = "premium_fastlane_ttl_expired_but_backfill_allowed_for_paper"
        ttl_note["ttl_hours"] = ttl_hours
        ttl_note["origin_timestamp"] = ts_str
        _append_bridge_audit(ttl_note)
    elif _ttl_exceeded(ts_str, ttl_hours):
        rec = base("expired")
        rec["ttl_hours"] = ttl_hours
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.EXPIRED,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.EXPIRED,
            ],
            reason="ttl_exceeded",
        )
        _append_bridge_audit(rec)
        result.expired += 1
        return

    payload = _payload(envelope)

    # Gate 3: completeness (entry / SL / TP / direction)
    direction = payload.get("direction")
    side_str = payload.get("side")
    symbol = _canonical_symbol(payload)
    entry_price = _resolve_entry_price(payload)
    stop_loss = _float(payload.get("stop_loss"))
    targets_raw = payload.get("targets")
    # V25-C (2026-05-04): preserve all targets for staged-exit ladder.
    # Pre-V25-C only targets[0] (TP1) was used; the remaining targets were
    # silently dropped, so a 4-target signal could only ever realise 1/4
    # of the channel-intended take-profit progression.
    targets = sorted(
        (
            float(t)
            for t in (targets_raw or [])
            if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0
        ),
        reverse=direction == "short",
    )
    tp1 = targets[0] if targets else None

    missing: list[str] = []
    if not symbol:
        missing.append("symbol")
    if entry_price is None or entry_price <= 0:
        missing.append("entry_price")
    if stop_loss is None or stop_loss <= 0:
        missing.append("stop_loss")
    if tp1 is None:
        missing.append("targets")
    if direction not in {"long", "short"}:
        missing.append("direction")
    if side_str not in {"buy", "sell"}:
        missing.append("side")

    if missing:
        rec = base("rejected_incomplete")
        rec["missing"] = missing
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            ],
            reason=f"missing_required_fields:{','.join(missing)}",
        )
        _append_bridge_audit(rec)
        result.rejected_incomplete += 1
        return

    # Re-narrow types for the type checker:
    assert entry_price is not None and stop_loss is not None and tp1 is not None

    # Gate 3.5: no merging. If a paper position already exists for this symbol
    # we refuse the fill — otherwise an averaged-down merge can leave the
    # combined position with a geometrically invalid SL/TP. Conservative by
    # design; operator can close the existing position first if they want the
    # new signal to take over.
    if symbol in engine.portfolio.positions:
        rec = base("rejected_position_exists")
        rec["existing_quantity"] = engine.portfolio.positions[symbol].quantity
        rec["executable_intent"] = _build_executable_intent(
            envelope_id=envelope_id,
            correlation_id=str(rec["correlation_id"]),
            source=source,
            payload=payload,
            symbol=symbol,
            side=str(side_str),
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
        ).to_dict()
        rec["order_intent"] = rec["executable_intent"]
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            ],
            reason="position_already_open",
        )
        _append_bridge_audit(rec)
        result.rejected_position_exists += 1
        return

    # Gate 4: market data / entry-band
    current_price = await price_provider(symbol) if price_provider is not None else None
    if current_price is None:
        current_price = await _fetch_price(symbol)
    if current_price is None:
        rec = base("pending")
        rec["reason"] = "no_market_data"
        rec["target_entry"] = entry_price
        rec["executable_intent"] = _build_executable_intent(
            envelope_id=envelope_id,
            correlation_id=str(rec["correlation_id"]),
            source=source,
            payload=payload,
            symbol=symbol,
            side=str(side_str),
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
        ).to_dict()
        rec["order_intent"] = rec["executable_intent"]
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.WAITING_FOR_ENTRY,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.WAITING_FOR_ENTRY,
            ],
            reason="market_data_unavailable",
        )
        _append_bridge_audit(rec)
        result.no_market_data += 1
        return

    # V25-D (2026-05-05): rescale Bybit-Futures integer-tick entries to USD
    # before the tolerance check. Channel posts e.g. SWARMS 32450 (×10^6) /
    # 1000LUNC 10310 (×10^5) which would otherwise blow past every gate.
    #
    # 2026-05-14 (P1 #8): if the worker already resolved the scale at receive
    # time (``scale_resolved_at_emit=True``), the entry/sl/targets values are
    # already in USD and we skip the bridge-side re-detection. Legacy
    # envelopes (no marker) and ``scale_unknown=True`` envelopes still flow
    # through the legacy path so a market_data outage at receive doesn't
    # strand the signal.
    if bool(payload.get("scale_resolved_at_emit")):
        scale_factor = 1.0
    else:
        scale_factor = _detect_scale_factor(entry_price, current_price)
    if scale_factor != 1.0:
        logger.info(
            "[bridge] scale-detect symbol=%s entry=%.6g current=%.6g "
            "factor=%.0e → rescaling entry/sl/targets",
            symbol,
            entry_price,
            current_price,
            scale_factor,
        )
        entry_price = entry_price / scale_factor
        stop_loss = stop_loss / scale_factor
        tp1 = tp1 / scale_factor
        targets = [t / scale_factor for t in targets]
        # Persist the corrected scale on the envelope payload so downstream
        # consumers (engine, tier ladder, audit) see the same numbers we
        # just gated on.
        scaled_payload = envelope.get("payload")
        _apply_scale(scaled_payload if isinstance(scaled_payload, dict) else {}, scale_factor)
        # BUG-3 (2026-06-08): persist the RESOLVED scale lifecycle onto the
        # envelope payload so the UI/analytics show the real entry (0.248), not
        # the raw channel value (24800), and the stale receive-time
        # scale_unknown=True flag is cleared. Emit a one-shot audit marker.
        from app.execution.premium_scale_lifecycle import build_scale_resolution_patch
        from app.observability.premium_audit import (
            EVENT_SCALE_RESOLVED_PERSISTED,
            append_premium_audit,
        )

        _scale_patch = build_scale_resolution_patch(
            scale_factor=scale_factor,
            scaled_entry=entry_price,
            scaled_stop_loss=stop_loss,
            scaled_targets=list(targets),
        )
        if _scale_patch and isinstance(scaled_payload, dict):
            was_unknown = bool(scaled_payload.get("scale_unknown"))
            scaled_payload.update(_scale_patch)
            if isinstance(payload, dict):
                payload.update(_scale_patch)
            if was_unknown:
                append_premium_audit(
                    EVENT_SCALE_RESOLVED_PERSISTED,
                    envelope_id=envelope_id,
                    correlation_id=correlation_id,
                    symbol=symbol,
                    scale_factor=scale_factor,
                    scaled_entry=entry_price,
                    scaled_stop_loss=stop_loss,
                    scaled_targets=list(targets),
                )

    # Gate 4.5 (2026-05-21): plausibility-check der skalierten Werte gegen spot.
    # Adressiert IRYS 2026-05-12: SL nach Skalierung lag über spot, paper-engine
    # rejected mit ``long_sl_at_or_above_price`` und Bridge schrieb nur opaken
    # ``paper_engine_returned_none``. validate_scaled_signal liefert jetzt einen
    # aussagekräftigen Reason der direkt in den Trail-Audit landet.
    from app.execution.scale_resolver import (
        SCALE_UNRESOLVED_REASON,
        classify_scale_failure,
        is_structural_scale_reason,
        is_tick_flaky_reason,
        validate_scaled_signal,
    )

    # BUG-1 (2026-06-08): an unresolved scale / bad price (raw entry implausibly
    # far from spot while scale stayed 1.0) is a STRUCTURAL data error, not a
    # market condition. It takes precedence over validate_scaled_signal so we
    # never emit a misleading ``long_sl_at_or_above_spot`` for the SKYAI case.
    bad_price_reason = classify_scale_failure(
        entry=entry_price, spot=current_price, scale_factor_applied=scale_factor
    )
    validation_reason = bad_price_reason or validate_scaled_signal(
        direction=str(direction or ""),
        entry=entry_price,
        stop_loss=stop_loss,
        targets=list(targets),
        spot=current_price,
    )
    # Premium-Fastlane non-fatal scale-hint (Goal 2026-06-05 §10). A market-
    # plausibility reason (SL at/below current spot, entry far from spot) is a
    # *market* condition, not a scale-detection bug: for fastlane PAPER it must
    # NOT terminally reject (forbidden ``fatal_requires_scale_review``). Instead
    # the signal is kept as a PENDING entry and re-evaluated every tick — a
    # not-yet-triggered breakout whose SL is currently below spot fills once the
    # market actually breaks above the entry. Structural reasons (scale collapse,
    # SL on the wrong side of entry, targets on the wrong side) stay terminal for
    # everyone — they are genuine geometry errors. Live is unaffected.
    if (
        validation_reason is not None
        and fl_routable
        and not is_structural_scale_reason(validation_reason)
    ):
        rec = base("pending")
        rec["reason"] = "price_outside_tolerance"
        rec["scale_hint"] = validation_reason
        rec["event"] = "premium_fastlane_scale_hint_nonfatal"
        rec["scale_factor_applied"] = scale_factor
        rec["scaled_entry"] = entry_price
        rec["scaled_stop_loss"] = stop_loss
        rec["scaled_targets"] = list(targets)
        rec["current_price"] = current_price
        rec["target_entry"] = entry_price
        rec["executable_intent"] = _build_executable_intent(
            envelope_id=envelope_id,
            correlation_id=str(rec["correlation_id"]),
            source=source,
            payload=payload,
            symbol=symbol,
            side=str(side_str),
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
        ).to_dict()
        rec["order_intent"] = rec["executable_intent"]
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.WAITING_FOR_ENTRY,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.WAITING_FOR_ENTRY,
            ],
            reason=f"fastlane_scale_hint:{validation_reason}",
        )
        _append_bridge_audit(rec)
        result.newly_pending += 1
        return
    if validation_reason is not None:
        # V-1 (2026-06-08): a tick-flaky reason (spot-dependent / bad-price) must
        # not terminally reject a signal that was previously a healthy pending
        # entry on the strength of a single garbage tick. Ignore it and keep the
        # signal pending until N consecutive bad ticks accumulate; only then
        # terminate (premium_terminal_stabilized). Pure internal-geometry reasons
        # are not tick-flaky and stay immediately terminal.
        if is_tick_flaky_reason(validation_reason):
            from app.execution.premium_scale_lifecycle import (
                PENDING_BAD_TICK_STAGE,
                analyze_bridge_history,
                decide_terminal_or_ignore,
            )
            from app.observability.premium_audit import (
                EVENT_BAD_TICK_IGNORED,
                EVENT_SCALE_UNRESOLVED,
                EVENT_TERMINAL_STABILIZED,
                append_premium_audit,
            )

            _prior_bad, _had_valid = analyze_bridge_history(
                _bridge_history_for_correlation(correlation_id)
            )
            _decision = decide_terminal_or_ignore(
                prior_consecutive_bad=_prior_bad, had_prior_valid_pending=_had_valid
            )
            if validation_reason == SCALE_UNRESOLVED_REASON:
                append_premium_audit(
                    EVENT_SCALE_UNRESOLVED,
                    envelope_id=envelope_id,
                    correlation_id=correlation_id,
                    symbol=symbol,
                    entry=entry_price,
                    spot=current_price,
                    scale_factor_applied=scale_factor,
                )
            if _decision.action == "ignore":
                rec = base(PENDING_BAD_TICK_STAGE)
                rec["reason"] = validation_reason
                rec["scale_factor_applied"] = scale_factor
                rec["current_price"] = current_price
                rec["consecutive_bad_ticks"] = _decision.consecutive_bad
                rec["target_entry"] = entry_price
                rec["executable_intent"] = _build_executable_intent(
                    envelope_id=envelope_id,
                    correlation_id=str(rec["correlation_id"]),
                    source=source,
                    payload=payload,
                    symbol=symbol,
                    side=str(side_str),
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    targets=targets,
                ).to_dict()
                rec["order_intent"] = rec["executable_intent"]
                _attach_lifecycle(
                    rec,
                    final_state=OrderLifecycleState.WAITING_FOR_ENTRY,
                    states=[
                        OrderLifecycleState.RECEIVED,
                        OrderLifecycleState.PARSED,
                        OrderLifecycleState.VALIDATED,
                        OrderLifecycleState.WAITING_FOR_ENTRY,
                    ],
                    reason=f"bad_tick_ignored:{validation_reason}",
                )
                _append_bridge_audit(rec)
                append_premium_audit(
                    EVENT_BAD_TICK_IGNORED,
                    envelope_id=envelope_id,
                    correlation_id=correlation_id,
                    symbol=symbol,
                    reason=validation_reason,
                    consecutive_bad=_decision.consecutive_bad,
                )
                result.newly_pending += 1
                return
            append_premium_audit(
                EVENT_TERMINAL_STABILIZED,
                envelope_id=envelope_id,
                correlation_id=correlation_id,
                symbol=symbol,
                reason=validation_reason,
                consecutive_bad=_decision.consecutive_bad,
            )
        rec = base("rejected_scale_review")
        rec["reason"] = validation_reason
        rec["scale_factor_applied"] = scale_factor
        rec["scaled_entry"] = entry_price
        rec["scaled_stop_loss"] = stop_loss
        rec["scaled_targets"] = list(targets)
        rec["current_price"] = current_price
        rec["executable_intent"] = _build_executable_intent(
            envelope_id=envelope_id,
            correlation_id=str(rec["correlation_id"]),
            source=source,
            payload=payload,
            symbol=symbol,
            side=str(side_str),
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
        ).to_dict()
        rec["order_intent"] = rec["executable_intent"]
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            ],
            reason=validation_reason,
        )
        _append_bridge_audit(rec)
        result.rejected_size += 1
        return

    if not _entry_condition_met(
        payload=payload,
        current_price=current_price,
        target_price=entry_price,
        tolerance_pct=tolerance_pct,
        side=side_str,
    ):
        rec = base("pending")
        rec["reason"] = "price_outside_tolerance"
        rec["current_price"] = current_price
        rec["target_entry"] = entry_price
        rec["entry_min"] = payload.get("entry_min")
        rec["entry_max"] = payload.get("entry_max")
        rec["tolerance_pct"] = tolerance_pct
        # BUG-3 (trail/API): when a real scale was resolved this tick, carry the
        # resolved geometry on the pending record so the trail shows the scaled
        # plan (0.248), not the raw channel value (24800). Only attach when a
        # factor was actually applied so a later factor=1.0 tick can't overwrite
        # the good resolved values.
        if scale_factor != 1.0:
            rec["scale_factor_applied"] = scale_factor
            rec["scaled_entry"] = entry_price
            rec["scaled_stop_loss"] = stop_loss
            rec["scaled_targets"] = list(targets)
            rec["scale_unknown"] = False
        rec["executable_intent"] = _build_executable_intent(
            envelope_id=envelope_id,
            correlation_id=str(rec["correlation_id"]),
            source=source,
            payload=payload,
            symbol=symbol,
            side=str(side_str),
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
        ).to_dict()
        rec["order_intent"] = rec["executable_intent"]
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.WAITING_FOR_ENTRY,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.WAITING_FOR_ENTRY,
            ],
            reason="entry_not_reached",
        )
        _append_bridge_audit(rec)
        existed_before = any(
            r.get("envelope_id") == envelope_id and r.get("stage") == "pending"
            for r in _read_jsonl(_BRIDGE_LOG)[:-1]
        )
        if existed_before:
            result.re_pending += 1
        else:
            result.newly_pending += 1
        return

    # Gate 5: Risk Engine
    current_open = len(engine.portfolio.positions)
    leverage_for_risk = _float(payload.get("leverage"))
    risk_result = risk.check_order(
        symbol=symbol,
        side=side_str,
        signal_confidence=1.0,
        signal_confluence_count=99,
        stop_loss_price=stop_loss,
        current_open_positions=current_open,
        entry_price=entry_price,
        take_profit_price=tp1,
        take_profit_targets=list(targets) if targets else None,
        leverage=leverage_for_risk,
    )
    # Staged-rollout audit: in audit OR enforce mode, persist a reward/risk-gate
    # evaluation when it flags the signal — even when the order is otherwise
    # approved (audit mode). Lets the operator measure reject-rate before
    # flipping RISK_GATES_MODE to enforce. Fail-soft; never blocks the bridge.
    try:
        from app.observability.risk_gate_audit import record_risk_gate_eval

        record_risk_gate_eval(
            risk_result=risk_result,
            envelope_id=envelope_id,
            correlation_id=str(
                _audit_base(
                    envelope_id=envelope_id,
                    stage="risk_gate_eval",
                    source=source,
                    envelope=envelope,
                ).get("correlation_id")
            ),
            source=source,
            symbol=symbol,
            enforced=str(risk_result.details.get("gates_mode")) == "enforce",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[bridge] risk_gate_audit record failed: %s", exc)

    # Safety-contract kill-switch (2026-06-02): EXECUTION_ENTRY_MODE is a GLOBAL
    # gate on risk-increasing entries. The autonomous loop honours it in
    # trading_loop; the premium/promoted bridge MUST honour it too — otherwise
    # ``disabled`` is only a partial kill-switch (autonomous blocked, premium
    # through). Diagnostics (risk-gate audit above) are computed FIRST on
    # purpose: under ``disabled`` we still emit full would_reject/geometry
    # evidence — we report, then refuse to act. Exits/risk-reductions never reach
    # here (this path opens NEW exposure only).
    entry_mode = get_settings().execution.entry_mode
    is_premium = isinstance(source, str) and source.startswith("telegram_premium")
    premium_paper_enabled = get_settings().premium.paper_execution_enabled
    classic_entry_blocks = not entry_mode.allows_risk_increasing_entry or (
        is_premium and not premium_paper_enabled
    )
    # Premium-Fastlane entry-mode bypass (Goal 2026-06-05 §9). When the fastlane
    # is routable for this authentic premium signal AND bypass_entry_mode_for_paper
    # is set, the global entry_mode=disabled / premium_paper_execution_disabled
    # block is downgraded to an OBSERVED note for a non-live route — the signal
    # proceeds to the (paper) fill. The classic kill-switch semantics are
    # untouched for every non-fastlane source. Live is unaffected: this bridge
    # never submits a live order, and fl_decision.live_protected is recorded.
    fl_wants_entry_mode_bypass = (
        classic_entry_blocks
        and fl_routable
        and "entry_mode_for_paper" in fl_decision.bypassed_gates
    )
    # Issue #181 §7 preflight: the bypass is honoured ONLY when the two-flag
    # override is fully armed (bypass_entry_mode_for_paper +
    # allow_entry_mode_disabled_override). Otherwise the global kill-switch holds
    # and the would-be bypass is recorded as a fail-closed refusal — enabling the
    # fastlane (or a single flag) can no longer neuter entry_mode=disabled.
    fl_override_allowed, fl_override_refusal = fastlane_entry_mode_override(get_settings())
    if fl_wants_entry_mode_bypass and fl_override_allowed:
        bypass_rec = base("fastlane_entry_mode_bypassed_for_paper")
        bypass_rec["event"] = "premium_fastlane_entry_mode_bypassed_for_paper"
        bypass_rec["entry_mode"] = entry_mode.value
        bypass_rec["premium_paper_execution_enabled"] = premium_paper_enabled
        bypass_rec["classic_block_reason"] = (
            "premium_paper_execution_disabled"
            if (is_premium and not premium_paper_enabled)
            else "entry_mode_disabled"
        )
        bypass_rec["fastlane"] = fl_decision.to_dict()
        bypass_rec["route"] = fl_decision.route
        bypass_rec["live_protected"] = fl_decision.live_protected
        _append_bridge_audit(bypass_rec)
        result.fastlane_bypassed_entry_mode += 1
        classic_entry_blocks = False
    elif fl_wants_entry_mode_bypass and not fl_override_allowed:
        # Bypass requested but override not armed → fail-closed. Surface the
        # refusal so the dashboard shows the kill-switch held; the signal then
        # falls through to the normal rejected_entry_mode terminal below.
        refusal_rec = base("fastlane_entry_mode_override_refused")
        refusal_rec["event"] = "premium_fastlane_entry_mode_override_refused"
        refusal_rec["reason"] = fl_override_refusal
        refusal_rec["reason_codes"] = [
            ExecutionBlockerCode.FASTLANE_ENTRY_MODE_OVERRIDE_NOT_ARMED.value
        ]
        refusal_rec["entry_mode"] = entry_mode.value
        refusal_rec["fastlane"] = fl_decision.to_dict()
        refusal_rec["live_protected"] = fl_decision.live_protected
        _append_bridge_audit(refusal_rec)
        result.fastlane_entry_mode_override_refused += 1
    # Pfad-3 decoupling (2026-06-10): allow a CLASSIC (non-fastlane) premium PAPER
    # signal to open while the GLOBAL entry_mode kill-switch is disabled — WITHOUT
    # touching the autonomous loop (it honours entry_mode in trading_loop and
    # stays killed) and WITHOUT re-enabling the Fastlane (operator-decision: OFF).
    # Only fires when the block is PURELY the entry_mode kill-switch (premium
    # paper is enabled, so the premium-paper-disabled term is already False) and
    # the fastlane bypass did not already clear it. Fail-closed two-arm override
    # (premium.allow_paper_while_entry_disabled + entry_disabled_override_ack);
    # a single flag can never neuter the kill-switch. Live is never reachable.
    premium_wants_entry_disabled_bypass = (
        classic_entry_blocks
        and is_premium
        and premium_paper_enabled
        and not entry_mode.allows_risk_increasing_entry
        and not fl_wants_entry_mode_bypass
    )
    if premium_wants_entry_disabled_bypass:
        prem_override_allowed, prem_override_refusal = premium_paper_entry_disabled_override(
            get_settings()
        )
        if prem_override_allowed:
            bypass_rec = base("premium_paper_entry_disabled_bypassed")
            bypass_rec["event"] = "premium_paper_entry_disabled_bypassed_for_paper"
            bypass_rec["entry_mode"] = entry_mode.value
            bypass_rec["premium_paper_execution_enabled"] = premium_paper_enabled
            bypass_rec["classic_block_reason"] = "entry_mode_disabled"
            bypass_rec["route"] = "paper"
            bypass_rec["autonomous_loop_protected"] = True
            _append_bridge_audit(bypass_rec)
            result.premium_paper_entry_disabled_bypassed += 1
            classic_entry_blocks = False
        else:
            refusal_rec = base("premium_paper_entry_disabled_override_refused")
            refusal_rec["event"] = "premium_paper_entry_disabled_override_refused"
            refusal_rec["reason"] = prem_override_refusal
            refusal_rec["reason_codes"] = [ExecutionBlockerCode.ENTRY_MODE_DISABLED.value]
            refusal_rec["entry_mode"] = entry_mode.value
            _append_bridge_audit(refusal_rec)
            result.premium_paper_entry_disabled_refused += 1
    # Sprint S3 (#181): explicit limited paper modes. The per-route entry
    # policy is AUTHORITATIVE here — it runs AFTER the legacy fastlane/Pfad-3
    # branches so no legacy bypass can re-open a route the policy refuses
    # (e.g. a contradictory fastlane arming under paper_premium_limited).
    # Legacy modes (disabled/paper/probe/live_*) never enter this block, so
    # their behaviour — including the three-arm migration aliases — is
    # byte-identical (Pi-neutral migration).
    policy_block_reason: str | None = None
    policy_block_codes: list[str] | None = None
    if entry_mode in (EntryMode.PAPER_PREMIUM_LIMITED, EntryMode.PAPER_LEARNING):
        entry_policy = resolve_entry_policy(get_settings())
        if not is_premium:
            # TV paper route (2026-06-22): tradingview_webhook alerts open a
            # dedicated, flag-armed PAPER route (isolated cohort, never live).
            if source == "tradingview_webhook":
                tv_verdict = entry_policy.verdict(EntryRoute.TRADINGVIEW_PAPER)
                if not tv_verdict.allowed:
                    classic_entry_blocks = True
                    policy_block_reason = tv_verdict.reason_code or "route_refused_by_entry_policy"
                    policy_block_codes = [ExecutionBlockerCode.ROUTE_NOT_OPEN_IN_MODE.value]
                    result.route_policy_rejected += 1
                else:
                    tv_ok, tv_detail, tv_snapshot = check_route_limits(
                        route=EntryRoute.TRADINGVIEW_PAPER,
                        limits=tv_verdict.limits,
                        audit_path=engine.audit_path,
                        current_open_positions=current_open,
                    )
                    if tv_ok:
                        classic_entry_blocks = False
                    else:
                        classic_entry_blocks = True
                        policy_block_reason = f"route_limit_exceeded:{tv_detail}"
                        policy_block_codes = [ExecutionBlockerCode.ROUTE_LIMIT_EXCEEDED.value]
                        tv_limit_rec = base("rejected_route_limit")
                        tv_limit_rec["event"] = "entry_policy_route_limit_rejected"
                        tv_limit_rec["entry_mode"] = entry_mode.value
                        tv_limit_rec["reason"] = policy_block_reason
                        tv_limit_rec["reason_codes"] = list(policy_block_codes)
                        tv_limit_rec["route_limits"] = tv_snapshot
                        tv_limit_rec["entry_policy"] = entry_policy.to_dict()
                        _append_bridge_audit(tv_limit_rec)
                        result.route_limit_rejected += 1
            else:
                classic_entry_blocks = True
                policy_block_reason = "route_not_open_in_mode"
                policy_block_codes = [ExecutionBlockerCode.ROUTE_NOT_OPEN_IN_MODE.value]
                result.route_policy_rejected += 1
        else:
            verdict = entry_policy.verdict(EntryRoute.PREMIUM_PAPER)
            if not verdict.allowed:
                classic_entry_blocks = True
                policy_block_reason = verdict.reason_code or "route_refused_by_entry_policy"
                policy_block_codes = [
                    ExecutionBlockerCode.ENTRY_POLICY_CONTRADICTION.value
                    if entry_policy.contradictions
                    else ExecutionBlockerCode.ROUTE_NOT_OPEN_IN_MODE.value
                ]
                result.route_policy_rejected += 1
            else:
                limits_ok, limit_detail, limits_snapshot = check_route_limits(
                    route=EntryRoute.PREMIUM_PAPER,
                    limits=verdict.limits,
                    audit_path=engine.audit_path,
                    current_open_positions=current_open,
                )
                if limits_ok:
                    # Route explicitly open in this mode (no ack required —
                    # the mode itself is the operator statement, #181 §8).
                    classic_entry_blocks = False
                else:
                    classic_entry_blocks = True
                    policy_block_reason = f"route_limit_exceeded:{limit_detail}"
                    policy_block_codes = [ExecutionBlockerCode.ROUTE_LIMIT_EXCEEDED.value]
                    limit_rec = base("rejected_route_limit")
                    limit_rec["event"] = "entry_policy_route_limit_rejected"
                    limit_rec["entry_mode"] = entry_mode.value
                    limit_rec["reason"] = policy_block_reason
                    limit_rec["reason_codes"] = list(policy_block_codes)
                    limit_rec["route_limits"] = limits_snapshot
                    limit_rec["entry_policy"] = entry_policy.to_dict()
                    _append_bridge_audit(limit_rec)
                    result.route_limit_rejected += 1
    if classic_entry_blocks:
        rec = base("rejected_entry_mode")
        rec["reason"] = policy_block_reason or (
            "premium_paper_execution_disabled"
            if (is_premium and not premium_paper_enabled)
            else "entry_mode_disabled"
        )
        rec["reason_codes"] = policy_block_codes or [ExecutionBlockerCode.ENTRY_MODE_DISABLED.value]
        rec["entry_mode"] = entry_mode.value
        rec["risk_gates_mode"] = risk_result.details.get("gates_mode")
        rec["risk_gate_would_reject"] = risk_result.would_reject
        rec["signal_geometry"] = risk_result.details.get("signal_geometry")
        rec["open_count"] = current_open
        rec["max_open_positions"] = risk.limits.max_open_positions
        rec["executable_intent"] = _build_executable_intent(
            envelope_id=envelope_id,
            correlation_id=str(rec["correlation_id"]),
            source=source,
            payload=payload,
            symbol=symbol,
            side=str(side_str),
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
        ).to_dict()
        rec["order_intent"] = rec["executable_intent"]
        # Lifecycle terminal is the coarse audit layer; the precise disposition is
        # carried by stage="rejected_entry_mode" + reason_codes=[ENTRY_MODE_DISABLED].
        # Block is post-validation / pre-entry-trigger: the signal is well-formed,
        # the global kill-switch refuses to act. VALIDATED -> REJECTED is the legal
        # (and honest) terminal — we never reach order-building.
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            ],
            reason="entry_mode_disabled",
        )
        _append_bridge_audit(rec)
        result.rejected_entry_mode += 1
        return

    # Gate 5b: risk-engine reward/risk QUALITY gates. Under the premium-fastlane
    # (Goal §5/§10) these are OBSERVE-ONLY: they are recorded but never block, so
    # real forward-data is generated. The fastlane still enforces its OWN hard
    # caps below (max_open_positions; per-symbol is the Gate-3.5 position_exists
    # guard). Classic (non-fastlane) sources keep the original hard reject.
    fl_cfg = get_settings().premium_fastlane
    if fl_routable and current_open >= fl_cfg.max_open_positions:
        rec = base("rejected_risk")
        rec["reason"] = "fastlane_max_open_positions"
        rec["open_count"] = current_open
        rec["max_open_positions"] = fl_cfg.max_open_positions
        rec["open_symbols"] = sorted(engine.portfolio.positions.keys())
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            ],
            reason="fastlane_max_open_positions",
        )
        _append_bridge_audit(rec)
        result.rejected_risk += 1
        return
    if not risk_result.approved and not fl_routable:
        rec = base("rejected_risk")
        rec["risk_check_id"] = risk_result.check_id
        rec["violations"] = list(risk_result.violations)
        rec["reason_codes"] = list(risk_result.reason_codes)
        rec["signal_geometry"] = risk_result.details.get("signal_geometry")
        rec["open_count"] = current_open
        rec["max_open_positions"] = risk.limits.max_open_positions
        rec["open_symbols"] = sorted(engine.portfolio.positions.keys())
        rec["executable_intent"] = _build_executable_intent(
            envelope_id=envelope_id,
            correlation_id=str(rec["correlation_id"]),
            source=source,
            payload=payload,
            symbol=symbol,
            side=str(side_str),
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
        ).to_dict()
        rec["order_intent"] = rec["executable_intent"]
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.ENTRY_TRIGGERED,
                OrderLifecycleState.ORDER_BUILDING,
                OrderLifecycleState.ORDER_SUBMITTED,
                OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            ],
            reason="risk_gate_rejected",
        )
        _append_bridge_audit(rec)
        result.rejected_risk += 1
        return
    if not risk_result.approved and fl_routable:
        obs = base("fastlane_risk_observed")
        obs["event"] = "premium_fastlane_risk_observed"
        obs["violations"] = list(risk_result.violations)
        obs["reason_codes"] = list(risk_result.reason_codes)
        obs["metric_role"] = "observe_only"
        _append_bridge_audit(obs)

    # Gate 6: Position sizing. Classic path uses the risk-engine sizing. The
    # fastlane path uses notional-based sizing (Goal §12): a fixed test notional
    # (clamped to [min,max]) → quantity = notional/entry, with leverage clamped
    # to the fastlane cap. This keeps fastlane stakes small, uniform and capital
    # -separated from the autonomous loop's risk-per-trade sizing.
    equity = engine.portfolio.cash
    leverage_val = _float(payload.get("leverage"))
    risk_allocation_pct = _float(payload.get("margin_pct"))
    if risk_allocation_pct is None:
        risk_allocation_pct = _float(payload.get("position_size_suggestion"))

    if fl_routable:
        fl_leverage, lev_note = resolve_leverage(leverage_val, fl_cfg)
        notional, fl_qty, notional_reject = resolve_notional(entry_price, fl_cfg)
        if notional_reject is not None or fl_qty <= 0:
            rec = base("rejected_size")
            rec["reason"] = notional_reject or "fastlane_quantity_non_positive"
            rec["fastlane_notional_usdt"] = notional
            rec["signal_leverage"] = leverage_val
            rec["fastlane_leverage"] = fl_leverage
            _attach_lifecycle(
                rec,
                final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
                states=[
                    OrderLifecycleState.RECEIVED,
                    OrderLifecycleState.PARSED,
                    OrderLifecycleState.VALIDATED,
                    OrderLifecycleState.REJECTED_INVALID_SIGNAL,
                ],
                reason=notional_reject or "fastlane_quantity_non_positive",
            )
            _append_bridge_audit(rec)
            result.rejected_size += 1
            return
        engine_size = risk.calculate_position_size(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            equity=equity,
            leverage=fl_leverage,
            risk_allocation_pct=risk_allocation_pct,
        )
        # Fastlane overrides the engine sizing with the fixed notional stake
        # (PositionSizeResult is frozen → rebuild via replace). approved=True
        # because the fastlane notional guard already validated min/max + qty>0.
        size_result = replace(
            engine_size,
            approved=True,
            position_size_units=fl_qty,
            position_size_pct=(notional / equity * 100.0) if equity > 0 else 0.0,
            rationale=f"fastlane_notional_usdt={notional:.2f}",
        )
        leverage_val = fl_leverage
        if lev_note:
            note_rec = base("fastlane_leverage_policy")
            note_rec["event"] = "premium_fastlane_leverage_policy"
            note_rec["note"] = lev_note
            note_rec["signal_leverage"] = _float(payload.get("leverage"))
            note_rec["fastlane_leverage"] = fl_leverage
            _append_bridge_audit(note_rec)
        result.fastlane_routed += 1
    else:
        # A-Fix 2026-06-13: premium signals execute 1:1 with stated leverage so
        # paper PnL reflects the real leveraged result (intake quality). Flag-
        # gated + premium-only; liquidation in monitor_positions bounds the loss.
        apply_lev = is_premium and get_settings().premium.apply_signal_leverage
        size_result = risk.calculate_position_size(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            equity=equity,
            leverage=leverage_val,
            risk_allocation_pct=risk_allocation_pct,
            apply_signal_leverage=apply_lev,
        )
    if not size_result.approved or size_result.position_size_units <= 0:
        rec = base("rejected_size")
        rec["rationale"] = size_result.rationale
        rec["signal_margin_pct"] = risk_allocation_pct
        rec["signal_leverage"] = leverage_val
        rec["position_size_pct"] = size_result.position_size_pct
        rec["max_loss_usd"] = size_result.max_loss_usd
        rec["max_loss_pct"] = size_result.max_loss_pct
        rec["executable_intent"] = _build_executable_intent(
            envelope_id=envelope_id,
            correlation_id=str(rec["correlation_id"]),
            source=source,
            payload=payload,
            symbol=symbol,
            side=str(side_str),
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
        ).to_dict()
        rec["order_intent"] = rec["executable_intent"]
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.ENTRY_TRIGGERED,
                OrderLifecycleState.ORDER_BUILDING,
                OrderLifecycleState.ORDER_SUBMITTED,
                OrderLifecycleState.REJECTED_INVALID_SIGNAL,
            ],
            reason="position_sizing_rejected",
        )
        _append_bridge_audit(rec)
        result.rejected_size += 1
        return

    # Create + fill
    executable_intent = _build_executable_intent(
        envelope_id=envelope_id,
        correlation_id=correlation_id,
        source=source,
        payload=payload,
        symbol=symbol,
        side=str(side_str),
        entry_price=entry_price,
        stop_loss=stop_loss,
        targets=targets,
        quantity=size_result.position_size_units,
    )
    # 2026-06-10 PnL-truth: fill premium paper at the signal's stated entry
    # price (LIMIT/STOP semantics), not the current spot. The entry-tolerance
    # gate above only proves spot is *near* entry; for a breakout-above signal
    # processed late, spot can already sit above the targets, so a fill-at-spot
    # opens the position above its own take-profits and a target-touch close
    # books a loss even though the channel reports "all targets hit". Filling at
    # the resolved entry makes the realised PnL match the signal's plan. Paper-
    # and premium-only; the observed spot is still recorded for honesty.
    fill_at_entry = is_premium and get_settings().premium.fill_at_signal_entry
    fill_price_override = entry_price if fill_at_entry else None
    try:
        order, fill = engine.execute_intent(
            intent=executable_intent,
            current_price=current_price,
            risk_check_id=risk_result.check_id,
            fill_price=fill_price_override,
        )
    except DuplicateOrderError as exc:
        # Sprint C (2026-05-12): cross-process Race-Guard hat eine zweite
        # Fill-Attempte für denselben envelope blockiert. KEIN rejected_fill —
        # das wäre falsch (der erste Fill war erfolgreich) — stattdessen
        # markieren wir den Tick als "filled_duplicate_suppressed" damit
        # downstream Konsumenten (Dashboard, AuditStream) das vom echten
        # rejected_fill unterscheiden können. Bridge zählt es als filled,
        # weil das envelope tatsächlich gefüllt wurde, nur nicht von uns.
        rec = base("filled_duplicate_suppressed")
        rec["reason"] = str(exc)
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.POSITION_OPEN,
            # 2026-06-26: the duplicate envelope WAS actually opened (by the first
            # process), so the lifecycle reaches POSITION_OPEN via the full legal
            # path — same sequence as the success branch below. The previous
            # shortcut [VALIDATED -> POSITION_OPEN] is an illegal transition and
            # raised IllegalLifecycleTransition on every duplicate, crash-looping
            # kai-entry-watch into a start-limit `failed` state.
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.ENTRY_TRIGGERED,
                OrderLifecycleState.ORDER_BUILDING,
                OrderLifecycleState.ORDER_SUBMITTED,
                OrderLifecycleState.ORDER_ACCEPTED,
                OrderLifecycleState.POSITION_OPEN,
            ],
            reason="duplicate_fill_already_executed",
        )
        _append_bridge_audit(rec)
        result.filled += 1
        logger.info(
            "[bridge] duplicate-fill suppressed envelope=%s symbol=%s reason=%s",
            envelope_id,
            symbol,
            exc,
        )
        return
    except Exception as exc:  # noqa: BLE001
        rec = base("rejected_fill")
        rec["error"] = str(exc)
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.FAILED,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.ENTRY_TRIGGERED,
                OrderLifecycleState.ORDER_BUILDING,
                OrderLifecycleState.ORDER_SUBMITTED,
                OrderLifecycleState.FAILED,
            ],
            reason="paper_engine_exception",
        )
        _append_bridge_audit(rec)
        result.rejected_fill += 1
        return

    if fill is None:
        rec = base("rejected_fill")
        rec["reason"] = "paper_engine_returned_none"
        rec["order_id"] = order.order_id
        _attach_lifecycle(
            rec,
            final_state=OrderLifecycleState.FAILED,
            states=[
                OrderLifecycleState.RECEIVED,
                OrderLifecycleState.PARSED,
                OrderLifecycleState.VALIDATED,
                OrderLifecycleState.ENTRY_TRIGGERED,
                OrderLifecycleState.ORDER_BUILDING,
                OrderLifecycleState.ORDER_SUBMITTED,
                OrderLifecycleState.FAILED,
            ],
            reason="paper_engine_returned_none",
        )
        _append_bridge_audit(rec)
        result.rejected_fill += 1
        return

    # V25-C: hand the staged-exit ladder over to the engine. We split the
    # filled position equally across all targets (e.g. 4 targets → 25/25/25/25
    # of the original quantity). The first target wins TP1, the second TP2,
    # etc. The position-monitor (cron 1min, V25-A) will close each tier as
    # its price gets hit, instead of dumping the entire position at TP1.
    if len(targets) > 1:
        share_each = 1.0 / len(targets)
        tiers = [(price, share_each) for price in targets]
        engine.set_position_tp_tiers(symbol, tiers)

    rec = base("filled")
    rec["order_id"] = order.order_id
    rec["fill_id"] = fill.fill_id
    rec["symbol"] = symbol
    rec["side"] = side_str
    rec["quantity"] = size_result.position_size_units
    rec["position_size_pct"] = size_result.position_size_pct
    rec["position_size_rationale"] = size_result.rationale
    rec["max_loss_usd"] = size_result.max_loss_usd
    rec["max_loss_pct"] = size_result.max_loss_pct
    rec["signal_margin_pct"] = risk_allocation_pct
    rec["signal_leverage"] = leverage_val
    rec["entry_price_target"] = entry_price
    rec["fill_price"] = fill.fill_price
    # 2026-06-10 PnL-truth transparency: record the basis the fill was booked on
    # and the spot observed at fill time, so a target-hit-but-loss (or the
    # absence of one) is always explainable from the audit without guessing.
    rec["fill_basis"] = "signal_entry" if fill_at_entry else "spot"
    rec["spot_at_fill"] = current_price
    rec["filled_off_entry_pct"] = (
        round((current_price - entry_price) / entry_price * 100.0, 4) if entry_price > 0 else None
    )
    rec["stop_loss"] = stop_loss
    rec["take_profit"] = tp1
    rec["take_profit_tiers"] = (
        [{"price": price, "qty_share": 1.0 / len(targets)} for price in targets]
        if len(targets) > 1
        else []
    )
    rec["executable_intent"] = _build_executable_intent(
        envelope_id=envelope_id,
        correlation_id=str(rec["correlation_id"]),
        source=source,
        payload=payload,
        symbol=symbol,
        side=side_str,
        entry_price=entry_price,
        stop_loss=stop_loss,
        targets=targets,
        quantity=size_result.position_size_units,
    ).to_dict()
    rec["order_intent"] = rec["executable_intent"]
    rec["leverage_mode"] = "paper_audit_only"
    rec["risk_allocation_source"] = (
        "signal_margin_pct"
        if _float(payload.get("margin_pct")) is not None
        else "risk_engine_default"
    )
    rec["risk_check_id"] = risk_result.check_id
    _attach_lifecycle(
        rec,
        final_state=OrderLifecycleState.POSITION_OPEN,
        states=[
            OrderLifecycleState.RECEIVED,
            OrderLifecycleState.PARSED,
            OrderLifecycleState.VALIDATED,
            OrderLifecycleState.ENTRY_TRIGGERED,
            OrderLifecycleState.ORDER_BUILDING,
            OrderLifecycleState.ORDER_SUBMITTED,
            OrderLifecycleState.ORDER_ACCEPTED,
            OrderLifecycleState.POSITION_OPEN,
        ],
        reason="paper_order_filled",
    )
    _append_bridge_audit(rec)
    result.filled += 1
    logger.info(
        "[bridge] filled envelope=%s %s qty=%.4f entry=%.4f sl=%.4f tp=%.4f fill=%.4f",
        envelope_id,
        symbol,
        size_result.position_size_units,
        entry_price,
        stop_loss,
        tp1,
        fill.fill_price,
    )


__all__ = ["BridgeTickResult", "backfill_run", "run_tick"]
