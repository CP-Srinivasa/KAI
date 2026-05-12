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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.execution.models import PaperPosition


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


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
    order_meta: dict[
        str, tuple[float | None, float | None, float | None, str]
    ] = {}
    # 2026-05-12 Sprint C: idempotency_key-Mapping aus order_created. Wenn das
    # zugehörige order_filled später erfolgreich verarbeitet wird, landet der
    # key im filled_keys-set. Cross-process Race-Schutz (siehe Q/USDT 2026-05-09).
    order_idem_by_id: dict[str, str] = {}
    filled_keys: set[str] = set()
    cash_usd = 0.0
    realized_pnl_usd = 0.0

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
        if event_type == "order_created":
            order_id = _coerce_str(payload.get("order_id"))
            if order_id is not None:
                order_meta[order_id] = (
                    _coerce_float(payload.get("stop_loss")),
                    _coerce_float(payload.get("take_profit")),
                    _coerce_float(payload.get("leverage")),
                    _coerce_str(payload.get("source")) or "",
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
            return AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_fill_validation_error_line_{line_number}",
            )

        stop_loss: float | None = None
        take_profit: float | None = None
        leverage: float | None = None
        source: str = ""
        if order_id is not None:
            meta = order_meta.get(order_id)
            if meta is not None:
                stop_loss, take_profit, leverage, source = meta
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
                    leverage=leverage,
                    source=source,
                )
            else:
                if existing.position_side != position_side_val:
                    return AuditReplayResult(
                        positions={},
                        cash_usd=0.0,
                        realized_pnl_usd=0.0,
                        available=False,
                        error=f"audit_position_side_conflict_line_{line_number}",
                    )
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
                    leverage=existing.leverage if existing.leverage is not None else leverage,
                    source=existing.source or source,
                )
        elif is_close:
            if (
                existing is None
                or existing.position_side != position_side_val
                or existing.quantity + 1e-9 < quantity
            ):
                return AuditReplayResult(
                    positions={},
                    cash_usd=0.0,
                    realized_pnl_usd=0.0,
                    available=False,
                    error=f"audit_close_without_position_line_{line_number}",
                )
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
                    leverage=existing.leverage,
                    source=existing.source,
                )
        else:
            return AuditReplayResult(
                positions={},
                cash_usd=0.0,
                realized_pnl_usd=0.0,
                available=False,
                error=f"audit_side_position_combo_error_line_{line_number}",
            )

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
    )
