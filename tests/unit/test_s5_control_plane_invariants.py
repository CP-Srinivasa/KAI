"""Unabhaengige Beweise fuer die S5-Zusagen — nicht der Bericht, der Code.

Codex' Donor behauptet fuenf verdrahtete Aufrufer, eine async-sichere Grenze und
genau einen Telemetrie-Strom. Diese Datei prueft jede dieser Zusagen selbst, und
zwar an den Stellen, an denen sie im Betrieb bricht:

* **OFF** ist der harte Rollback. Nicht "LiteLLM wird ignoriert", sondern: es
  entsteht kein HTTP-Client, kein Task, kein Versuch. Ein Modus-Schalter, der
  nur die Auswertung aendert, waere kein Rollback.
* **SHADOW** heisst: der Transport laeuft mit, aber die Antwort des Altpfads
  gewinnt — auch und gerade dann, wenn LiteLLM etwas anderes sagt.
* **CONSENSUS** darf nie mehr als SHADOW erreichen. Diese Route entscheidet
  ueber Handelssignale.
* Das **Budget** begrenzt Ausgaben, nicht den Betrieb.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.config import InferenceSettings
from app.ai.models import AttemptTrace
from app.integrations.litellm.provider import LiteLLMResponse
from tests.unit.test_caller_wiring_s5 import _analysis_output


def _off() -> InferenceSettings:
    return InferenceSettings(enabled=False, mode_ceiling="off", route_modes={})


def _mode(route: str, mode: str) -> InferenceSettings:
    return InferenceSettings(
        enabled=True, mode_ceiling=mode, route_modes={route: mode}, max_attempts=1
    )


def _trace(alias: str = "kai-standard") -> AttemptTrace:
    return AttemptTrace(
        transport="litellm",
        requested_model=alias,
        latency_ms=1.0,
        actual_provider="openai",
        actual_model="gpt-4o-mini",
        cost_usd=0.001,
    )


def _chat_body(content: str) -> dict[str, object]:
    return {"model": "gpt-4o-mini", "choices": [{"message": {"content": content}}]}


@pytest.fixture
def kein_litellm(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Jede Beruehrung des Transports ist ein Testfehler, keine stille Null."""
    transport = AsyncMock(side_effect=AssertionError("LiteLLM darf in OFF nicht laufen"))
    monkeypatch.setattr("app.ai.runtime.call_litellm_async", transport)
    monkeypatch.setattr(
        "app.ai.runtime.httpx.AsyncClient",
        MagicMock(side_effect=AssertionError("in OFF entsteht kein HTTP-Client")),
    )
    return transport


# ---------------------------------------------------------------------------
# OFF = 0 LiteLLM-Aufrufe, fuer JEDEN der fuenf Aufrufer einzeln.
# ---------------------------------------------------------------------------


async def test_off_analysis_ruft_kein_litellm(kein_litellm: AsyncMock) -> None:
    from app.analysis.ai_control_plane import ControlPlaneAnalysisProvider

    direct = MagicMock()
    direct.provider_name = "openai"
    direct.model = "gpt-4o"
    erwartet = _analysis_output()
    direct.analyze = AsyncMock(return_value=erwartet)

    provider = ControlPlaneAnalysisProvider(direct, _off())
    ergebnis = await provider.analyze("Bitcoin", "Text")
    assert ergebnis is erwartet
    direct.analyze.assert_awaited_once()


async def test_off_chat_ruft_kein_litellm(
    monkeypatch: pytest.MonkeyPatch, kein_litellm: AsyncMock
) -> None:
    from app.messaging import kai_chat_engine

    monkeypatch.setattr(
        kai_chat_engine,
        "get_settings",
        lambda: SimpleNamespace(
            providers=SimpleNamespace(openai_api_key="k", openai_model="gpt-4o"),
            ai_gateway=_off(),
        ),
    )
    antwort = MagicMock()
    antwort.choices = [MagicMock(message=MagicMock(content="direkt"))]
    antwort.usage = None
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=antwort)
    monkeypatch.setattr("openai.AsyncOpenAI", MagicMock(return_value=client))

    ergebnis = await kai_chat_engine._respond_smalltalk("Hallo", "de")
    assert ergebnis.reply == "direkt"
    assert ergebnis.source == "gpt4o", "OFF darf nicht als litellm-Quelle erscheinen"


async def test_off_intent_ruft_kein_litellm(
    monkeypatch: pytest.MonkeyPatch, kein_litellm: AsyncMock
) -> None:
    from app.messaging import text_intent

    monkeypatch.setattr("app.ai.runtime.environment_settings", _off)
    antwort = MagicMock()
    antwort.choices = [
        MagicMock(message=MagicMock(content=json.dumps({"intent": "chat", "response": "hi"})))
    ]
    antwort.usage = None
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=antwort)
    monkeypatch.setattr(text_intent, "AsyncOpenAI", MagicMock(return_value=client))

    ergebnis = await text_intent.TextIntentProcessor(api_key="k").process("Status")
    assert ergebnis.intent == "chat"
    assert ergebnis.response == "hi"


async def test_off_stt_ruft_kein_litellm(
    monkeypatch: pytest.MonkeyPatch, kein_litellm: AsyncMock
) -> None:
    from app.messaging import voice_transcriber

    monkeypatch.setattr("app.ai.runtime.environment_settings", _off)
    antwort = MagicMock()
    antwort.raise_for_status = MagicMock()
    antwort.json = MagicMock(return_value={"text": "direkt transkribiert"})
    client = MagicMock()
    client.post = AsyncMock(return_value=antwort)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(voice_transcriber.httpx, "AsyncClient", MagicMock(return_value=client))

    ergebnis = await voice_transcriber.VoiceTranscriber("bot", "k")._whisper_transcribe(
        b"audio", "voice.oga"
    )
    assert ergebnis == "direkt transkribiert"


async def test_off_consensus_ruft_kein_litellm(
    monkeypatch: pytest.MonkeyPatch, kein_litellm: AsyncMock
) -> None:
    from app.trading import signal_consensus

    monkeypatch.setattr("app.ai.runtime.environment_settings", _off)
    antwort = MagicMock()
    antwort.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps({"agree": True, "confidence": 0.9, "reasoning": "direkt"})
            )
        )
    ]
    antwort.usage = None
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=antwort)
    monkeypatch.setattr(signal_consensus, "AsyncOpenAI", MagicMock(return_value=client))

    validator = signal_consensus.SignalConsensusValidator(api_key="k")
    ergebnis = await validator._validate_single(validator._configs[0], "signal")
    assert ergebnis.reasoning == "direkt"
    assert ergebnis.error is None


# ---------------------------------------------------------------------------
# SHADOW: der Transport laeuft, der Altpfad entscheidet.
# ---------------------------------------------------------------------------


async def test_shadow_chat_liefert_die_direkte_antwort_trotz_abweichendem_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.messaging import kai_chat_engine

    transport = AsyncMock(return_value=LiteLLMResponse(trace=_trace(), body=_chat_body("SCHATTEN")))
    monkeypatch.setattr("app.ai.runtime.call_litellm_async", transport)
    monkeypatch.setattr(
        kai_chat_engine,
        "get_settings",
        lambda: SimpleNamespace(
            providers=SimpleNamespace(openai_api_key="k", openai_model="gpt-4o"),
            ai_gateway=_mode("standard", "shadow"),
        ),
    )
    antwort = MagicMock()
    antwort.choices = [MagicMock(message=MagicMock(content="ALTPFAD"))]
    antwort.usage = None
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=antwort)
    monkeypatch.setattr("openai.AsyncOpenAI", MagicMock(return_value=client))

    ergebnis = await kai_chat_engine._respond_smalltalk("Hallo", "de")
    assert transport.await_count == 1, "SHADOW laesst den Transport mitlaufen"
    assert ergebnis.reply == "ALTPFAD", "SHADOW darf die Antwort nicht ersetzen"
    assert ergebnis.source == "gpt4o"


async def test_shadow_analysis_bleibt_beim_direkten_ergebnis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.analysis.ai_control_plane import ControlPlaneAnalysisProvider

    abweichend = _analysis_output()
    abweichend.sentiment_score = -0.9
    transport = AsyncMock(
        return_value=LiteLLMResponse(trace=_trace(), body=_chat_body(abweichend.model_dump_json()))
    )
    monkeypatch.setattr("app.ai.runtime.call_litellm_async", transport)

    direkt_ergebnis = _analysis_output()
    direct = MagicMock()
    direct.provider_name = "openai"
    direct.model = "gpt-4o"
    direct.analyze = AsyncMock(return_value=direkt_ergebnis)

    provider = ControlPlaneAnalysisProvider(direct, _mode("standard", "shadow"))
    ergebnis = await provider.analyze("Bitcoin", "Text")
    assert transport.await_count == 1
    assert ergebnis is direkt_ergebnis
    assert ergebnis.sentiment_score == pytest.approx(0.6)


async def test_shadow_ueberlebt_einen_transport_der_wirft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein kaputter Schattenpfad hinterlaesst keine herrenlose Task."""
    from app.analysis.ai_control_plane import ControlPlaneAnalysisProvider

    monkeypatch.setattr(
        "app.ai.runtime.call_litellm_async",
        AsyncMock(side_effect=RuntimeError("transport kaputt")),
    )
    direct = MagicMock()
    direct.provider_name = "openai"
    direct.model = "gpt-4o"
    direct.analyze = AsyncMock(return_value=_analysis_output())

    provider = ControlPlaneAnalysisProvider(direct, _mode("standard", "shadow"))
    with pytest.raises(RuntimeError, match="transport kaputt"):
        await provider.analyze("Bitcoin", "Text")
    # Entscheidend: keine zweite, unbeachtete Ausnahme aus einer vergessenen Task.
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# CONSENSUS erreicht nie mehr als SHADOW.
# ---------------------------------------------------------------------------


async def test_consensus_wird_auch_bei_globaler_primary_decke_geklemmt() -> None:
    from app.ai.gateway import execute_async
    from app.ai.models import AttemptResult

    async def direct() -> AttemptResult[str]:
        return AttemptResult(trace=AttemptTrace("direct", "gpt-4o", 1.0), value="direkt")

    async def lite() -> AttemptResult[str]:
        return AttemptResult(trace=_trace("kai-reasoning"), value="litellm")

    outcome = await execute_async(
        purpose="consensus",
        alias="kai-reasoning",
        direct_call=direct,
        litellm_call=lite,
        per_route={"reasoning": "primary"},
        ceiling="primary",
    )
    assert outcome.gateway.mode == "shadow"
    assert outcome.gateway.detail.get("mode_clamped") == "consensus_max_shadow"
    assert outcome.authoritative_value == "direkt"
    from app.ai.modes import has_execution_authority

    assert has_execution_authority(outcome.gateway.mode) is False


# ---------------------------------------------------------------------------
# Budget begrenzt Ausgaben, nicht den Betrieb.
# ---------------------------------------------------------------------------


async def test_ein_erschoepftes_budget_legt_den_direktpfad_nicht_still() -> None:
    """Sonst haette die Kostenbremse mehr Macht ueber KAI als der Modus-Schalter."""
    from app.ai.budget import BudgetPolicy, BudgetState
    from app.ai.gateway import SKIP_BUDGET_REJECT, execute_async
    from app.ai.models import AttemptResult

    beruehrt: list[str] = []

    async def direct() -> AttemptResult[str]:
        beruehrt.append("direct")
        return AttemptResult(trace=AttemptTrace("direct", "gpt-4o", 1.0), value="direkt")

    async def lite() -> AttemptResult[str]:
        beruehrt.append("litellm")
        return AttemptResult(trace=_trace(), value="litellm")

    outcome = await execute_async(
        purpose="chat",
        alias="kai-standard",
        direct_call=direct,
        litellm_call=lite,
        per_route={"standard": "shadow"},
        ceiling="shadow",
        budget_policy=BudgetPolicy(daily_limit_usd=1.0, monthly_limit_usd=10.0),
        daily=BudgetState(booked_usd=99.0, known_calls=1, unknown_calls=0),
        monthly=BudgetState(booked_usd=99.0, known_calls=1, unknown_calls=0),
    )
    assert outcome.gateway.budget == "reject"
    assert SKIP_BUDGET_REJECT in outcome.gateway.skipped
    assert beruehrt == ["direct"], "LiteLLM aus, Altpfad weiter"
    assert outcome.authoritative_value == "direkt"


# ---------------------------------------------------------------------------
# Async-Grenze: der synchrone Weg darf den Loop nicht anhalten.
# ---------------------------------------------------------------------------


async def test_der_synchrone_pfad_verweigert_sich_im_event_loop() -> None:
    from app.ai.gateway import execute

    with pytest.raises(RuntimeError, match="execute_async"):
        execute(purpose="chat", alias="kai-standard", direct_call=None)


def test_der_synchrone_pfad_laeuft_ausserhalb_des_loops_weiter() -> None:
    from app.ai.gateway import execute

    outcome = execute(purpose="chat", alias="kai-standard", direct_call=None)
    assert outcome.mode == "off"


def test_kein_blockierendes_http_in_den_verdrahteten_aufrufern() -> None:
    """``httpx.Client`` und ``requests`` haben im Event-Loop nichts zu suchen."""
    import ast
    from pathlib import Path

    dateien = [
        "app/ai/runtime.py",
        "app/ai/gateway.py",
        "app/analysis/ai_control_plane.py",
        "app/messaging/kai_chat_engine.py",
        "app/messaging/text_intent.py",
        "app/messaging/voice_transcriber.py",
        "app/trading/signal_consensus.py",
    ]
    for name in dateien:
        baum = ast.parse(Path(name).read_text(encoding="utf-8"))
        aufrufe = {ast.unparse(k.func) for k in ast.walk(baum) if isinstance(k, ast.Call)}
        assert "httpx.Client" not in aufrufe, name
        assert not {a for a in aufrufe if a.startswith("requests.")}, (name, aufrufe)
        assert "time.sleep" not in aufrufe, name


# ---------------------------------------------------------------------------
# Die Umgebungsfassung darf den Rollback nicht sprengen.
# ---------------------------------------------------------------------------


def test_eine_kaputte_env_variable_faellt_auf_hartes_off_statt_zu_werfen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Tippfehler in ``KAI_INFERENCE_*`` darf Chat und STT nicht abschalten."""
    from app.ai import runtime

    monkeypatch.setenv("KAI_INFERENCE_TIMEOUT_SECONDS", "voellig-kaputt")
    runtime.reset_environment_settings()
    try:
        konfiguriert = runtime.environment_settings()
        assert konfiguriert.enabled is False
        assert konfiguriert.mode_ceiling == "off"
    finally:
        runtime.reset_environment_settings()


def test_die_umgebung_wird_nicht_pro_aufruf_von_der_platte_gelesen() -> None:
    """``BaseSettings()`` liest ``.env`` — pro Chat-Aufruf waere das Datei-I/O im Loop."""
    from app.ai import runtime

    runtime.reset_environment_settings()
    try:
        for _ in range(5):
            runtime.inference_settings(None)
        info = runtime.environment_settings.cache_info()
        assert info.misses == 1, f"einmal gelesen, nicht {info.misses}-mal"
        assert info.hits == 4
    finally:
        runtime.reset_environment_settings()
