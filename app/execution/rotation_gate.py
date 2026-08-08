"""Rotation-Gate (Plan 08-08, PR-4): route-aware Open-Guard gegen ``archived``.

Kontext: Die Asset-Rotation (G1) bewertet täglich sauber, hatte aber NULL
Konsumenten im Handelspfad — Epochen-Trades auf bereits-bei-Entry-archivierten
Symbolen trugen −594 USD (n=95), während das System es wusste. Dieses Modul
ist der fehlende Konsument, bewusst dreistufig und route-scoped:

* ``off`` (Default) — Gate existiert nicht; Null-Verhaltensänderung beim Deploy.
* ``shadow`` — nichts wird geblockt; jede archived-Öffnung erzeugt ein
  ``rotation_gate_would_block``-Audit-Event (Counterfactual-Zählung für die
  Prä-Reg ``rotation_gated_universe_v1``, Phase F).
* ``enforce`` — blockt Öffnungen auf ``archived``-Symbolen, aber NUR für
  Routen in ``asset_rotation_gate_routes``. **H1/H2-Doktrin:** die Prä-Regs
  ``fd6f5f7842f49244``/``0c7ead764621dd17`` messen die versiegelte
  ``technical_paper``-Population — diese Route darf bis zu deren Abschluss
  NIE im Enforce-Scope stehen (Zweig-A-Entscheid des Operators 08-08).

Fail-open-Grundsätze: fehlender/korrupter State blockt nie; eine leere oder
unbekannte ``source`` blockt nie (``rotation_gate_unattributed``-Event, nur
sichtbar wenn das Symbol archived ist — kein Rauschen auf gesunden Symbolen).
Closes laufen IMMER (der Aufrufer wendet das Gate nur auf Öffnungen an).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.execution.entry_policy import ROUTE_SOURCE_PREFIXES, EntryRoute

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("artifacts/asset_rotation_state.json")

# (mtime, statuses) — der State ändert sich einmal täglich (Shadow-Timer);
# ein mtime-Cache erspart dem Fill-Pfad das Re-Parsen pro Order.
_cache: dict[str, tuple[float, dict[str, str]]] = {}


def resolve_entry_route(source: str) -> EntryRoute | None:
    """Kanonische Route aus ``PaperOrder.source`` — nie raten.

    Nutzt das bestehende ``ROUTE_SOURCE_PREFIXES``-Mapping (entry_policy) plus
    den Loop-Strom ``autonomous_generator`` → AUTONOMOUS_LOOP. Leere oder
    unbekannte Quelle ⇒ ``None`` (Aufrufer behandelt das fail-open).
    """
    src = (source or "").strip().lower()
    if not src:
        return None
    if src.startswith("autonomous"):
        return EntryRoute.AUTONOMOUS_LOOP
    for route, prefixes in ROUTE_SOURCE_PREFIXES.items():
        if any(src.startswith(p) for p in prefixes):
            return route
    return None


def _load_statuses(state_path: Path) -> dict[str, str]:
    """Symbol→Status aus dem Rotation-State; fail-open ({}) bei fehlend/korrupt."""
    key = str(state_path)
    try:
        mtime = state_path.stat().st_mtime
    except OSError:
        return {}
    cached = _cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Korrupter State ist ein Rotations-Problem, kein Handels-Stopp:
        # fail-open, der Shadow-Lauf/Health-Check meldet die Wurzel.
        return {}
    statuses: dict[str, str] = {}
    if isinstance(raw, dict):
        for symbol, entry in raw.items():
            if isinstance(entry, dict) and isinstance(entry.get("status"), str):
                statuses[str(symbol)] = entry["status"]
    _cache[key] = (mtime, statuses)
    return statuses


def parse_gate_routes(routes_csv: str) -> frozenset[str]:
    """CSV → normalisierte Routen-Werte (EntryRoute.value-Strings)."""
    return frozenset(p.strip().lower() for p in (routes_csv or "").split(",") if p.strip())


@dataclass(frozen=True)
class RotationGateDecision:
    """Ergebnis für EINE Öffnung. ``action``: pass | would_block | block |
    unattributed. Nur ``block`` verhindert den Fill."""

    action: str
    symbol: str
    status: str | None
    route: str | None
    mode: str

    @property
    def audit_event(self) -> str:
        return f"rotation_gate_{self.action}"


def evaluate_rotation_gate(
    symbol: str,
    source: str,
    *,
    mode: str,
    routes_csv: str,
    state_path: Path | None = None,
) -> RotationGateDecision:
    """Entscheidung für eine Öffnung (pure bis auf den gecachten State-Read).

    ``state_path=None`` löst zur CALL-Zeit gegen ``DEFAULT_STATE_PATH`` auf
    (testbar via monkeypatch — ein def-Zeit-Default wäre eingefroren).
    """
    if mode not in ("shadow", "enforce"):
        return RotationGateDecision("pass", symbol, None, None, mode)
    status = _load_statuses(state_path or DEFAULT_STATE_PATH).get(symbol)
    if status != "archived":
        # Nur 'archived' verliert das Open-Recht; probation/flagged sammeln
        # weiter Evidenz (sonst könnte sich nichts je rehabilitieren).
        return RotationGateDecision("pass", symbol, status, None, mode)
    route = resolve_entry_route(source)
    if route is None:
        # Unattribuierte Quelle: nie blocken, aber sichtbar zählen.
        return RotationGateDecision("unattributed", symbol, status, None, mode)
    in_scope = route.value in parse_gate_routes(routes_csv)
    if mode == "enforce" and in_scope:
        return RotationGateDecision("block", symbol, status, route.value, mode)
    # shadow-Modus ODER Route außerhalb des Enforce-Scopes (z. B. die
    # H1-versiegelte technical_paper-Route): Counterfactual-Event, Fill läuft.
    return RotationGateDecision("would_block", symbol, status, route.value, mode)


__all__ = [
    "DEFAULT_STATE_PATH",
    "RotationGateDecision",
    "evaluate_rotation_gate",
    "parse_gate_routes",
    "resolve_entry_route",
]
