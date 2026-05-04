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
- Channel-stated leverage is ignored in paper mode (consistent with
  execution safety invariants).
- Entry-type: limit-style. Only fills when the current spot price is
  within ``entry_tolerance_pct`` of the operator entry; otherwise the
  envelope stays ``pending`` and is re-checked next tick.
- TTL: after ``ttl_hours`` (default 24) an unfilled envelope is expired.
- Take-profit: TP1 only (``targets[0]``). Staged exits are out of scope.

Fail-closed:
- ``operator_signal_bridge_enabled=False`` (default) -> tick() is a no-op.
- Source not in allowlist -> skipped with audit, no fill.
- Missing entry / stop_loss / targets -> rejected at gate.
- Short/sell signals -> rejected in v1 (paper_engine has no open-short
  primitive; would require separate logic).

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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.settings import get_settings
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.service import get_market_data_snapshot
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits

logger = logging.getLogger(__name__)

_ENVELOPE_LOG = Path("artifacts/telegram_message_envelope.jsonl")
_BRIDGE_LOG = Path("artifacts/bridge_pending_orders.jsonl")
_PAPER_AUDIT_LOG = Path("artifacts/paper_execution_audit.jsonl")

# Terminal stages — envelopes that have reached any of these are done.
_TERMINAL_STAGES = frozenset(
    {
        "filled",
        "expired",
        "rejected_risk",
        "rejected_size",
        "rejected_incomplete",
        "rejected_short_unsupported",
        "rejected_fill",
        "rejected_position_exists",
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
    rejected_incomplete: int = 0
    rejected_short: int = 0
    rejected_fill: int = 0
    rejected_position_exists: int = 0
    no_market_data: int = 0
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
            "rejected_incomplete": self.rejected_incomplete,
            "rejected_short": self.rejected_short,
            "rejected_fill": self.rejected_fill,
            "rejected_position_exists": self.rejected_position_exists,
            "no_market_data": self.no_market_data,
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


def _float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


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
    )


def _audit_base(
    *, envelope_id: str, stage: str, source: str, envelope: dict[str, object]
) -> dict[str, object]:
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "event": "operator_signal_bridge",
        "envelope_id": envelope_id,
        "stage": stage,
        "source": source,
        "origin_envelope_stage": envelope.get("stage"),
        "origin_envelope_timestamp": envelope.get("timestamp_utc"),
    }


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


# V25-D (2026-05-05) — Channel scale normalisation.
#
# The premium Telegram channel posts Bybit-Futures pairs in two distinct
# formats: (a) direct USD for 1.0+-USD tokens (HYPE 40.9, GIGGLE 39.5),
# (b) integer ticks for sub-cent tokens (SWARMS '32450' = $0.0003245,
# 1000LUNC '10310' = $0.10310). The integer form keeps targets readable
# but breaks any price comparison against a USD-priced provider.
#
# We detect the scale by comparing channel-entry against current_price:
# the ratio falls into a power-of-ten band and we divide entry / SL /
# targets by that factor. If the ratio is outside the recognised bands
# we leave the values untouched and let the existing tolerance check
# decide — better silent pass-through than a wrong scale.
_RECOGNISED_SCALES = (1.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8)


def _detect_scale_factor(channel_value: float, provider_price: float) -> float:
    """Return the power-of-ten factor that brings channel_value to provider scale.

    Returns 1.0 if no recognised scale matches (fail-soft: bridge then
    treats the values as-is and the tolerance gate will reject sensibly).
    """
    if channel_value <= 0 or provider_price <= 0:
        return 1.0
    ratio = channel_value / provider_price
    # Find the recognised scale whose log-distance to the ratio is smallest
    # AND within ±50% (strict guardrail so a 2× drift is not "rescaled").
    best_factor = 1.0
    best_dist = abs(ratio - 1.0)
    for factor in _RECOGNISED_SCALES:
        dist = abs(ratio - factor) / factor
        if dist <= 0.5 and dist < best_dist:
            best_factor = factor
            best_dist = dist
    return best_factor


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


async def run_tick() -> BridgeTickResult:
    """One bridge tick: scan new envelopes, re-check pending ones, fill/expire."""
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
    result.envelopes_scanned = len(pending_signals)

    if not pending_signals:
        return result

    engine = PaperExecutionEngine(
        initial_equity=settings.execution.paper_initial_equity,
        fee_pct=settings.execution.paper_fee_pct,
        slippage_pct=settings.execution.paper_slippage_pct,
        live_enabled=False,
    )
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
) -> None:
    envelope_id = str(envelope.get("envelope_id") or "")
    source = _extract_source(envelope)
    base = lambda stage: _audit_base(  # noqa: E731
        envelope_id=envelope_id, stage=stage, source=source, envelope=envelope
    )

    # Gate 1: allowlist
    if source not in allowlist:
        rec = base("skipped_source")
        rec["allowlist"] = sorted(allowlist)
        _append_bridge_audit(rec)
        result.skipped_source += 1
        return

    # Gate 2: TTL
    ts_raw = envelope.get("timestamp_utc")
    ts_str = ts_raw if isinstance(ts_raw, str) else None
    if _ttl_exceeded(ts_str, ttl_hours):
        rec = base("expired")
        rec["ttl_hours"] = ttl_hours
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
        )
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
        _append_bridge_audit(rec)
        result.rejected_incomplete += 1
        return

    # v1: only long/buy supported by paper_engine open path
    if direction != "long" or side_str != "buy":
        rec = base("rejected_short_unsupported")
        rec["direction"] = direction
        rec["side"] = side_str
        _append_bridge_audit(rec)
        result.rejected_short += 1
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
        _append_bridge_audit(rec)
        result.rejected_position_exists += 1
        return

    # Gate 4: market data / entry-band
    current_price = await _fetch_price(symbol)
    if current_price is None:
        rec = base("pending")
        rec["reason"] = "no_market_data"
        rec["target_entry"] = entry_price
        _append_bridge_audit(rec)
        result.no_market_data += 1
        return

    # V25-D (2026-05-05): rescale Bybit-Futures integer-tick entries to USD
    # before the tolerance check. Channel posts e.g. SWARMS 32450 (×10^6) /
    # 1000LUNC 10310 (×10^5) which would otherwise blow past every gate.
    # _detect_scale_factor returns 1.0 when no scaling is needed (HYPE,
    # GIGGLE, BTC, …) so this is a no-op for direct-USD signals.
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
        _apply_scale(envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}, scale_factor)

    if not _within_tolerance(
        current_price=current_price,
        target_price=entry_price,
        tolerance_pct=tolerance_pct,
        side=side_str,
    ):
        rec = base("pending")
        rec["reason"] = "price_outside_tolerance"
        rec["current_price"] = current_price
        rec["target_entry"] = entry_price
        rec["tolerance_pct"] = tolerance_pct
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
    risk_result = risk.check_order(
        symbol=symbol,
        side=side_str,
        signal_confidence=1.0,
        signal_confluence_count=99,
        stop_loss_price=stop_loss,
        current_open_positions=current_open,
        entry_price=entry_price,
        take_profit_price=tp1,
    )
    if not risk_result.approved:
        rec = base("rejected_risk")
        rec["risk_check_id"] = risk_result.check_id
        rec["violations"] = list(risk_result.violations)
        _append_bridge_audit(rec)
        result.rejected_risk += 1
        return

    # Gate 6: Position sizing
    equity = engine.portfolio.cash
    size_result = risk.calculate_position_size(
        symbol=symbol,
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        equity=equity,
    )
    if not size_result.approved or size_result.position_size_units <= 0:
        rec = base("rejected_size")
        rec["rationale"] = size_result.rationale
        _append_bridge_audit(rec)
        result.rejected_size += 1
        return

    # Create + fill
    idem = f"opbridge:{envelope_id}"
    try:
        order = engine.create_order(
            symbol=symbol,
            side=side_str,
            quantity=size_result.position_size_units,
            order_type="limit",
            limit_price=entry_price,
            stop_loss=stop_loss,
            take_profit=tp1,
            idempotency_key=idem,
            risk_check_id=risk_result.check_id,
        )
        fill = engine.fill_order(order, current_price=current_price)
    except Exception as exc:  # noqa: BLE001
        rec = base("rejected_fill")
        rec["error"] = str(exc)
        _append_bridge_audit(rec)
        result.rejected_fill += 1
        return

    if fill is None:
        rec = base("rejected_fill")
        rec["reason"] = "paper_engine_returned_none"
        rec["order_id"] = order.order_id
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
    rec["entry_price_target"] = entry_price
    rec["fill_price"] = fill.fill_price
    rec["stop_loss"] = stop_loss
    rec["take_profit"] = tp1
    rec["take_profit_tiers"] = [
        {"price": price, "qty_share": 1.0 / len(targets)} for price in targets
    ] if len(targets) > 1 else []
    rec["risk_check_id"] = risk_result.check_id
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


__all__ = ["BridgeTickResult", "run_tick"]
