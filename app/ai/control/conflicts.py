"""Widersprueche zwischen Konfiguration und Messwerten (Spec §5.5) -- rein, ohne I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.ai.control.config import LiteLLMModels
from app.ai.control.workloads import ProviderActivity, WorkloadKey, WorkloadStats
from app.ai.modes import unknown_route_keys

#: Modi, in denen eine Route ueber LiteLLM laeuft und also ein Modell braucht.
_AN: Final = frozenset({"shadow", "primary", "advisory"})


@dataclass(frozen=True)
class Conflict:
    key: str
    title: str
    detail: str


def find_conflicts(
    *,
    route_modes: dict[str, str],
    models: LiteLLMModels,
    lock_matches: bool | None,
    workloads_24h: dict[WorkloadKey, WorkloadStats],
    activity: dict[str, ProviderActivity],
) -> list[Conflict]:
    funde: list[Conflict] = []
    unbekannt = set(unknown_route_keys(route_modes))
    for route in sorted(unbekannt):
        funde.append(
            Conflict(
                f"konflikt:unbekannte_route:{route}",
                f"Unbekannte Route „{route}“",
                "KAI_INFERENCE_ROUTE_MODES nennt eine Route, die es nicht gibt -- Tippfehler?",
            )
        )
    for route, modus in route_modes.items():
        if route in unbekannt or str(modus).strip().lower() not in _AN:
            continue
        if not getattr(models, f"{route}_model", ""):
            funde.append(
                Conflict(
                    f"konflikt:route_ohne_modell:{route}",
                    f"Route {route} ohne Modell",
                    f"Modus {modus}, aber KAI_LITELLM_{route.upper()}_MODEL ist leer.",
                )
            )
    if lock_matches is False:
        funde.append(
            Conflict(
                "konflikt:baum_lock",
                "LiteLLM-Baum passt nicht zum Lock",
                "Der laufende Transportbaum ist nicht der gepruefte.",
            )
        )
    je_route: dict[str, WorkloadStats] = {}
    for key, st in workloads_24h.items():
        if key.transport != "litellm":
            continue
        summe = je_route.setdefault(key.route, WorkloadStats())
        summe.calls += st.calls
        summe.ok += st.ok
        summe.fallbacks += st.fallbacks
    for route, st in sorted(je_route.items()):
        if st.calls > 0 and st.ok == 0:
            funde.append(
                Conflict(
                    f"konflikt:nur_fehler:{route}",
                    f"LiteLLM-Route {route}: nur Fehler",
                    f"{st.calls} Aufrufe in 24 h, keiner erfolgreich.",
                )
            )
        primary = str(route_modes.get(route, "")).strip().lower() == "primary"
        if primary and st.calls >= 10 and st.fallbacks / st.calls > 0.2:
            funde.append(
                Conflict(
                    f"konflikt:rueckfall:{route}",
                    f"Route {route} faellt oft zurueck",
                    f"{st.fallbacks} von {st.calls} Aufrufen auf direct zurueckgefallen.",
                )
            )
    for name, a in sorted(activity.items()):
        if a.schema_errors_24h >= 3:
            funde.append(
                Conflict(
                    f"konflikt:schemafehler:{name}",
                    f"{name}: wiederholt ungueltige Antworten",
                    f"{a.schema_errors_24h} Schema-Fehler in 24 h (z. B. Codeblock statt JSON).",
                )
            )
    return funde


__all__ = ["Conflict", "find_conflicts"]
