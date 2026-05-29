"""Replay paper-execution audit JSONL back into an in-memory portfolio.

Used by:
- portfolio_read (read-only snapshot projections)
- PaperExecutionEngine.rehydrate_from_audit (state recovery across processes)

The replay honors order_created → order_filled ordering so stop_loss and
take_profit values attached to the order are restored into the resulting
position (they are not repeated on the fill record).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.execution.models import (
    LifecycleTransition,
    OrderLifecycleState,
    PaperPosition,
    validate_lifecycle_transition,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditReplayResult:
    positions: dict[str, PaperPosition]
    cash_usd: float
    realized_pnl_usd: float
    available: bool
    error: str | None = None
    # 2026-05-12 Sprint C: persistente idempotency-Spur aus Audit damit
    # cross-process & cross-engine-instance Race-Conditions die zu doppelten
    # PaperFills geführt haben (Q/USDT 2026-05-09 Bug) nicht mehr durchkommen.
    # Set leer wenn audit-file fehlt — Replay bleibt rückwärtskompatibel.
    filled_idempotency_keys: frozenset[str] = field(default_factory=frozenset)
    # 2026-05-25 Forensik-Fix: Resilience-Skips statt fataler Abbruch bei
    # historischen Race-Conditions (z.B. doppeltem position_closed auf MATIC
    # 2026-05-10). Liste von (line_number, reason)-Tupeln. Aufrufer können
    # die Liste anzeigen, ohne dass das gesamte Portfolio unsichtbar wird.
    skipped_events: tuple[tuple[int, str], ...] = field(default_factory=tuple)
    # PRE-A: lifecycle_transition rows are audit metadata, not the position
    # source of truth. Replay reconstructs valid history but keeps portfolio
    # recovery available when legacy/corrupt lifecycle rows are encountered.
    lifecycle_history: dict[str, tuple[LifecycleTransition, ...]] = field(default_factory=dict)
    lifecycle_replay_errors: tuple[str, ...] = ()


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_lifecycle_transition(
    payload: dict[str, object],
    *,
    line_number: int,
) -> tuple[LifecycleTransition | None, str | None]:
    correlation_id = _coerce_str(payload.get("correlation_id"))
    from_state_raw = _coerce_str(payload.get("from_state"))
    to_state_raw = _coerce_str(payload.get("to_state"))
    timestamp_utc = _coerce_str(payload.get("timestamp_utc"))
    reason = _coerce_str(payload.get("reason")) or ""

    if (
        correlation_id is None
        or from_state_raw is None
        or to_state_raw is None
        or timestamp_utc is None
    ):
        return (
            None,
            f"audit_lifecycle_validation_error_line_{line_number}: missing_required_field",
        )

    try:
        from_state = OrderLifecycleState(from_state_raw)
        to_state = OrderLifecycleState(to_state_raw)
        validate_lifecycle_transition(from_state, to_state)
    except ValueError:
        return (
            None,
            "audit_lifecycle_validation_error_line_"
            f"{line_number}: illegal {from_state_raw} -> {to_state_raw}",
        )

    return (
        LifecycleTransition(
            correlation_id=correlation_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            timestamp_utc=timestamp_utc,
        ),
        None,
    )


def replay_paper_audit(audit_path: Path) -> AuditReplayResult:
    """Replay the paper execution audit JSONL into an AuditReplayResult."""
    if not audit_path.exists():
        return AuditReplayResult(
            positions={},
            cash_usd=0.0,
            realized_pnl_usd=0.0,
            available=True,
            error=None,
        )

    positions: dict[str, PaperPosition] = {}
    # 2026-05-12 Sprint A: order_meta erweitert um leverage + source. Pre-Sprint-A
    # audit-rows haben diese Felder nicht — _coerce_float/str geben None/"" zurück
    # und der Replay bleibt rückwärtskompatibel.
    order_meta: dict[str, tuple[float | None, float | None, float | None, str, str]] = {}
    # 2026-05-12 Sprint C: idempotency_key-Mapping aus order_created. Wenn das
    # zugehörige order_filled später erfolgreich verarbeitet wird, landet der
    # key im filled_keys-set. Cross-process Race-Schutz (siehe Q/USDT 2026-05-09).
    order_idem_by_id: dict[str, str] = {}
    filled_keys: set[str] = set()
    lifecycle_history: dict[str, list[LifecycleTransition]] = {}
    lifecycle_replay_errors: list[str] = []
    cash_usd = 0.0
    realized_pnl_usd = 0.0
    # 2026-05-25 Forensik-Fix: skipped events sammeln statt Replay abzubrechen.
    skipped: list[tuple[int, str]] = []

    for line_number, raw_line in enumerate(
        audit_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_json_decode_error_line_{line_number}",
            )

        if not isinstance(payload, dict):
            return AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_payload_type_error_line_{line_number}",
            )

        event_type = _coerce_str(payload.get("event_type"))
        if event_type == "portfolio_correction":
            # DS-20260529-V1: explicit, auditable book correction. cash_usd and
            # realized_pnl_usd are reconstructed as the *latest snapshot* from
            # each fill (see below), so a correction appended after the affected
            # fills shifts the running totals by an explicit delta. Used once to
            # back out the MATIC phantom-PnL (+73,459) booked against BitMEX's
            # delisted-instrument price. A later real fill overwrites the
            # snapshot with the engine's corrected-forward cumulative, so the
            # correction is not double-counted. See scripts/apply_phantom_correction.py.
            realized_delta = _coerce_float(payload.get("realized_pnl_delta_usd"))
            if realized_delta is not None:
                realized_pnl_usd += realized_delta
            cash_delta = _coerce_float(payload.get("cash_delta_usd"))
            if cash_delta is not None:
                cash_usd += cash_delta
            continue

        if event_type == "lifecycle_transition":
            transition, error = _coerce_lifecycle_transition(payload, line_number=line_number)
            if transition is not None:
                history = lifecycle_history.setdefault(transition.correlation_id, [])
                if history and history[-1].to_state != transition.from_state:
                    lifecycle_replay_errors.append(
                        "audit_lifecycle_validation_error_line_"
                        f"{line_number}: discontinuous "
                        f"{history[-1].to_state.value} -> {transition.from_state.value}"
                    )
                    continue
                history.append(transition)
            if error is not None:
                lifecycle_replay_errors.append(error)
            continue

        if event_type == "order_created":
            order_id = _coerce_str(payload.get("order_id"))
            if order_id is not None:
                order_meta[order_id] = (
                    _coerce_float(payload.get("stop_loss")),
                    _coerce_float(payload.get("take_profit")),
                    _coerce_float(payload.get("leverage")),
                    _coerce_str(payload.get("source")) or "",
                    _coerce_str(payload.get("correlation_id")) or "",
                )
                # Sprint C: idempotency_key persistieren damit fill-replay sie
                # in filled_keys eintragen kann sobald order_filled gesehen wird.
                idem_key = _coerce_str(payload.get("idempotency_key"))
                if idem_key:
                    order_idem_by_id[order_id] = idem_key
            continue

        if event_type == "position_tp_tiers_set":
            # V25-C (2026-05-04): re-attach the tier ladder + initial_quantity
            # to a position that was rehydrated from prior order_filled rows.
            sym = _coerce_str(payload.get("symbol"))
            if sym is None or sym not in positions:
                continue
            existing = positions[sym]
            raw_tiers = payload.get("tiers")
            tiers: list[tuple[float, float]] = []
            if isinstance(raw_tiers, list):
                for entry in raw_tiers:
                    if not isinstance(entry, dict):
                        continue
                    p = _coerce_float(entry.get("price"))
                    q = _coerce_float(entry.get("qty_share"))
                    if p is None or q is None or p <= 0 or q <= 0:
                        continue
                    tiers.append((p, q))
            initial_qty = _coerce_float(payload.get("initial_quantity")) or existing.quantity
            existing.take_profit_tiers = sorted(tiers, key=lambda t: t[0])
            existing.initial_quantity = initial_qty
            continue

        if event_type == "position_partial_closed":
            # Carry the tier ladder forward — the actual quantity reduction
            # was already booked by the preceding order_filled (sell) row.
            sym = _coerce_str(payload.get("symbol"))
            if sym is None or sym not in positions:
                continue
            existing = positions[sym]
            raw_remaining = payload.get("remaining_tiers")
            remaining: list[tuple[float, float]] = []
            if isinstance(raw_remaining, list):
                for entry in raw_remaining:
                    if not isinstance(entry, dict):
                        continue
                    p = _coerce_float(entry.get("price"))
                    q = _coerce_float(entry.get("qty_share"))
                    if p is None or q is None or p <= 0 or q <= 0:
                        continue
                    remaining.append((p, q))
            existing.take_profit_tiers = sorted(remaining, key=lambda t: t[0])
            continue

        if event_type == "position_adjusted":
            sym = _coerce_str(payload.get("symbol"))
            if sym is None or sym not in positions:
                continue
            existing = positions[sym]
            new_sl = _coerce_float(payload.get("stop_loss"))
            new_tp = _coerce_float(payload.get("take_profit"))
            positions[sym] = PaperPosition(
                symbol=existing.symbol,
                quantity=existing.quantity,
                avg_entry_price=existing.avg_entry_price,
                stop_loss=new_sl if new_sl is not None else existing.stop_loss,
                take_profit=new_tp if new_tp is not None else existing.take_profit,
                opened_at=existing.opened_at,
                realized_pnl_usd=existing.realized_pnl_usd,
                position_side=existing.position_side,
                take_profit_tiers=list(existing.take_profit_tiers),
                initial_quantity=existing.initial_quantity,
                correlation_id=existing.correlation_id,
                leverage=existing.leverage,
                source=existing.source,
            )
            continue

        if event_type != "order_filled":
            continue

        symbol = _coerce_str(payload.get("symbol"))
        side = _coerce_str(payload.get("side"))
        quantity = _coerce_float(payload.get("quantity"))
        fill_price = _coerce_float(payload.get("fill_price"))
        order_id = _coerce_str(payload.get("order_id"))
        filled_at = _coerce_str(payload.get("filled_at")) or datetime.now(UTC).isoformat()

        if (
            symbol is None
            or side not in {"buy", "sell"}
            or quantity is None
            or fill_price is None
            or quantity <= 0.0
            or fill_price <= 0.0
        ):
            # 2026-05-25 Forensik-Fix: invalid fill row darf nicht das gesamte
            # Portfolio unsichtbar machen. Skip + warn + weiterlaufen.
            reason = f"audit_fill_validation_error_line_{line_number}"
            logger.warning(
                "[audit_replay] skip %s (symbol=%r side=%r qty=%r price=%r)",
                reason,
                symbol,
                side,
                quantity,
                fill_price,
            )
            skipped.append((line_number, reason))
            continue

        stop_loss: float | None = None
        take_profit: float | None = None
        leverage: float | None = None
        source: str = ""
        correlation_id = _coerce_str(payload.get("correlation_id")) or ""
        if order_id is not None:
            meta = order_meta.get(order_id)
            if meta is not None:
                stop_loss, take_profit, leverage, source, order_correlation_id = meta
                correlation_id = correlation_id or order_correlation_id
            # Sprint C: filled_keys aus der order_idem_by_id-Brücke befüllen.
            # Wenn ein order_filled für einen bekannten order_id replay-läuft,
            # wissen wir dass dieser idempotency_key bereits einen Fill produziert
            # hat. Cross-process Race-Schutz für engine.create_order.
            idem_for_order = order_idem_by_id.get(order_id)
            if idem_for_order:
                filled_keys.add(idem_for_order)

        # NEO-P-101-r2: v2 audit rows carry position_side; v1 rows default to long.
        position_side_val = _coerce_str(payload.get("position_side")) or "long"
        existing = positions.get(symbol)
        is_open = (position_side_val == "long" and side == "buy") or (
            position_side_val == "short" and side == "sell"
        )
        is_close = (position_side_val == "long" and side == "sell") or (
            position_side_val == "short" and side == "buy"
        )

        if is_open:
            if existing is None:
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=quantity,
                    avg_entry_price=fill_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    opened_at=filled_at,
                    realized_pnl_usd=0.0,
                    position_side=position_side_val,
                    correlation_id=correlation_id,
                    leverage=leverage,
                    source=source,
                )
            else:
                if existing.position_side != position_side_val:
                    # 2026-05-25 Forensik-Fix: position_side-Konflikt aus historischer
                    # Race-Condition oder fehlerhaftem Replay darf nicht das gesamte
                    # Portfolio unsichtbar machen.
                    reason = f"audit_position_side_conflict_line_{line_number}"
                    logger.warning(
                        "[audit_replay] skip %s (existing.side=%r new.side=%r)",
                        reason,
                        existing.position_side,
                        position_side_val,
                    )
                    skipped.append((line_number, reason))
                    continue
                total_qty = existing.quantity + quantity
                avg_entry = (
                    (existing.avg_entry_price * existing.quantity) + (fill_price * quantity)
                ) / total_qty
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=total_qty,
                    avg_entry_price=avg_entry,
                    stop_loss=stop_loss if stop_loss is not None else existing.stop_loss,
                    take_profit=(take_profit if take_profit is not None else existing.take_profit),
                    opened_at=existing.opened_at,
                    realized_pnl_usd=existing.realized_pnl_usd,
                    position_side=existing.position_side,
                    take_profit_tiers=list(existing.take_profit_tiers),
                    initial_quantity=existing.initial_quantity,
                    correlation_id=existing.correlation_id or correlation_id,
                    leverage=existing.leverage if existing.leverage is not None else leverage,
                    source=existing.source or source,
                )
        elif is_close:
            if (
                existing is None
                or existing.position_side != position_side_val
                or existing.quantity + 1e-9 < quantity
            ):
                # 2026-05-25 Forensik-Fix: doppelter close oder out-of-order
                # close (z.B. MATIC/USDT 2026-05-10 Race-Condition Zeile 75)
                # darf nicht den gesamten Portfolio-Replay killen. Skip + warn.
                # Realized-PnL aus dieser Zeile wird trotzdem überschrieben
                # über das spätere position_closed.realized_pnl_usd-Field
                # (per_fill cumulative snapshot, siehe Z. 320 unten).
                reason = f"audit_close_without_position_line_{line_number}"
                logger.warning(
                    "[audit_replay] skip %s (sym=%s have_pos=%s req_qty=%s)",
                    reason,
                    symbol,
                    existing is not None,
                    quantity,
                )
                skipped.append((line_number, reason))
                continue
            remaining = existing.quantity - quantity
            if remaining <= 1e-8:
                del positions[symbol]
            else:
                positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=remaining,
                    avg_entry_price=existing.avg_entry_price,
                    stop_loss=existing.stop_loss,
                    take_profit=existing.take_profit,
                    opened_at=existing.opened_at,
                    realized_pnl_usd=existing.realized_pnl_usd,
                    position_side=existing.position_side,
                    take_profit_tiers=list(existing.take_profit_tiers),
                    initial_quantity=existing.initial_quantity,
                    correlation_id=existing.correlation_id,
                    leverage=existing.leverage,
                    source=existing.source,
                )
        else:
            # 2026-05-25 Forensik-Fix: unbekannte side/position-Kombi skipt.
            reason = f"audit_side_position_combo_error_line_{line_number}"
            logger.warning(
                "[audit_replay] skip %s (side=%r position_side=%r)", reason, side, position_side_val
            )
            skipped.append((line_number, reason))
            continue

        portfolio_cash = _coerce_float(payload.get("portfolio_cash"))
        if portfolio_cash is not None:
            cash_usd = portfolio_cash
        # NEO-P-101-r2 / DECISION_LOG D-209: payload["realized_pnl_usd"]
        # is the KUMULATIVE portfolio total per fill — NEVER per-trade. Per-trade
        # NETTO PnL lives in payload["trade_pnl_usd"] on schema_version=v2
        # position_closed events. Reading here is correct: we want the latest
        # cumulative snapshot to seed self._portfolio.realized_pnl_usd.
        realized = _coerce_float(payload.get("realized_pnl_usd"))
        if realized is not None:
            realized_pnl_usd = realized

    return AuditReplayResult(
        positions=positions,
        cash_usd=cash_usd,
        realized_pnl_usd=realized_pnl_usd,
        available=True,
        error=None,
        filled_idempotency_keys=frozenset(filled_keys),
        skipped_events=tuple(skipped),
        lifecycle_history={
            correlation_id: tuple(transitions)
            for correlation_id, transitions in lifecycle_history.items()
        },
        lifecycle_replay_errors=tuple(lifecycle_replay_errors),
    )
