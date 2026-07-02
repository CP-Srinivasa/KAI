"""decision_id → realized outcome (0/1) — JOIN über Loop- + Execution-Audit.

Ziel: aus den heute schon geschriebenen JSONL-Audits eine
``Mapping[decision_id, 0|1]`` produzieren, die die Calibration-Pipeline
direkt verarbeiten kann — *ohne* Schema-Änderung an den existierenden
Audits.

JOIN-Pfad:

    trading_loop_audit.jsonl       paper_execution_audit.jsonl
    ------------------------       ----------------------------
    decision_id (Signal)    ─┐     order_id        (Fill-Trace)
    order_id    (Order)     ─┴───→ trade_pnl_usd   (per close-Fill)
    fill_simulated=True            event ∈ {position_closed,
                                            position_partial_closed}

Aggregation:
  - Summiere ``trade_pnl_usd`` pro ``order_id`` (mehrere Tier-Closes).
  - Mappe ``order_id → decision_id`` über das Loop-Audit.
  - Win/Loss-Schwelle: cumulative_pnl > 0 ⇒ 1, sonst 0.
  - Offene Positionen (kein close-Event) ⇒ Mapping fehlt → wird vom
    Calibration-Loader als "unresolved" übersprungen (kein Bias).

Vertrag:
  - Pure read-only JSONL-Parser.  Fehlerhafte Zeilen werden geloggt +
    übersprungen (Audit darf das Lernen nicht blocken).
  - Pfade existieren nicht ⇒ leere Map (kein Throw).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# Events im Paper-Execution-Audit, die einen realisierten PnL-Beitrag liefern.
_CLOSE_EVENTS: Final[frozenset[str]] = frozenset({"position_closed", "position_partial_closed"})


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "[outcome-link] skipped malformed row %s:%d (%s)", path, line_no, exc
                    )
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError as exc:
        logger.warning("[outcome-link] read failed for %s: %s", path, exc)
        return


def _build_order_to_decision(loop_audit_path: Path) -> dict[str, str]:
    """``order_id → decision_id`` aus den Loop-Audit-Zeilen mit fill_simulated."""
    out: dict[str, str] = {}
    for row in _iter_jsonl(loop_audit_path):
        if not row.get("fill_simulated"):
            continue
        order_id = row.get("order_id")
        decision_id = row.get("decision_id")
        if isinstance(order_id, str) and isinstance(decision_id, str):
            # Falls eine order_id mehrfach erscheint, gewinnt die *erste*
            # decision_id — beide gehören zum selben fill-Event.
            out.setdefault(order_id, decision_id)
    return out


def _build_pnl_per_order(exec_audit_path: Path) -> dict[str, float]:
    """``order_id → cumulative trade_pnl_usd`` aus close-Events."""
    out: dict[str, float] = {}
    for row in _iter_jsonl(exec_audit_path):
        # Schema-v2 hat 'event' am top-level + Payload eingebettet — wir
        # akzeptieren beides (nested 'data'/'payload' oder flat).
        event = row.get("event") or row.get("event_type")
        payload: Mapping[str, object] = row
        for key in ("payload", "data"):
            if isinstance(row.get(key), dict):
                payload = row[key]
                event = event or payload.get("event") or payload.get("event_type")
                break
        if event not in _CLOSE_EVENTS:
            continue
        order_id = payload.get("order_id") or row.get("order_id")
        pnl = payload.get("trade_pnl_usd")
        if not isinstance(order_id, str):
            continue
        if not isinstance(pnl, (int, float)):
            continue
        out[order_id] = out.get(order_id, 0.0) + float(pnl)
    return out


def build_outcome_map_from_audit(
    *,
    loop_audit_path: Path | str,
    exec_audit_path: Path | str,
) -> dict[str, int]:
    """Liefere ``{decision_id: 0|1}`` für alle vollständig realisierten Trades.

    Ein Trade gilt als realisiert, sobald mindestens ein
    ``position_(partial_)closed``-Event mit ``trade_pnl_usd`` für die
    zugehörige ``order_id`` existiert.  Cumulative_pnl > 0 → 1.

    Operator-Hinweis: für strikte Win-Rate (alle Tier-Closes inkl.
    Fee-Saldo positiv) reicht das aktuelle Schema;  ein eventuell
    späterer "is_winning_trade"-Flag im Execution-Audit könnte das
    granularer machen, ist aber nicht erforderlich.
    """
    order_to_decision = _build_order_to_decision(Path(loop_audit_path))
    pnl_per_order = _build_pnl_per_order(Path(exec_audit_path))

    outcomes: dict[str, int] = {}
    for order_id, pnl in pnl_per_order.items():
        decision_id = order_to_decision.get(order_id)
        if decision_id is None:
            # Trade ohne korrespondierende Loop-Cycle → vermutlich
            # operator-manueller Eingriff; ohne decision_id nicht für
            # die Calibration nutzbar.
            continue
        outcomes[decision_id] = 1 if pnl > 0 else 0
    return outcomes


__all__ = ["build_outcome_map_from_audit"]
