"""Die Synthese haengt AM Brief, sie baut ihn nicht und bewertet ihn nicht.

Der Brief wird zuerst fertig gebaut. Erst danach wird Text angehaengt. Faellt
die Synthese aus, ist der Brief unveraendert derselbe wie ohne sie — das ist
die ganze Zusicherung, und sie wird hier gemessen, nicht behauptet.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.ai.brief_synthesis import (
    ROUTE_OFF_REASON,
    SYNTHESIS_SYSTEM_PROMPT,
    BriefSynthesis,
    attach_synthesis,
    synthesize_brief,
)
from app.ai.config import InferenceSettings
from app.core.briefs import ResearchBrief


def _settings(**overrides: object) -> InferenceSettings:
    base: dict[str, object] = {
        "enabled": True,
        "mode_ceiling": "advisory",
        "route_modes": {"research": "advisory"},
        "litellm_api_key": "test-master-key",
        "max_attempts": 1,
    }
    base.update(overrides)
    return InferenceSettings(**base)  # type: ignore[arg-type]


def _brief() -> ResearchBrief:
    return ResearchBrief(
        cluster_name="layer1",
        title="Research Brief: layer1",
        summary="14 Dokumente, Stimmung gemischt.",
        generated_at=datetime(2026, 9, 10, 20, 0, tzinfo=UTC),
        data_state="current",
        document_count=14,
        average_priority=5.5,
        overall_sentiment="neutral",
        top_documents=[],
        top_assets=[],
        top_entities=[],
        top_actionable_signals=[],
        key_documents=[],
    )


def _client_factory(handler: httpx.MockTransport) -> Callable[..., httpx.AsyncClient]:
    def build(*, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=handler, timeout=timeout)

    return build


def _ok_response(text: str = "## Lage\n\nKurz und brauchbar.") -> Callable[..., httpx.Response]:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "x-litellm-model-name": "moonshot/kimi-k2.6",
                "x-litellm-response-cost": "0.0042",
            },
            json={
                "model": "kai-kimi-research",
                "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 310},
            },
        )

    return handle


@pytest.mark.asyncio
async def test_die_synthese_haengt_am_brief_und_traegt_ihre_herkunft(tmp_path: Path) -> None:
    gesendet: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        gesendet.update(json.loads(request.content))
        return _ok_response()(request)

    telemetrie = tmp_path / "llm.jsonl"
    brief = _brief()
    markdown_vorher = brief.to_markdown()

    ergebnis = await synthesize_brief(
        brief,
        settings=_settings(),
        correlation_id="brief-layer1",
        telemetry_path=telemetrie,
        client_factory=_client_factory(httpx.MockTransport(handle)),
    )

    assert isinstance(ergebnis, BriefSynthesis)
    assert ergebnis.content == "## Lage\n\nKurz und brauchbar."
    assert ergebnis.unavailable_reason is None
    assert ergebnis.model == "moonshot/kimi-k2.6"
    assert ergebnis.cost_usd == pytest.approx(0.0042)
    assert not ergebnis.execution_authority
    assert not ergebnis.alert_authority

    nachrichten = gesendet["messages"]
    assert isinstance(nachrichten, list)
    assert nachrichten[0] == {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT}
    # Der Brief geht so hinein, wie er herauskommt - kein zweiter Aufbau.
    assert markdown_vorher in str(nachrichten[1]["content"])

    zeile = json.loads(telemetrie.read_text(encoding="utf-8").splitlines()[0])
    assert zeile["logical_route"] == "research"
    assert zeile["role"] == "advisory"
    assert zeile["execution_authority"] is False
    assert zeile["use_case"] == "research"


@pytest.mark.asyncio
async def test_der_brief_ueberlebt_den_ausfall_der_synthese_unveraendert() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "moonshot down"})

    brief = _brief()
    vorher = brief.to_markdown()

    ergebnis = await synthesize_brief(
        brief,
        settings=_settings(),
        client_factory=_client_factory(httpx.MockTransport(handle)),
    )

    assert ergebnis.content is None
    assert ergebnis.unavailable_reason
    assert brief.to_markdown() == vorher


@pytest.mark.asyncio
async def test_ohne_aktivierte_route_wird_gar_nicht_erst_gefragt() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - darf nie laufen
        raise AssertionError("Die abgeschaltete Route hat einen Transport angefasst.")

    ergebnis = await synthesize_brief(
        _brief(),
        settings=InferenceSettings(),
        client_factory=_client_factory(httpx.MockTransport(handle)),
    )

    assert ergebnis.content is None
    assert ergebnis.unavailable_reason == "research route is off"


def test_die_angehaengte_synthese_ist_im_markdown_als_beratend_markiert() -> None:
    brief = _brief()
    brief.advisory_synthesis = "## Lage\n\nKurz und brauchbar."
    brief.advisory_synthesis_model = "moonshot/kimi-k2.6"
    brief.advisory_synthesis_cost_usd = 0.0042

    markdown = brief.to_markdown()

    assert "## Advisory Research Synthesis" in markdown
    assert "moonshot/kimi-k2.6" in markdown
    assert "kein Signal, kein Alert, keine Ausfuehrung" in markdown
    assert "Kurz und brauchbar." in markdown
    # Die Synthese steht HINTER den Dokumenten, nicht vor ihnen.
    assert markdown.index("## Top Documents") < markdown.index("## Advisory Research Synthesis")


def test_ein_brief_ohne_synthese_sieht_aus_wie_bisher() -> None:
    markdown = _brief().to_markdown()

    assert "Advisory Research Synthesis" not in markdown


def test_der_ausfallgrund_steht_im_brief_statt_einer_erfundenen_synthese() -> None:
    brief = _brief()
    brief.advisory_synthesis_unavailable = "research route is off"

    markdown = brief.to_markdown()

    assert "## Advisory Research Synthesis" in markdown
    assert "research route is off" in markdown
    assert "*Nicht erhoben.*" in markdown


def test_die_abgeschaltete_route_hinterlaesst_keine_spur_im_brief() -> None:
    """Der Normalbetrieb ist OFF — ein Brief darf davon nichts merken."""
    brief = _brief()
    vorher = brief.to_markdown()

    attach_synthesis(brief, BriefSynthesis(content=None, unavailable_reason=ROUTE_OFF_REASON))

    assert brief.advisory_synthesis is None
    assert brief.advisory_synthesis_unavailable is None
    assert brief.to_markdown() == vorher


def test_ein_echter_ausfall_wird_dagegen_sichtbar() -> None:
    brief = _brief()

    attach_synthesis(brief, BriefSynthesis(content=None, unavailable_reason="moonshot down"))

    assert "moonshot down" in brief.to_markdown()


def test_der_http_endpunkt_zahlt_nicht_von_allein() -> None:
    """Ein GET, der bei jedem Aufruf Geld kostet, waere ein Defekt.

    Der Endpunkt wird von Oberflaechen und Agenten wiederholt gezogen. Die
    Synthese zahlt aus demselben Topf wie die Nachrichtenanalyse, also ist sie
    dort anzufordern und nicht abzubestellen. Auf der CLI ist jeder Aufruf eine
    Operatorhandlung — dort ist die Voreinstellung umgekehrt.
    """
    import inspect

    from app.api.routers.research import get_research_brief
    from app.cli.commands.research_core import research_brief

    assert inspect.signature(get_research_brief).parameters["synthesis"].default is False
    # Typer verpackt die Voreinstellung in ein OptionInfo.
    assert inspect.signature(research_brief).parameters["synthesis"].default.default is True
