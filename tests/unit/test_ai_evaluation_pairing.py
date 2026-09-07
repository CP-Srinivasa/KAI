"""Eine Auswertung, zwei Seiten, ein Schluessel.

Ohne diesen Schluessel ist die Shadow-Telemetrie unauswertbar, und zwar auf
eine Weise, die niemandem auffaellt: die Zeilen SIND da, sie sehen vollstaendig
aus, und trotzdem laesst sich hinterher nicht sagen, welche DIRECT-Zeile und
welche SHADOW-Zeile denselben Aufruf beschreiben.

Zwei Kandidaten waren vorhanden und beide untauglich:

* ``call_id`` wird pro ZEILE vergeben -- fuer jeden physischen Versuch und fuer
  jede Seite eine eigene. Als Paarungsschluessel ist sie das Gegenteil dessen,
  was gebraucht wird.
* ``correlation_id`` haelt eine ganze KETTE zusammen. Ein Aufrufer, der sie
  durchreicht, haette darunter mehrere Auswertungen; zwei Auswertungen mit je
  einer DIRECT- und einer SHADOW-Seite saehen aus wie eine Auswertung mit zwei
  Duplikaten.

Dazu kam ein zweiter, unabhaengiger Defekt: der Direktpfad wurde von den
Aufrufern instrumentiert, lange bevor es eine Control-Plane gab. Seine
Telemetriezeile kannte ihre eigene Route nicht. Eine Auswertung, die Routen
vergleicht, wirft eine Zeile ohne Route weg -- und behaelt die SHADOW-Seite
allein zurueck.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.ai.audit import (
    current_evaluation_id,
    evaluation_scope,
    llm_call_scope,
    record_attempt_trace,
)
from app.ai.models import AttemptTrace


def _zeilen(pfad: Path) -> list[dict[str, Any]]:
    if not pfad.exists():
        return []
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]


# ---------------------------------------------------------------------------
# Der Kontext selbst.
# ---------------------------------------------------------------------------


def test_ausserhalb_einer_auswertung_gibt_es_keine_id() -> None:
    """Eine erfundene Id waere schlimmer als keine."""
    assert current_evaluation_id() is None


def test_die_auswertung_bindet_und_gibt_wieder_frei() -> None:
    with evaluation_scope(logical_route="standard", mode="shadow") as ident:
        assert ident.startswith("eval_")
        assert current_evaluation_id() == ident
    assert current_evaluation_id() is None, "eine inline erwartete Pipeline vererbt nichts"


def test_verschachtelte_auswertungen_stellen_den_vorigen_zustand_wieder_her() -> None:
    with evaluation_scope(logical_route="standard", mode="shadow") as aussen:
        with evaluation_scope(logical_route="reasoning", mode="shadow") as innen:
            assert innen != aussen
            assert current_evaluation_id() == innen
        assert current_evaluation_id() == aussen


def test_eine_vorgegebene_id_wird_respektiert() -> None:
    with evaluation_scope(logical_route="standard", mode="shadow", evaluation_id="eval_fix") as i:
        assert i == "eval_fix"


# ---------------------------------------------------------------------------
# Der Altpfad erbt die Zuordnung, ohne dass ein Aufrufer sich aendert.
# ---------------------------------------------------------------------------


async def test_der_direktpfad_traegt_route_und_auswertung_aus_dem_kontext(
    tmp_path: Path,
) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    with evaluation_scope(logical_route="standard", mode="shadow") as ident:
        async with llm_call_scope(
            purpose="analysis", provider="openai", model="gpt-4o", path=pfad
        ) as scope:
            scope.set_tokens(11, 5)

    (zeile,) = _zeilen(pfad)
    assert zeile["evaluation_id"] == ident
    assert zeile["logical_route"] == "standard", "ohne Route wirft die Auswertung die Zeile weg"
    assert zeile["mode"] == "shadow"
    assert zeile["transport"] == "direct"


async def test_auch_der_fehlerfall_traegt_die_zuordnung(tmp_path: Path) -> None:
    """Gerade die gescheiterte Seite muss zuordenbar bleiben."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    with evaluation_scope(logical_route="critical", mode="shadow") as ident:
        with pytest.raises(TimeoutError):
            async with llm_call_scope(
                purpose="intent", provider="openai", model="gpt-4o", path=pfad
            ):
                raise TimeoutError("upstream")

    (zeile,) = _zeilen(pfad)
    assert zeile["evaluation_id"] == ident
    assert zeile["logical_route"] == "critical"
    assert zeile["ok"] is False


async def test_ohne_auswertung_bleibt_der_altpfad_exakt_wie_er_war(tmp_path: Path) -> None:
    """OFF ist der Rollback: dort entsteht keine Auswertung und keine Id."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    async with llm_call_scope(purpose="chat", provider="openai", model="gpt-4o", path=pfad):
        pass

    (zeile,) = _zeilen(pfad)
    assert zeile["evaluation_id"] is None
    assert zeile["logical_route"] is None
    assert zeile["mode"] is None


# ---------------------------------------------------------------------------
# Beide Seiten unter einem Schluessel.
# ---------------------------------------------------------------------------


async def test_beide_seiten_derselben_auswertung_teilen_die_id(tmp_path: Path) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    with evaluation_scope(logical_route="standard", mode="shadow") as ident:
        record_attempt_trace(
            AttemptTrace(
                transport="litellm",
                requested_model="kai-standard",
                latency_ms=12.0,
                actual_provider="openai",
                actual_model="gpt-4o-mini",
                cost_usd=0.001,
            ),
            correlation_id="corr-1",
            purpose="analysis",
            logical_route="standard",
            mode="shadow",
            role="shadow",
            attempt_number=1,
            budget_decision="allow",
            circuit_state="closed",
            execution_authority=False,
            schema_status="valid",
            outcome="success",
            path=pfad,
        )
        async with llm_call_scope(purpose="analysis", provider="openai", model="gpt-4o", path=pfad):
            pass

    schatten, direkt = _zeilen(pfad)
    assert schatten["evaluation_id"] == direkt["evaluation_id"] == ident
    assert schatten["transport"] == "litellm"
    assert direkt["transport"] == "direct"
    assert schatten["call_id"] != direkt["call_id"], "die call_id beschreibt eine ZEILE"


async def test_zwei_auswertungen_unter_einer_correlation_bleiben_getrennt(
    tmp_path: Path,
) -> None:
    """Sonst saehen zwei Aufrufe aus wie ein Aufruf mit Duplikaten."""
    from app.ai.audit import correlation_scope

    pfad = tmp_path / "llm_telemetry.jsonl"
    ids: list[str] = []
    with correlation_scope("corr-geteilt"):
        for _ in range(2):
            with evaluation_scope(logical_route="standard", mode="shadow") as ident:
                ids.append(ident)
                async with llm_call_scope(
                    purpose="analysis", provider="openai", model="gpt-4o", path=pfad
                ):
                    pass

    zeilen = _zeilen(pfad)
    assert len({z["correlation_id"] for z in zeilen}) == 1, "eine Kette"
    assert len({z["evaluation_id"] for z in zeilen}) == 2, "zwei Auswertungen"
    assert ids[0] != ids[1]


# ---------------------------------------------------------------------------
# Der ganze Weg: ein echter SHADOW-Aufruf hinterlaesst ein Paar.
# ---------------------------------------------------------------------------


async def test_ein_shadow_aufruf_hinterlaesst_zwei_zuordenbare_zeilen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Befund, gegen den diese Datei steht — an der echten Kette gemessen."""
    from unittest.mock import AsyncMock

    from app.ai.config import InferenceSettings
    from app.ai.runtime import LiteLLMRequest, invoke
    from app.integrations.litellm.provider import LiteLLMResponse

    pfad = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr(
        "app.ai.runtime.call_litellm_async",
        AsyncMock(
            return_value=LiteLLMResponse(
                trace=AttemptTrace(
                    transport="litellm",
                    requested_model="kai-standard",
                    latency_ms=12.0,
                    actual_provider="openai",
                    actual_model="gpt-4o-mini",
                    cost_usd=0.001,
                ),
                body={"choices": [{"message": {"content": "schatten"}}]},
            )
        ),
    )

    async def direct_call() -> str:
        async with llm_call_scope(
            purpose="analysis", provider="openai", model="gpt-4o", path=pfad
        ) as scope:
            scope.set_tokens(11, 5)
            return "direkt"

    ergebnis = await invoke(
        purpose="analysis",
        direct_call=direct_call,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=LiteLLMRequest(parser=lambda body: body["choices"][0]["message"]["content"]),
        settings=InferenceSettings(
            enabled=True,
            mode_ceiling="shadow",
            route_modes={"standard": "shadow"},
            max_attempts=1,
        ),
        telemetry_path=pfad,
    )

    assert ergebnis.value == "direkt", "SHADOW ersetzt die Antwort nicht"
    zeilen = _zeilen(pfad)
    assert len(zeilen) == 2
    assert len({z["evaluation_id"] for z in zeilen}) == 1, "eine Auswertung, ein Schluessel"
    assert {z["transport"] for z in zeilen} == {"litellm", "direct"}
    assert {z["logical_route"] for z in zeilen} == {"standard"}, "beide Seiten kennen die Route"
    assert all(z["evaluation_id"] is not None for z in zeilen)


async def test_off_hinterlaesst_keine_auswertungs_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.config import InferenceSettings
    from app.ai.runtime import invoke

    pfad = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr(
        "app.ai.runtime.call_litellm_async",
        lambda **_: (_ for _ in ()).throw(AssertionError("OFF ruft nicht")),
    )

    async def direct_call() -> str:
        async with llm_call_scope(purpose="analysis", provider="openai", model="gpt-4o", path=pfad):
            return "direkt"

    ergebnis = await invoke(
        purpose="analysis",
        direct_call=direct_call,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=None,
        settings=InferenceSettings(enabled=False, mode_ceiling="off", route_modes={}),
        telemetry_path=pfad,
    )

    assert ergebnis.value == "direkt"
    (zeile,) = _zeilen(pfad)
    assert zeile["evaluation_id"] is None, "OFF ist der Altpfad, unveraendert"


def test_die_lauf_id_taucht_nicht_in_zwei_stroemen_auf() -> None:
    """Ein zweiter Strom waere die naechste Wahrheit, die driftet."""
    from app.observability.llm_telemetry import DEFAULT_TELEMETRY_PATH

    assert DEFAULT_TELEMETRY_PATH == Path("artifacts/llm_telemetry.jsonl")


def test_das_feld_ist_additiv_und_bricht_keinen_alten_aufrufer(tmp_path: Path) -> None:
    """Jeder bestehende Aufruf bleibt gueltig und schreibt schlicht ``null``."""
    from app.observability.llm_telemetry import record_llm_call

    pfad = tmp_path / "llm_telemetry.jsonl"
    record_llm_call(provider="openai", model="gpt-4o", ok=True, latency_ms=1.0, path=pfad)

    (zeile,) = _zeilen(pfad)
    assert "evaluation_id" in zeile
    assert zeile["evaluation_id"] is None


def test_die_auswertung_ueberlebt_nebenlaeufige_aufrufe() -> None:
    """ContextVars sind pro Task -- sonst vermischten sich parallele Auswertungen."""

    async def eine(route: str) -> str:
        with evaluation_scope(logical_route=route, mode="shadow") as ident:
            await asyncio.sleep(0)
            assert current_evaluation_id() == ident
            return ident

    async def alle() -> list[str]:
        return await asyncio.gather(*(eine(r) for r in ("standard", "critical", "bulk")))

    ids = asyncio.run(alle())
    assert len(set(ids)) == 3
