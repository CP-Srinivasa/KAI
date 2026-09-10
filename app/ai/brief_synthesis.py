"""Beratende Synthese ZU einem fertigen Research-Brief.

Welches Modell antwortet, steht hier nicht und darf hier nicht stehen: der
Aufruf geht an die Route ``research``, der Anbieter ist Konfiguration.

Reihenfolge ist hier die ganze Sicherheitsaussage: der Brief wird zuerst
vollstaendig aus analysierten Dokumenten gebaut, und erst der fertige Brief
geht als Text nach draussen. Die Antwort kommt als freier Markdown zurueck und
wird angehaengt — sie veraendert keinen Score, keine Prioritaet, keinen
Signalkandidaten und keinen Alert. Faellt sie aus, ist der Brief exakt der
Brief von vorher; ein Grund wird notiert, nichts wird erfunden.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final

import httpx

from app.ai.budget import BudgetExceeded
from app.ai.config import InferenceSettings
from app.ai.modes import resolve_mode
from app.ai.research import ResearchUnavailableError, research_advisory
from app.ai.routes import route_for
from app.ai.runtime import inference_settings
from app.core.briefs import ResearchBrief

SYNTHESIS_SYSTEM_PROMPT: Final[str] = (
    "Du bist Research-Berater fuer KAI und antwortest auf Deutsch in Markdown.\n"
    "Du bekommst einen fertig gebauten Research-Brief aus bereits analysierten "
    "Dokumenten. Deine Aufgabe ist die Synthese, nicht die Wiederholung.\n\n"
    "Gliedere so:\n"
    "1. Lage in drei Saetzen\n"
    "2. Was zusammenhaengt (Verbindungen zwischen Meldungen, die einzeln nicht sichtbar sind)\n"
    "3. Widersprueche und schwache Belege\n"
    "4. Was fehlt (welche Quelle oder Zahl die Lage entscheiden wuerde)\n"
    "5. Worauf in den naechsten 24 Stunden zu achten ist\n\n"
    "Harte Regeln: Trenne Beleg, Ableitung und Vermutung sichtbar. Erfinde "
    "keine Zahl, kein Datum und keine Quelle. Gib keine Handelsempfehlung, "
    "keine Order, keine Zahlungsanweisung und keinen Alert aus — dein Text ist "
    "beratende Evidenz fuer einen Menschen, sonst nichts. Wenn der Brief zu "
    "duenn fuer eine Synthese ist, sage genau das."
)

#: Grund, wenn die Route gar nicht erst gefragt wird. Der Normalfall im Betrieb.
ROUTE_OFF_REASON: Final[str] = "research route is off"


@dataclass(frozen=True)
class BriefSynthesis:
    """Entweder Text ODER ein Grund — nie beides, nie keines von beidem."""

    content: str | None
    unavailable_reason: str | None
    provider: str | None = None
    model: str | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    correlation_id: str | None = None

    execution_authority: ClassVar[bool] = False
    analysis_result_authority: ClassVar[bool] = False
    alert_authority: ClassVar[bool] = False


def synthesis_prompt(brief: ResearchBrief) -> str:
    """Der fertige Brief, so wie ein Mensch ihn liest — kein zweiter Aufbau."""
    return (
        f"Research-Brief fuer die Watchlist '{brief.cluster_name}'. "
        "Synthetisiere ihn nach den Regeln aus der Systemanweisung.\n\n"
        f"{brief.to_markdown()}"
    )


async def synthesize_brief(
    brief: ResearchBrief,
    *,
    settings: InferenceSettings | None = None,
    correlation_id: str | None = None,
    telemetry_path: Path | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> BriefSynthesis:
    """Haenge eine beratende Synthese an — oder sage begruendet, warum nicht."""
    configured = inference_settings(settings)
    ceiling = configured.mode_ceiling if configured.enabled else "off"
    mode = resolve_mode(route_for("research"), per_route=configured.route_modes, ceiling=ceiling)
    if mode == "off":
        # Kein Transport, kein Client, keine Task: die abgeschaltete Route
        # kostet nichts und beruehrt nichts.
        return BriefSynthesis(content=None, unavailable_reason=ROUTE_OFF_REASON)

    try:
        ergebnis = await research_advisory(
            synthesis_prompt(brief),
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
            settings=configured,
            correlation_id=correlation_id,
            telemetry_path=telemetry_path,
            client_factory=client_factory,
        )
    except BudgetExceeded as exc:
        # Eine lokale KAI-Blockade, kein Anbieterausfall. Der Unterschied steht
        # im Grund, damit er spaeter nicht als Stoerung gelesen wird.
        return BriefSynthesis(content=None, unavailable_reason=f"budget blocked: {exc}")
    except ResearchUnavailableError as exc:
        return BriefSynthesis(content=None, unavailable_reason=str(exc))

    return BriefSynthesis(
        content=ergebnis.content,
        unavailable_reason=None,
        provider=ergebnis.provider,
        model=ergebnis.model,
        cost_usd=ergebnis.cost_usd,
        input_tokens=ergebnis.input_tokens,
        output_tokens=ergebnis.output_tokens,
        latency_ms=ergebnis.latency_ms,
        correlation_id=ergebnis.correlation_id,
    )


def attach_synthesis(brief: ResearchBrief, synthesis: BriefSynthesis) -> ResearchBrief:
    """Schreibe das Ergebnis in den Brief. Genau eine Stelle tut das."""
    if synthesis.unavailable_reason == ROUTE_OFF_REASON:
        # Die abgeschaltete Route ist der Normalzustand im Betrieb. Sie in jeden
        # Brief zu schreiben waere Rauschen und kein Befund — der Brief sieht
        # dann exakt so aus wie vor dieser Aenderung.
        return brief
    brief.advisory_synthesis = synthesis.content
    brief.advisory_synthesis_unavailable = synthesis.unavailable_reason
    brief.advisory_synthesis_model = synthesis.model
    brief.advisory_synthesis_cost_usd = synthesis.cost_usd
    return brief


__all__ = [
    "ROUTE_OFF_REASON",
    "SYNTHESIS_SYSTEM_PROMPT",
    "BriefSynthesis",
    "attach_synthesis",
    "synthesis_prompt",
    "synthesize_brief",
]
