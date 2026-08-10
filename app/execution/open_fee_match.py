"""FIFO-Zuordnung der Entry-Fee zu einem Close-Event.

``trade_pnl_usd`` im Paper-Audit ist **nicht** voll belastet: ``paper_engine``
zieht beim Schließen nur die Close-Fee ab (`pnl = (fill - entry) * qty - fee`).
Die Entry-Fee wurde beim Öffnen vom Cash abgezogen, taucht im Trade-PnL aber
nie auf. ``churn_report`` weiß das und korrigiert es nachträglich
(``net = trade_pnl - ofee``); jeder andere Konsument las den Wert roh — unter
anderem ``paper_quality_snapshot``, dessen Ausgabe als „net-of-fee realized
PnL" in das Rotations-Verdikt fließt.

Gemessen am Pi-Buch (2026-08-10, Fenster 200 Closes): die fehlende Entry-Fee
macht **25,3 %** des ausgewiesenen |PnL| aus. Bei einem Fee-Drag von ~120 % ist
das grob die halben Round-Trip-Kosten.

Die Zuordnung ist FIFO über die Entry-Fills desselben Symbols, identisch zur
Logik in ``churn_report``: jede Öffnung legt (Menge, Fee-pro-Einheit) in eine
Deque, jeder Close zieht daraus die passende Menge. Ein Close ohne passende
Öffnung (Legacy-Kontamination) bleibt als ``orphan`` markiert, statt still eine
Fee von 0 zu erfinden.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

__all__ = ["CloseWithOpenFee", "CLOSE_EVENT_TYPES", "is_entry_fill", "match_open_fees"]

CLOSE_EVENT_TYPES = frozenset({"position_closed", "position_partial_closed"})


@dataclass(frozen=True)
class CloseWithOpenFee:
    """Ein Close-Event mit der ihm zugeordneten Entry-Fee."""

    record: dict[str, Any]
    trade_pnl_usd: float
    open_fee_usd: float
    matched_quantity: float

    @property
    def orphan(self) -> bool:
        """Kein passender Entry-Fill gefunden — Open-Fee ist unbekannt, nicht 0."""
        return self.matched_quantity <= 1e-9

    @property
    def net_pnl_usd(self) -> float:
        """Voll belastet: Preisbewegung minus Close-Fee minus Entry-Fee."""
        return self.trade_pnl_usd - self.open_fee_usd


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def is_entry_fill(record: dict[str, Any]) -> bool:
    """``order_filled``, das eine Position ÖFFNET (long-buy oder short-sell)."""
    if record.get("event_type") != "order_filled":
        return False
    side = str(record.get("side", "")).lower()
    position_side = str(record.get("position_side", "")).lower()
    return (side == "buy" and position_side == "long") or (
        side == "sell" and position_side == "short"
    )


def _close_quantity(record: dict[str, Any], trade_pnl: float, close_fee: float) -> float:
    """Menge des Close-Events.

    ``position_partial_closed`` trägt weder ``quantity`` noch ``position_side``.
    Sie wird dann arithmetisch abgeleitet: ``|gross| = |exit-entry| * qty`` gilt
    für long UND short, weil ``gross = trade_pnl + close_fee`` das Vorzeichen
    schon trägt (NEO-F-201: die frühere Ableitung über ``position_side`` verwarf
    Short-Partials still).
    """
    qty = _as_float(record.get("quantity"))
    if qty > 0:
        return qty
    entry = _as_float(record.get("entry_price"))
    exit_price = _as_float(record.get("exit_price"))
    span = abs(exit_price - entry)
    if span <= 1e-12:
        return 0.0
    return abs(trade_pnl + close_fee) / span


def match_open_fees(records: list[dict[str, Any]]) -> list[CloseWithOpenFee]:
    """Ordne jedem Close-Event seine FIFO-gematchte Entry-Fee zu.

    ``records`` muss in chronologischer Reihenfolge vorliegen (so, wie das
    Append-only-Journal sie führt). Closes ohne ``trade_pnl_usd`` werden
    übersprungen: ``realized_pnl_usd`` ist portfolio-KUMULATIV und niemals ein
    Trade-Wert (NEO-P-101-r2).
    """
    opens: dict[str, deque[list[float]]] = defaultdict(deque)
    out: list[CloseWithOpenFee] = []

    for record in records:
        symbol = str(record.get("symbol", "?"))

        if is_entry_fill(record):
            quantity = _as_float(record.get("filled_quantity")) or _as_float(record.get("quantity"))
            if quantity <= 0:
                continue
            fee = _as_float(record.get("fee_usd"))
            opens[symbol].append([quantity, fee / quantity])
            continue

        if record.get("event_type") not in CLOSE_EVENT_TYPES:
            continue
        if record.get("trade_pnl_usd") is None:
            continue

        trade_pnl = _as_float(record.get("trade_pnl_usd"))
        close_fee = _as_float(record.get("fee_usd"))
        quantity = _close_quantity(record, trade_pnl, close_fee)
        if quantity <= 0:
            continue

        pending = opens[symbol]
        need, open_fee, matched = quantity, 0.0, 0.0
        while need > 1e-9 and pending:
            open_qty, fee_per_unit = pending[0]
            take = min(need, open_qty)
            open_fee += take * fee_per_unit
            matched += take
            need -= take
            open_qty -= take
            if open_qty <= 1e-9:
                pending.popleft()
            else:
                pending[0][0] = open_qty

        out.append(
            CloseWithOpenFee(
                record=record,
                trade_pnl_usd=trade_pnl,
                open_fee_usd=open_fee,
                matched_quantity=matched,
            )
        )

    return out
