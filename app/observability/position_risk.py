"""Read-only open-position risk snapshot.

Operator-Auftrag 2026-06-03 (Pre-Re-Enable-Blocker #3): offene Positionen
beobachtbar machen — ein deterministisches JSON/CLI-Artefakt mit
``symbol, side, size, entry, current, unrealized PnL, risk, source, age, mode``
und einem klaren Risk-Status pro Position sowie portfolio-weit:

* ``no_risk``       — kein offener Verlust jenseits der Schwelle (kein Risiko)
* ``risk_open``     — offener Verlust jenseits der Schwelle (Risiko offen)
* ``data_unknown``  — kein/stale Preis, PnL nicht bewertbar (Daten unbekannt)

Dieses Modul ist die **Detektions-Hälfte** für den Bleed-/Loss-Circuit-Breaker
(Blocker #4): es erkennt stillen Kapitalverlust, ändert aber selbst **nichts**
am Ausführungszustand. Reine Funktion über einen bereits gebauten
``PortfolioSnapshot`` — keine Markt-Calls, keine Writes, kein Eingriff in den
Entry-Pfad. ``entry_mode`` wird nur als Kontext mitgeführt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

RISK_NO = "no_risk"
RISK_OPEN = "risk_open"
RISK_UNKNOWN = "data_unknown"


def _parse_iso(ts: str | None) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _age_seconds(opened_at: str | None, now: datetime) -> float | None:
    opened = _parse_iso(opened_at)
    if opened is None:
        return None
    delta = (now - opened).total_seconds()
    return round(delta, 1) if delta >= 0 else None


def classify_position(
    pos: dict[str, Any],
    *,
    loss_threshold_pct: float,
    now: datetime,
) -> dict[str, Any]:
    """Classify a single canonical position dict (``PositionSummary.to_json_dict``)."""
    side = str(pos.get("position_side") or "long").lower()
    qty = float(pos.get("quantity") or 0.0)
    entry = float(pos.get("avg_entry_price") or 0.0)
    price = pos.get("market_price")
    available = bool(pos.get("market_data_available"))
    stale = bool(pos.get("market_data_is_stale"))

    pnl_usd: float | None = None
    pnl_pct: float | None = None
    price_f = float(price) if isinstance(price, (int, float)) else None
    priceable = available and not stale and price_f is not None and entry > 0 and qty != 0
    if priceable and price_f is not None:
        direction = -1.0 if side == "short" else 1.0
        pnl_usd = round(direction * (price_f - entry) * qty, 6)
        pnl_pct = round(direction * (price_f - entry) / entry * 100.0, 4)

    if not priceable:
        status = RISK_UNKNOWN
    elif pnl_pct is not None and pnl_pct <= -abs(loss_threshold_pct):
        status = RISK_OPEN
    else:
        status = RISK_NO

    opened_at = pos.get("opened_at")
    opened_at_str = opened_at if isinstance(opened_at, str) else None

    return {
        "symbol": pos.get("symbol"),
        "side": side,
        "size": qty,
        "entry": entry,
        "current": price,
        "unrealized_pnl_usd": pnl_usd,
        "unrealized_pnl_pct": pnl_pct,
        "stop_loss": pos.get("stop_loss"),
        "source": pos.get("source") or "",
        "opened_at": opened_at_str,
        "age_seconds": _age_seconds(opened_at_str, now),
        "market_data_stale": stale,
        "market_data_available": available,
        "risk_status": status,
    }


def build_positions_risk_snapshot(
    snapshot: Any,
    *,
    entry_mode: str,
    loss_threshold_pct: float = 1.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the full read-only risk snapshot over a ``PortfolioSnapshot``.

    ``snapshot.positions`` must be an iterable of objects exposing
    ``to_json_dict()`` (canonical ``PositionSummary``).
    """
    now = now or datetime.now(UTC)
    raw_positions = getattr(snapshot, "positions", ()) or ()
    positions = [
        classify_position(p.to_json_dict(), loss_threshold_pct=loss_threshold_pct, now=now)
        for p in raw_positions
    ]

    n_risk = sum(1 for p in positions if p["risk_status"] == RISK_OPEN)
    n_unknown = sum(1 for p in positions if p["risk_status"] == RISK_UNKNOWN)
    total_unrealized = sum(
        p["unrealized_pnl_usd"]
        for p in positions
        if isinstance(p["unrealized_pnl_usd"], (int, float))
    )

    if n_risk > 0:
        overall = RISK_OPEN
    elif n_unknown > 0:
        overall = RISK_UNKNOWN
    else:
        overall = RISK_NO

    return {
        "report_type": "open_positions_risk_snapshot",
        "generated_at": now.isoformat(),
        "entry_mode": entry_mode,
        "execution_enabled": bool(getattr(snapshot, "execution_enabled", False)),
        "available": bool(getattr(snapshot, "available", True)),
        "position_count": len(positions),
        "risk_open_count": n_risk,
        "data_unknown_count": n_unknown,
        "total_unrealized_pnl_usd": round(total_unrealized, 6),
        "overall_risk_status": overall,
        "loss_threshold_pct": loss_threshold_pct,
        "positions": positions,
    }
