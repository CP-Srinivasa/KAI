"""Eine Leseregel für den Per-Trade-PnL eines Close-Events.

Es gab drei: ``analytics_db`` prüfte ``schema_version='v2' OR trade_pnl_usd IS
NOT NULL`` und behandelte Shorts seitenbewusst, ``dashboard`` gatete allein auf
den Stempel, ``portfolio_read`` prüfte den Wert, rekonstruierte aber ohne Seite.
Drei Regeln über einer Datei heißen: dieselbe Zeile ergibt je nach Leser eine
andere Zahl.

Die kanonische Regel ist die von ``analytics_db``, weil sie als einzige beide
Fälle trägt:

1. Liegt ``trade_pnl_usd`` vor, ist es der Wahrheitswert — unabhängig davon, ob
   die Zeile gestempelt ist. Der Reconciler schrieb den Wert ohne Stempel; ein
   Stempel-Gate warf ihn weg und rekonstruierte brutto daneben.
2. Sonst rekonstruiere aus Preis und Menge — und **seitenbewusst**, sonst kehrt
   sich das Vorzeichen jedes Shorts um.

``realized_pnl_usd`` ist hier nie eine Quelle: es ist portfolio-kumulativ, kein
Trade-Wert (NEO-P-101-r2).
"""

from __future__ import annotations

from typing import Any

__all__ = ["close_pnl"]


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def close_pnl(row: dict[str, Any]) -> float:
    """Per-Trade-Netto-PnL eines ``position_closed``/``position_partial_closed``.

    Rekonstruierte Werte sind brutto (vor Gebühren) — bewusst, weil der Lesepfad
    kein Gebührenmodell erfinden soll (NEO-P-106). Sie betreffen nur echte
    Alt-Zeilen ohne ``trade_pnl_usd``.
    """
    raw = row.get("trade_pnl_usd")
    if raw is not None:
        return _as_float(raw)

    entry = _as_float(row.get("entry_price"))
    exit_ = _as_float(row.get("exit_price"))
    qty = _as_float(row.get("quantity"))
    delta = exit_ - entry
    if str(row.get("position_side") or "").strip().lower() == "short":
        delta = -delta
    return delta * qty
