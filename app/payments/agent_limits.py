"""Die Agenten-Tabelle (ADR 0018 §1 Agent-Flow, §6 ``actor_limits``).

Eine Zeile in dieser Tabelle ist eine **Erlaubnis**, kein Vermerk: fehlt sie,
darf der Agent nichts. Deshalb ist jeder Fehler beim Laden ein LEERES
Ergebnis und keine Ausnahme — eine unlesbare Datei darf nicht dazu fuehren,
dass der Server nicht startet, aber sie darf erst recht nicht dazu fuehren,
dass ein Agent unbegrenzt zahlt. Beides zusammen geht nur so: leere Tabelle,
und die Regel ``actor_limits`` lehnt jeden Agenten ab.

**Warum ein leeres ``purposes`` KEINE Erlaubnis ist.** Der naheliegende
Kurzschluss waere "keine Einschraenkung = alles erlaubt". Die Regel prueft
``purpose in limits.purposes``; eine leere Menge lehnt damit alles ab, und das
ist die richtige Richtung.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.payments.policy_context import ActorLimits

logger = logging.getLogger(__name__)


def load_actor_limits(path: Path) -> dict[str, ActorLimits]:
    """Lies ``config/payment_agent_limits.json``. Jeder Zweifel ergibt ``{}``."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(
            "payment_agent_limits_unreadable",
            extra={"error_type": type(exc).__name__, "path": str(path)},
        )
        return {}
    agents = raw.get("agents") if isinstance(raw, dict) else None
    if not isinstance(agents, dict):
        return {}
    out: dict[str, ActorLimits] = {}
    for actor, entry in agents.items():
        limits = _one(str(actor), entry)
        if limits is not None:
            out[str(actor)] = limits
    return out


def _one(actor: str, entry: Any) -> ActorLimits | None:
    """Ein Eintrag — oder ``None``, wenn er nicht vollstaendig ist.

    Ein halb gelesener Eintrag waere gefaehrlicher als gar keiner: ein
    fehlendes ``daily_max_sat`` mit einem Default zu fuellen hiesse, ein
    Budget zu erfinden, das nie jemand freigegeben hat.
    """
    if not isinstance(entry, dict):
        return None
    try:
        max_amount = int(entry["max_amount_sat"])
        daily_max = int(entry["daily_max_sat"])
    except (KeyError, TypeError, ValueError):
        logger.warning("payment_agent_limits_incomplete", extra={"actor": actor})
        return None
    raw_threshold = entry.get("approval_threshold_sat")
    threshold = (
        int(raw_threshold)
        if isinstance(raw_threshold, int) and not isinstance(raw_threshold, bool)
        else None
    )
    return ActorLimits(
        actor=actor,
        max_amount_sat=max_amount,
        daily_max_sat=daily_max,
        purposes=frozenset(str(p) for p in entry.get("purposes", ()) if isinstance(p, str)),
        rails=frozenset(str(r) for r in entry.get("rails", ()) if isinstance(r, str)),
        approval_threshold_sat=threshold,
    )


__all__ = ["load_actor_limits"]
