"""LiteLLM-v2 Sprint 5 control-plane acceptance tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai.audit import llm_call_scope
from app.ai.config import InferenceSettings
from app.ai.runtime import LiteLLMRequest, invoke
from app.integrations.litellm.provider import LiteLLMConfig


def _settings(
    mode: str,
    *,
    purpose_route: str = "standard",
    max_attempts: int = 3,
    reasoning_budget: dict[str, int] | None = None,
    reasoning_effort: dict[str, str] | None = None,
) -> InferenceSettings:
    return InferenceSettings(
        enabled=True,
        mode_ceiling=mode,
        route_modes={purpose_route: mode},
        route_reasoning_budget=reasoning_budget or {},
        route_reasoning_effort=reasoning_effort or {},
        max_attempts=max_attempts,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        jitter_max_seconds=0.0,
    )


def _factory(handler: Callable[[httpx.Request], httpx.Response]):
    def build(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return build


def _chat_request() -> LiteLLMRequest[str]:
    def parse(body: dict[str, Any]) -> str:
        return str(body["choices"][0]["message"]["content"])

    return LiteLLMRequest(parser=parse, payload={"messages": []})


def _response(request: httpx.Request, *, status: int = 200, cost: str | None = "0.1"):
    headers = {
        "x-litellm-model-provider": "openai",
        "x-litellm-model": "gpt-4o-mini",
    }
    if cost is not None:
        headers["x-litellm-response-cost"] = cost
    return httpx.Response(
        status,
        json={
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": "lite"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
        headers=headers,
        request=request,
    )


async def _no_sleep(_: float) -> None:
    return None


def test_inference_settings_are_fail_safe_by_default() -> None:
    settings = InferenceSettings(_env_file=None)
    assert settings.enabled is False
    assert settings.mode_ceiling == "off"
    assert settings.litellm_base_url == "http://127.0.0.1:4000"
    assert settings.max_attempts == 3


def test_localhost_boundary_rejects_prefix_and_userinfo_tricks() -> None:
    assert LiteLLMConfig(base_url="http://127.0.0.1:4000").is_local
    assert LiteLLMConfig(base_url="http://[::1]:4000").is_local
    assert not LiteLLMConfig(base_url="http://localhost.evil.example:4000").is_local
    assert not LiteLLMConfig(base_url="http://localhost@evil.example:4000").is_local


async def test_off_calls_direct_and_never_constructs_litellm_client() -> None:
    seen: list[str] = []

    async def direct() -> str:
        seen.append("direct")
        return "direct"

    def forbidden_factory(**_: Any) -> httpx.AsyncClient:
        raise AssertionError("OFF must not construct a LiteLLM client")

    result = await invoke(
        purpose="analysis",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=InferenceSettings(enabled=False),
        client_factory=forbidden_factory,
    )
    assert result.value == "direct"
    assert result.transport == "direct"
    assert seen == ["direct"]


async def test_unknown_mode_and_global_primary_without_route_graduation_are_off() -> None:
    calls = 0

    async def direct() -> str:
        return "direct"

    def forbidden(**_: Any) -> httpx.AsyncClient:
        nonlocal calls
        calls += 1
        raise AssertionError

    for settings in (
        InferenceSettings(enabled=True, mode_ceiling="typo", route_modes={"standard": "primary"}),
        InferenceSettings(enabled=True, mode_ceiling="primary", route_modes={}),
    ):
        result = await invoke(
            purpose="analysis",
            direct_call=direct,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=_chat_request(),
            settings=settings,
            client_factory=forbidden,
        )
        assert result.transport == "direct"
    assert calls == 0


async def test_shadow_runs_both_but_direct_is_authoritative() -> None:
    direct_calls = 0
    lite_calls = 0

    async def direct() -> str:
        nonlocal direct_calls
        direct_calls += 1
        return "direct"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lite_calls
        lite_calls += 1
        return _response(request)

    result = await invoke(
        purpose="analysis",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("shadow"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "direct"
    assert result.transport == "direct"
    assert direct_calls == lite_calls == 1
    assert result.outcome is not None
    assert result.outcome.gateway.shadow is result.outcome.gateway.litellm
    assert not result.outcome.gateway.litellm.execution_authority


async def test_consensus_primary_is_clamped_to_shadow() -> None:
    async def direct() -> str:
        return "direct-consensus"

    result = await invoke(
        purpose="consensus",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o-mini",
        litellm=_chat_request(),
        settings=_settings("primary", purpose_route="reasoning"),
        client_factory=_factory(_response),
        sleeper=_no_sleep,
    )
    assert result.value == "direct-consensus"
    assert result.outcome is not None
    assert result.outcome.gateway.mode == "shadow"
    assert result.outcome.gateway.authoritative is result.outcome.gateway.direct


@pytest.mark.parametrize("status", [400, 401, 402, 403])
async def test_non_retryable_http_errors_are_not_retried(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(request, status=status)

    async def direct() -> str:
        return "fallback"

    result = await invoke(
        purpose="analysis",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "fallback"
    assert calls == 1


async def test_quota_marker_on_429_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            json={"error": {"type": "insufficient_quota", "message": "quota exhausted"}},
            request=request,
        )

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("fallback"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "fallback"
    assert calls == 1
    assert result.outcome is not None
    assert result.outcome.litellm_attempts[0].trace.error_class == "quota"


async def test_schema_error_is_not_retried() -> None:
    """Eine Antwort, die DA ist, aber nicht die erwartete Form hat.

    Frueher stand hier `{"choices": []}`. Das ist aber kein Schema-Verstoss,
    sondern gar keine Antwort -- und seit der Leer-Erkennung im Transport wird
    es als `empty` gemeldet, mit dem `finish_reason` als Grund. Die Trennung ist
    keine Wortklauberei: "leer" verlangt ein anderes Token-Budget oder einen
    anderen Prompt, "schema" verlangt eine Aenderung am Parser. Wer beides
    gleich nennt, schickt den naechsten Leser in die falsche Datei.

    Der Fixture traegt jetzt eine echte Antwort, deren Feld der Parser nicht
    findet -- der Fall, den dieser Test seit jeher meint.
    """
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Klar, gerne!"}, "finish_reason": "stop"}]},
            request=request,
        )

    def erwartet_json(body: dict[str, Any]) -> str:
        # Der reale Fall: KAI liest strukturierte Daten AUS dem Text. Die
        # Antwort ist vorhanden und nicht leer -- sie ist nur Prosa, wo ein
        # JSON-Objekt stehen muesste.
        return str(json.loads(body["choices"][0]["message"]["content"])["verdict"])

    anfrage: LiteLLMRequest[str] = LiteLLMRequest(parser=erwartet_json, payload={"messages": []})

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("fallback"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=anfrage,
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "fallback"
    assert calls == 1
    assert result.outcome is not None
    assert result.outcome.litellm_attempts[0].trace.error_class == "schema"


async def test_eine_leere_antwort_wird_ebenfalls_nicht_wiederholt() -> None:
    """Der Fall, der vorher als `schema` durchging -- jetzt mit eigenem Namen.

    Wichtig ist nicht nur, dass nicht wiederholt wird, sondern dass der
    Direktpfad das Ergebnis liefert: im Schatten darf ein leerer Transport den
    Aufruf nicht mitnehmen.
    """
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": []}, request=request)

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("fallback"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )

    assert result.value == "fallback"
    assert calls == 1, "kein zweiter Versuch"
    assert result.outcome is not None
    trace = result.outcome.litellm_attempts[0].trace
    assert trace.error_class == "empty"
    assert trace.detail["empty_reason"] == "no_choices"


async def test_das_denkbudget_erreicht_wirklich_die_anfrage() -> None:
    """Der Helfer allein beweist nichts -- er muss verdrahtet sein.

    Diese Woche ist mehrfach ein Test gruen gewesen, der den Text eines
    Bausteins geprueft hat statt sein Verhalten im Betrieb. Hier wird deshalb
    der KOERPER der abgehenden Anfrage gelesen, nicht die Funktion, die ihn
    ergaenzt.
    """
    gesehen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.update(json.loads(request.content))
        return _response(request)

    await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direkt"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary", reasoning_budget={"standard": 0}),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )

    assert gesehen["thinking"] == {"type": "enabled", "budget_tokens": 0}


async def test_ohne_budget_steht_nichts_in_der_anfrage() -> None:
    """Die Gegenprobe: sonst zeigt der Test nur, dass irgendetwas gesetzt wird."""
    gesehen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.update(json.loads(request.content))
        return _response(request)

    await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direkt"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )

    assert "thinking" not in gesehen


async def test_eine_abgeschnittene_antwort_ueberlebt_den_transport() -> None:
    """Die Bedingung, an der die ganze Aufteilung haengt.

    `app/ai/runtime.py` kehrt bei `not trace.ok` zurueck, BEVOR
    `litellm.parser` laeuft. Wuerde der Transport eine Abschneidung als
    `error_class` melden, waere die praezise Runtime-Diagnose aus #945 --
    die Meldung, die `max_tokens` nennt -- im Betrieb nie erreichbar. Sie
    kaeme nur noch im Unit-Test vor.

    Deshalb bleibt `truncated` orthogonal zu `ok`, und deshalb wird das hier
    AUSGEFUEHRT geprueft und nicht behauptet: der Parser muss aufgerufen
    worden sein.
    """
    gesehen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gemini/gemini-3.6-flash",
                "choices": [
                    {"message": {"content": "Der groesste Risik"}, "finish_reason": "length"}
                ],
            },
            request=request,
        )

    def parser(body: dict[str, Any]) -> str:
        gesehen["aufgerufen"] = True
        return str(body["choices"][0]["message"]["content"])

    anfrage: LiteLLMRequest[str] = LiteLLMRequest(
        parser=parser, payload={"messages": [], "max_tokens": 1024}
    )

    ergebnis = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direkt"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=anfrage,
        # SHADOW und nicht PRIMARY: seit #945 faellt die Runtime bei
        # `truncated` fail-closed, und in PRIMARY risse ein gescheiterter
        # LiteLLM-Versuch den ganzen Aufruf mit. Geprueft werden soll hier
        # aber die TRANSPORT-Eigenschaft, nicht das Urteil der Runtime --
        # im Schatten bleibt sie beobachtbar.
        settings=_settings("shadow"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )

    # WAS HIER GEMEINT WAR -- und was die erste Fassung falsch zugesichert hat.
    #
    # Sie verlangte, dass der Parser AUFGERUFEN wird. Das war zu eng: gemeint
    # war, dass der TRANSPORT die Diagnose nicht vorwegnimmt, also kein
    # `not trace.ok`-Rueckweg greift. Der Operator hat fuer die Runtime
    # ausdruecklich das Gegenteil vorgesehen -- "truncated=true -> vor Parser
    # FAIL-CLOSED -> Parser wird nicht aufgerufen".
    #
    # Damit verbot diese Zusicherung genau den Entwurf, den sie schuetzen
    # sollte. bin-f3 hat ihr Gate deshalb fallengelassen, statt eine fremde
    # gemergte Kontrolle zu drehen -- konservativ und richtig gehandelt, aber
    # der Fehler lag hier.
    #
    # Geprueft wird jetzt die Eigenschaft, auf die es ankommt: der Versuch
    # ueberlebt den Transport. Ob ein spaeteres Runtime-Gate den Parser
    # ueberspringt, ist eine Entscheidung der Runtime und nicht Sache dieses
    # Tests.
    assert ergebnis.outcome is not None
    trace = ergebnis.outcome.litellm_attempts[0].trace
    assert trace.truncated is True
    assert trace.ok, "Transport erfolgreich — das Urteil faellt die Runtime"
    assert trace.detail["max_tokens"] == 1024


async def test_die_denkstufe_erreicht_wirklich_die_anfrage() -> None:
    """Derselbe Anspruch wie beim Budget: der KOERPER der Anfrage zaehlt.

    Und hier zaehlt er doppelt. Das Budget stand seit 2026-09-08 verdrahtet im
    Code und war auf `gemini/gemini-3.6-flash` trotzdem wirkungslos, weil
    LiteLLM 1.99.0 die Anthropic-Schreibweise FUER DIESES MODELL nicht
    uebersetzt -- fuer `gemini-2.5-flash` sehr wohl, dort wirkte das Budget.
    Dieser Test belegt, dass die Stufe die Anfrage verlaesst -- dass sie beim
    Anbieter ankommt, hat die Messung am 2026-09-09 auf kai-pi5 gezeigt
    (0 statt 876 Denk-Token).
    """
    gesehen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.update(json.loads(request.content))
        return _response(request)

    await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direkt"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary", reasoning_effort={"standard": "minimal"}),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )

    assert gesehen["reasoning_effort"] == "minimal"


async def test_ohne_stufe_steht_nichts_in_der_anfrage() -> None:
    """Die Gegenprobe: sonst zeigt der Test nur, dass irgendetwas gesetzt wird."""
    gesehen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.update(json.loads(request.content))
        return _response(request)

    await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direkt"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )

    assert "reasoning_effort" not in gesehen


async def test_beide_regler_erreichen_die_anfrage_gemeinsam() -> None:
    """Zwei Dialekte in einer Nutzlast — keiner verdraengt den anderen."""
    gesehen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.update(json.loads(request.content))
        return _response(request)

    await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direkt"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings(
            "primary",
            reasoning_budget={"standard": 0},
            reasoning_effort={"standard": "minimal"},
        ),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )

    assert gesehen["thinking"] == {"type": "enabled", "budget_tokens": 0}
    assert gesehen["reasoning_effort"] == "minimal"


async def _value(value: str) -> str:
    return value


@pytest.mark.parametrize("failure", [408, 429, 500, 503])
async def test_retryable_http_failure_retries_then_succeeds(failure: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(request, status=failure if calls == 1 else 200)

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "lite"
    assert result.transport == "litellm"
    assert calls == 2
    assert result.outcome is not None
    assert len(result.outcome.litellm_attempts) == 2


@pytest.mark.parametrize("kind", ["timeout", "transport"])
async def test_retryable_network_failure_is_bounded(kind: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if kind == "timeout":
            raise httpx.ReadTimeout("late", request=request)
        raise httpx.ConnectError("down", request=request)

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct-fallback"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary", max_attempts=3),
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.value == "direct-fallback"
    assert calls == 3
    assert result.outcome is not None
    assert len(result.outcome.litellm_attempts) == 3


async def test_unknown_retry_cost_makes_total_unknown_and_attempts_share_correlation(
    tmp_path: Path,
) -> None:
    calls = 0
    sink = tmp_path / "llm.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(
            request,
            status=500 if calls == 1 else 200,
            cost=None if calls == 1 else "0.2",
        )

    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("primary"),
        correlation_id="corr-s5",
        telemetry_path=sink,
        client_factory=_factory(handler),
        sleeper=_no_sleep,
    )
    assert result.outcome is not None
    assert result.outcome.gateway.litellm is not None
    assert result.outcome.gateway.litellm.total_cost_usd is None
    rows = [json.loads(line) for line in sink.read_text("utf-8").splitlines()]
    assert len(rows) == 2
    assert {row["correlation_id"] for row in rows} == {"corr-s5"}
    assert [row["attempt"] for row in rows] == [1, 2]
    # D-CORE-007: die ABRECHNUNG des Anbieters fehlt weiterhin -- der 500er
    # trug keinen Kosten-Header, und `AttemptTrace.cost_usd` bleibt `None`
    # (siehe `total_cost_usd` oben). Die Telemetriezeile traegt jetzt eine
    # SCHAETZUNG aus der Preistabelle, weil Modell und Token bekannt sind, und
    # weist ihre Herkunft aus. Beides nebeneinander ist der Punkt: eine
    # Schaetzung ersetzt keine Rechnung, aber sie ist besser als `null`.
    assert rows[0]["cost_source"].startswith("list_price:")
    assert rows[0]["cost_status"] == "OK"
    assert rows[0]["cost_usd"] > 0
    assert rows[1]["cost_source"] == "upstream"
    assert rows[1]["cost_usd"] == pytest.approx(0.2)
    assert rows[1]["actual_provider"] == "openai"
    assert rows[1]["actual_model"] == "gpt-4o-mini"
    assert rows[1]["identity_proven"] is True
    assert rows[1]["logical_route"] == "standard"
    assert rows[1]["execution_authority"] is True


async def test_direct_and_litellm_rows_share_correlation_in_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = tmp_path / "llm.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)

    async def direct() -> str:
        async with llm_call_scope(purpose="analysis", provider="openai", model="gpt-4o"):
            return "direct"

    await invoke(
        purpose="analysis",
        direct_call=direct,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("shadow"),
        correlation_id="corr-shared",
        telemetry_path=sink,
        client_factory=_factory(_response),
        sleeper=_no_sleep,
    )
    rows = [json.loads(line) for line in sink.read_text("utf-8").splitlines()]
    assert len(rows) == 2
    assert {row["correlation_id"] for row in rows} == {"corr-shared"}
    assert {row["transport"] for row in rows} == {"direct", "litellm"}


async def test_telemetry_writer_failure_does_not_break_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_lock(*_: Any, **__: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("app.observability.llm_telemetry.append_lock", fail_lock)
    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("shadow"),
        client_factory=_factory(_response),
        sleeper=_no_sleep,
    )
    assert result.value == "direct"


async def test_async_path_never_uses_sync_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("sync httpx.Client used in async caller")

    monkeypatch.setattr(httpx.Client, "post", forbidden)
    result = await invoke(
        purpose="analysis",
        direct_call=lambda: _value("direct"),
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=_chat_request(),
        settings=_settings("shadow"),
        client_factory=_factory(_response),
        sleeper=_no_sleep,
    )
    assert result.value == "direct"
