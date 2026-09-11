"""Tests for the telemetry-derived provider health snapshot (NEO-P-005)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.health import ai_health_snapshot
from app.api.routers.health import router as health_router


@pytest.fixture(autouse=True)
def _schattenkette_sichtbar(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Diese Datei prueft die KETTENBESCHREIBUNG, nicht den Schatten-Default.

    Seit 2026-09-09 ist die Zweitmeinung standardmaessig aus; ohne diesen
    Schalter waere ``chain.shadow`` in jedem Test hier leer und die Aussage
    ("Kette ist Konfiguration, nicht Verfuegbarkeit") ginge verloren. Der Test
    des Defaults selbst schaltet ihn wieder ab.
    """
    from app.core.ai_cost_settings import reset_ai_cost_settings

    monkeypatch.setenv("APP_ANALYSIS_SHADOW_ENABLED", "true")
    reset_ai_cost_settings()
    yield
    reset_ai_cost_settings()


def _settings(
    *,
    openai: str = "sk-x",
    gemini: str = "gk-x",
    anthropic: str = "ak-x",
    xai: str = "",
    xai_enabled: bool = False,
) -> Any:
    return SimpleNamespace(
        providers=SimpleNamespace(
            openai_api_key=openai,
            gemini_api_key=gemini,
            anthropic_api_key=anthropic,
            xai_api_key=xai,
            xai_fallback_enabled=xai_enabled,
        )
    )


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8"
    )


def _row(
    provider: str,
    ok: bool,
    *,
    minutes_ago: float = 1.0,
    latency_ms: float = 100.0,
    chain_position: int = 0,
    correlation_id: str | None = "req_1",
    error_class: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "v2",
        "ts": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(),
        "provider": provider,
        "model": f"{provider}-model",
        "role": "primary",
        "ok": ok,
        "latency_ms": latency_ms,
        "error_type": None if ok else "X",
        "error_class": error_class,
        "http_status": None,
        "correlation_id": correlation_id,
        "call_id": f"llmc_{provider}_{minutes_ago}",
        "purpose": "analysis",
        "chain_position": chain_position,
        "attempt": 1,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "outcome": "success" if ok else "fallthrough",
    }


def _providers(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in snapshot["ai"]["providers"]}


# ── chain description ────────────────────────────────────────────────────────


def test_chain_comes_from_the_factory_not_a_hardcoded_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.ai_cost_settings import reset_ai_cost_settings

    monkeypatch.delenv("APP_ANALYSIS_SHADOW_ENABLED", raising=False)
    reset_ai_cost_settings()
    snap = ai_health_snapshot(path=tmp_path / "none.jsonl", settings=_settings())
    assert snap["ai"]["chain"]["primary"] == ["openai", "gemini"]
    # Zweitmeinung AUS als Standard (2026-09-09): was die Factory nicht baut,
    # meldet /health/ai auch nicht.
    assert snap["ai"]["chain"]["shadow"] == []
    assert snap["ai"]["chain"]["source"] == "app/analysis/factory.py"

    monkeypatch.setenv("APP_ANALYSIS_SHADOW_ENABLED", "true")
    reset_ai_cost_settings()
    wieder_an = ai_health_snapshot(path=tmp_path / "none.jsonl", settings=_settings())
    assert wieder_an["ai"]["chain"]["shadow"] == ["anthropic"]
    reset_ai_cost_settings()


def test_ein_schluessel_macht_keinen_verfuegbaren_provider(tmp_path: Path) -> None:
    """Der Befund vom 2026-09-08, als das Anthropic-Auto-Aufladen ausging.

    `chain.shadow` kommt aus der Factory, und die bildet die Kette
    ausschliesslich aus der Key-Praesenz. Konto leer, Schluessel widerrufen,
    Modell abgeschaltet -- in allen drei Faellen steht der Provider weiter in
    der Kette. Wer nur die Kette liest, haelt ihn fuer einsatzbereit, waehrend
    jeder Aufruf scheitert.
    """
    sink = tmp_path / "telemetry.jsonl"
    _write(sink, [_row("anthropic", ok=False), _row("anthropic", ok=False)])

    snap = ai_health_snapshot(path=sink, settings=_settings())

    assert snap["ai"]["chain"]["shadow"] == ["anthropic"], "die Kette bleibt Konfiguration"
    assert snap["ai"]["chain"]["derived_from"] == "credentials_only"
    assert snap["ai"]["chain"]["observed"]["anthropic"] == "error"


def test_unbeobachtet_ist_nicht_gesund(tmp_path: Path) -> None:
    """Ohne Aufrufe im Fenster ist die richtige Antwort `unavailable`.

    Ein `ok` waere hier eine Erfindung: niemand hat den Provider gefragt, also
    weiss auch niemand, ob er antwortet. Das ist der Unterschied zwischen
    "nichts Schlechtes gesehen" und "es laeuft".
    """
    snap = ai_health_snapshot(path=tmp_path / "leer.jsonl", settings=_settings())

    for name in ("openai", "gemini", "anthropic"):
        assert snap["ai"]["chain"]["observed"][name] == "unavailable", name


def test_die_beobachtung_stammt_aus_denselben_bloecken(tmp_path: Path) -> None:
    """Kein zweiter Wahrheitsstand: eine Quelle, zwei Lesarten.

    Wuerde `observed` seinen Zustand selbst berechnen, koennten Kette und
    Provider-Block auseinanderlaufen -- und zwei Stellen im selben Dokument
    saehen denselben Provider verschieden.
    """
    sink = tmp_path / "telemetry.jsonl"
    _write(sink, [_row("openai", ok=True), _row("anthropic", ok=False)])

    snap = ai_health_snapshot(path=sink, settings=_settings())
    bloecke = _providers(snap)

    for name, zustand in snap["ai"]["chain"]["observed"].items():
        assert zustand == bloecke[name]["state"], name


def test_chain_includes_grok_only_when_flag_and_key_are_set(tmp_path: Path) -> None:
    with_flag = ai_health_snapshot(
        path=tmp_path / "none.jsonl", settings=_settings(xai="xk", xai_enabled=True)
    )
    assert with_flag["ai"]["chain"]["primary"] == ["openai", "gemini", "grok"]

    key_only = ai_health_snapshot(
        path=tmp_path / "none.jsonl", settings=_settings(xai="xk", xai_enabled=False)
    )
    assert key_only["ai"]["chain"]["primary"] == ["openai", "gemini"]


def test_shadow_falls_back_to_gemini_without_anthropic(tmp_path: Path) -> None:
    snap = ai_health_snapshot(path=tmp_path / "none.jsonl", settings=_settings(anthropic=""))
    assert snap["ai"]["chain"]["shadow"] == ["gemini"]


# ── state classification ─────────────────────────────────────────────────────


def test_empty_stream_is_unavailable_never_ok(tmp_path: Path) -> None:
    snap = ai_health_snapshot(path=tmp_path / "missing.jsonl", settings=_settings())
    blocks = _providers(snap)
    assert blocks["openai"]["state"] == "unavailable"
    assert blocks["openai"]["calls"] == 0
    assert blocks["openai"]["failure_rate_pct"] is None
    assert blocks["openai"]["latency_p50_ms"] is None
    assert blocks["openai"]["configured"] is True


def test_state_ok_below_ten_percent(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    rows = [_row("openai", True, minutes_ago=20 - i) for i in range(19)]
    rows.insert(0, _row("openai", False, minutes_ago=30, error_class="server"))
    _write(path, rows)

    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["calls"] == 20 and block["failures"] == 1
    assert block["failure_rate_pct"] == 5.0
    assert block["state"] == "ok"
    assert block["consecutive_failures"] == 0
    assert block["last_error_class"] == "server"
    assert block["last_ok_ts"] is not None


def test_state_degraded_between_ten_and_fifty_percent(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    rows = [_row("openai", False, minutes_ago=10, error_class="rate_limit")]
    rows += [_row("openai", True, minutes_ago=9 - i) for i in range(9)]
    _write(path, rows)

    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["failure_rate_pct"] == 10.0
    assert block["state"] == "error"


def test_state_down_above_fifty_percent(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write(
        path,
        [
            _row("openai", False, minutes_ago=5, error_class="server"),
            _row("openai", False, minutes_ago=4, error_class="server"),
            _row("openai", True, minutes_ago=3),
        ],
    )
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["state"] == "error"


def test_state_down_on_three_consecutive_failures_even_at_low_rate(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    rows = [_row("openai", True, minutes_ago=60 - i) for i in range(40)]
    rows += [_row("openai", False, minutes_ago=3 - i * 0.5, error_class="auth") for i in range(3)]
    _write(path, rows)

    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["consecutive_failures"] == 3
    assert block["failure_rate_pct"] < 10.0
    assert block["state"] == "error"
    assert block["last_error_class"] == "auth"


# ── windowing + de-duplication ───────────────────────────────────────────────


def test_rows_outside_the_window_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write(
        path,
        [
            _row("openai", False, minutes_ago=60 * 48, error_class="server"),
            _row("openai", True, minutes_ago=5),
        ],
    )
    block = _providers(ai_health_snapshot(window_hours=24.0, path=path, settings=_settings()))[
        "openai"
    ]
    assert block["calls"] == 1 and block["failures"] == 0


def test_outer_wrapper_row_does_not_double_count_the_ensemble(tmp_path: Path) -> None:
    """chain_position=-1 spans the whole chain; counting it too would double."""
    path = tmp_path / "t.jsonl"
    _write(
        path,
        [
            _row("openai", False, minutes_ago=3, chain_position=0, error_class="rate_limit"),
            _row("gemini", True, minutes_ago=2, chain_position=1),
            _row("gemini", True, minutes_ago=1, chain_position=-1),
        ],
    )
    blocks = _providers(ai_health_snapshot(path=path, settings=_settings()))
    assert blocks["openai"]["calls"] == 1
    assert blocks["gemini"]["calls"] == 1


def test_v1_rows_without_correlation_id_are_still_counted(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "ts": datetime.now(UTC).isoformat(),
                "provider": "openai",
                "model": "gpt-4o",
                "role": "primary",
                "ok": True,
                "latency_ms": 120.0,
                "error_type": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["calls"] == 1 and block["state"] == "ok"


def test_unconfigured_provider_seen_in_traffic_is_reported_as_not_configured(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    _write(path, [_row("grok", True, minutes_ago=2)])
    blocks = _providers(ai_health_snapshot(path=path, settings=_settings()))
    assert blocks["grok"]["configured"] is False
    assert blocks["grok"]["calls"] == 1


def test_latency_percentiles(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _write(
        path,
        [
            _row("openai", True, minutes_ago=4, latency_ms=100.0),
            _row("openai", True, minutes_ago=3, latency_ms=200.0),
            _row("openai", True, minutes_ago=2, latency_ms=300.0),
            _row("openai", True, minutes_ago=1, latency_ms=400.0),
        ],
    )
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["latency_p50_ms"] == 200.0
    assert block["latency_p95_ms"] == 400.0


def test_corrupt_lines_do_not_break_the_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        "not json\n" + json.dumps(_row("openai", True, minutes_ago=1)) + "\n", encoding="utf-8"
    )
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["calls"] == 1


# ── endpoint ─────────────────────────────────────────────────────────────────


@pytest.fixture
def health_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    sink = tmp_path / "llm_telemetry.jsonl"
    _write(
        sink,
        [
            _row("openai", False, minutes_ago=3, chain_position=0, error_class="rate_limit"),
            _row("gemini", True, minutes_ago=2, chain_position=1),
        ],
    )
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides = {}
    return TestClient(app)


def test_health_ai_endpoint_returns_chain_and_providers(
    health_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.core.settings.get_settings", lambda: _settings())
    response = health_client.get("/health/ai")
    assert response.status_code == 200
    body = response.json()
    assert body["chain"]["source"] == "app/analysis/factory.py"
    assert body["window_hours"] == 24.0
    names = [p["name"] for p in body["providers"]]
    assert "openai" in names and "gemini" in names
    assert response.headers["Cache-Control"].startswith("no-store")


def test_plain_health_endpoint_is_unchanged(health_client: TestClient) -> None:
    """HealthResponse must not grow — liveness consumers depend on it."""
    body = health_client.get("/health").json()
    assert body["status"] == "ok"
    assert "providers" not in body and "chain" not in body


def test_source_telemetry_cannot_poison_ai_health(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    # A source sharing a correlation id must not suppress a legacy AI wrapper.
    _write(
        path,
        [_row("CNBC", False), _row("Defillama", True), _row("openai", True, chain_position=-1)],
    )
    blocks = _providers(ai_health_snapshot(path=path, settings=_settings()))
    assert "CNBC" not in blocks and "Defillama" not in blocks
    assert blocks["openai"]["calls"] == 1


def test_configured_idle_disabled_and_missing_credentials_are_distinct(tmp_path: Path) -> None:
    blocks = _providers(
        ai_health_snapshot(path=tmp_path / "missing", settings=_settings(anthropic="", xai="key"))
    )
    assert blocks["gemini"]["state"] == "unavailable"
    assert blocks["gemini"]["status_reason"] == "no_recent_calls"
    assert blocks["gemini"]["last_error_class"] is None
    assert blocks["grok"]["state"] == "disabled"
    assert blocks["anthropic"]["state"] == "not_configured"


def test_future_and_naive_rows_cannot_establish_health(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    naive = _row("openai", True)
    naive["ts"] = datetime.now().isoformat()
    _write(path, [naive, _row("openai", True, minutes_ago=-10)])
    block = _providers(ai_health_snapshot(path=path, settings=_settings()))["openai"]
    assert block["calls"] == 0
    assert block["state"] == "unavailable"


# ── Budget-Abweisung ist kein Anbieterausfall ────────────────────────────────
#
# Am 2026-09-09 meldete kai-pi5 ``openai: state=error,
# status_reason=recent_calls_down`` — bei 308 erfolgreichen Aufrufen und null
# Anbieterfehlern. Die fuenf "Fehler" waren Budget-Abweisungen nach Erreichen
# des Tageslimits um 12:04 UTC. Gemini stand auf ``down`` mit
# ``failure_rate_pct=100.0``, nachdem sein einziger Aufruf im Fenster ebenfalls
# eine Abweisung war — der Anbieter wurde in 24 h kein einziges Mal kontaktiert.


def _budget_row(provider: str, *, minutes_ago: float = 1.0, nr: int = 0) -> dict[str, Any]:
    """Eine Zeile, wie das Gateway sie bei ``BudgetExceeded`` schreibt.

    Feldtreu zur Zeile vom Geraet: aeussere Kettenzeile (``chain_position=-1``)
    ohne innere Gegenstuecke, kein HTTP-Status, keine Tokens, ~200 ms — der
    Anbieter wurde nie erreicht.
    """
    return {
        "schema_version": "v2",
        "ts": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(),
        "provider": provider,
        "model": f"{provider}-model",
        "role": "primary",
        "ok": False,
        "latency_ms": 203.087,
        "error_type": "BudgetExceeded",
        "error_class": "unknown",
        "http_status": None,
        # Eigene correlation_id je Zeile: die echte Abweisung hat keine inneren
        # Versuchszeilen, ``dedupe_chain_levels`` darf sie nicht verwerfen.
        "correlation_id": f"doc_budget_{provider}_{nr}",
        "call_id": f"llmc_budget_{provider}_{nr}",
        "purpose": "analysis",
        "chain_position": -1,
        "attempt": 1,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "outcome": "exhausted",
    }


def test_single_budget_refusal_is_not_a_provider_failure(tmp_path: Path) -> None:
    sink = tmp_path / "t.jsonl"
    _write(sink, [_row("openai", True, minutes_ago=5), _budget_row("openai", minutes_ago=1)])

    openai = _providers(ai_health_snapshot(path=sink, settings=_settings()))["openai"]

    assert openai["calls"] == 1
    assert openai["failures"] == 0
    assert openai["failure_rate_pct"] == 0.0
    assert openai["consecutive_failures"] == 0
    assert openai["observed_state"] == "ok"
    assert openai["state"] == "ok"
    assert openai["status_reason"] == "recent_calls_ok"
    # Verschwiegen wird sie nicht.
    assert openai["local_refusals"] == 1


def test_three_consecutive_budget_refusals_do_not_trip_the_down_threshold(
    tmp_path: Path,
) -> None:
    """Genau der Schwellenwert, an dem es auf dem Geraet umkippte."""
    sink = tmp_path / "t.jsonl"
    _write(
        sink,
        [_row("openai", True, minutes_ago=10)]
        + [_budget_row("openai", minutes_ago=m, nr=m) for m in (3, 2, 1)],
    )

    openai = _providers(ai_health_snapshot(path=sink, settings=_settings()))["openai"]

    assert openai["consecutive_failures"] == 0
    assert openai["observed_state"] == "ok"
    assert openai["state"] != "error"
    assert openai["status_reason"] != "recent_calls_down"
    assert openai["local_refusals"] == 3


def test_success_and_budget_refusal_mixed_keeps_rate_and_latency_clean(
    tmp_path: Path,
) -> None:
    sink = tmp_path / "t.jsonl"
    _write(
        sink,
        [
            _row("openai", True, minutes_ago=9, latency_ms=1000.0, correlation_id="r1"),
            _row("openai", True, minutes_ago=8, latency_ms=1000.0, correlation_id="r2"),
            _budget_row("openai", minutes_ago=2, nr=1),
            _budget_row("openai", minutes_ago=1, nr=2),
        ],
    )

    openai = _providers(ai_health_snapshot(path=sink, settings=_settings()))["openai"]

    assert openai["calls"] == 2
    assert openai["failures"] == 0
    assert openai["failure_rate_pct"] == 0.0
    # Die 203-ms-Absage darf die Latenzverteilung nicht nach unten ziehen:
    # ein blockierter Anbieter saehe sonst schneller aus als ein arbeitender.
    assert openai["latency_p50_ms"] == 1000.0
    assert openai["latency_p95_ms"] == 1000.0
    assert openai["local_refusals"] == 2


def test_a_real_provider_failure_is_still_counted(tmp_path: Path) -> None:
    """Die Gegenprobe: der Fix darf echte Ausfaelle nicht mitverstecken."""
    sink = tmp_path / "t.jsonl"
    _write(
        sink,
        [
            _row("openai", False, minutes_ago=3, error_class="timeout", correlation_id="r1"),
            _row("openai", False, minutes_ago=2, error_class="timeout", correlation_id="r2"),
            _row("openai", False, minutes_ago=1, error_class="auth", correlation_id="r3"),
        ],
    )

    openai = _providers(ai_health_snapshot(path=sink, settings=_settings()))["openai"]

    assert openai["calls"] == 3
    assert openai["failures"] == 3
    assert openai["failure_rate_pct"] == 100.0
    assert openai["consecutive_failures"] == 3
    assert openai["observed_state"] == "down"
    assert openai["state"] == "error"
    assert openai["status_reason"] == "recent_calls_down"
    assert openai["last_error_class"] == "auth"
    assert openai["local_refusals"] == 0


def test_budget_refusal_after_a_success_keeps_the_last_measured_state(
    tmp_path: Path,
) -> None:
    """Der Anbieterzustand bleibt der letzte TATSAECHLICH gemessene."""
    sink = tmp_path / "t.jsonl"
    _write(
        sink,
        [
            _row("openai", True, minutes_ago=30, correlation_id="r1"),
            _budget_row("openai", minutes_ago=2, nr=1),
        ],
    )

    openai = _providers(ai_health_snapshot(path=sink, settings=_settings()))["openai"]

    assert openai["observed_state"] == "ok"
    assert openai["last_ok_ts"] is not None
    # Der Budget-Pfad setzt keine Anbieter-Fehlerklasse; "unknown" darf nicht
    # als letzte bekannte Fehlerursache stehenbleiben.
    assert openai["last_error_class"] is None


def test_provider_without_any_real_contact_is_unavailable_not_down(
    tmp_path: Path,
) -> None:
    """Gemini am 2026-09-09: ein Aufruf, eine Abweisung, kein Kontakt."""
    sink = tmp_path / "t.jsonl"
    _write(sink, [_row("openai", True, minutes_ago=5), _budget_row("gemini", minutes_ago=1)])

    gemini = _providers(ai_health_snapshot(path=sink, settings=_settings()))["gemini"]

    assert gemini["calls"] == 0
    assert gemini["failures"] == 0
    assert gemini["failure_rate_pct"] is None
    assert gemini["observed_state"] == "unavailable"
    assert gemini["state"] == "unavailable"
    assert gemini["state"] != "error"
    # "Es rief niemand an" und "wir haben selbst abgelehnt" sind zwei Lagen.
    assert gemini["status_reason"] == "no_provider_contact_local_refusal"
    assert gemini["local_refusals"] == 1


def test_provider_with_no_rows_at_all_still_says_no_recent_calls(tmp_path: Path) -> None:
    sink = tmp_path / "t.jsonl"
    _write(sink, [_row("openai", True, minutes_ago=5)])

    gemini = _providers(ai_health_snapshot(path=sink, settings=_settings()))["gemini"]

    assert gemini["observed_state"] == "unavailable"
    assert gemini["status_reason"] == "no_recent_calls"
    assert gemini["local_refusals"] == 0


def test_budget_block_reports_the_limit_separately_from_the_providers(
    tmp_path: Path,
) -> None:
    sink = tmp_path / "t.jsonl"
    _write(
        sink,
        [_budget_row("openai", minutes_ago=m, nr=m) for m in (3, 2, 1)]
        + [_budget_row("gemini", minutes_ago=1, nr=9)],
    )

    snap = ai_health_snapshot(path=sink, settings=_settings())
    budget = snap["ai"]["budget"]

    # Teilmenge, nicht Gleichheit: der Block ist additiv erweiterbar (Budget-
    # Policy v2 haengt die Alert-Faehigkeit daran). Was diese Datei zusichert,
    # sind DIESE vier Schluessel und ihre Bedeutung — nicht, dass es nie einen
    # fuenften geben darf.
    assert set(budget) >= {
        "budget_state",
        "budget_status_reason",
        "routine_calls_blocked",
        "local_refusals_in_window",
    }
    assert budget["local_refusals_in_window"] == 4
    # Projektion, kein zweiter Rechenweg: derselbe Zustand wie im Kostenblock.
    assert budget["budget_state"] == str(snap["ai"]["cost"]["status"]).lower()
    assert budget["budget_status_reason"] == snap["ai"]["cost"]["reason"]
    assert budget["routine_calls_blocked"] is bool(snap["ai"]["cost"]["blocks_routine"])


def test_endpoint_carries_budget_block_and_local_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nicht deklariert heisst bei Pydantic: still weggeworfen.

    ``ai_health`` baut die Antwort als ``AIHealthResponse(**snapshot["ai"])``.
    Ein Schluessel, den das Modell nicht kennt, verschwindet ohne Fehler — der
    Fix waere im Snapshot richtig und an der Leitung unsichtbar.
    """
    sink = tmp_path / "llm_telemetry.jsonl"
    _write(
        sink,
        [
            _row("openai", True, minutes_ago=5, correlation_id="r1"),
            _budget_row("openai", minutes_ago=1, nr=1),
        ],
    )
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)

    from app.core.settings import get_settings

    app = FastAPI()
    app.include_router(health_router)
    # Ueber die Dependency, nicht ueber das Quellmodul: FastAPI haelt das
    # Funktionsobjekt fest, ein spaeterer monkeypatch am Modul erreicht es nicht.
    app.dependency_overrides[get_settings] = _settings
    body = TestClient(app).get("/health/ai").json()

    assert "budget" in body
    assert set(body["budget"]) >= {
        "budget_state",
        "budget_status_reason",
        "routine_calls_blocked",
        "local_refusals_in_window",
    }
    assert body["budget"]["local_refusals_in_window"] == 1

    openai = next(p for p in body["providers"] if p["name"] == "openai")
    assert openai["local_refusals"] == 1
    assert openai["failures"] == 0
    assert openai["state"] == "ok"


def test_budget_block_names_the_reached_limit_in_the_agreed_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die exakten Felder, an denen der Operator die Lage erkennt.

    Der Replay der echten kai-pi5-Daten am 2026-09-09 konnte diese Zuordnung
    nicht belegen: lokal war kein Tageslimit gesetzt, der Kostenblock stand
    deshalb auf ``ok``. Hier steht das Limit — und damit die Abbildung
    ``LIMIT_REACHED -> limit_reached``.
    """
    from app.ai.spend import reset_spend_cache
    from app.core.ai_cost_settings import reset_ai_cost_settings

    sink = tmp_path / "t.jsonl"
    teuer = {
        "schema_version": "v2",
        "ts": datetime.now(UTC).isoformat(),
        "provider": "openai",
        "model": "gpt-4o",
        "actual_model": "gpt-4o",
        "ok": True,
        "latency_ms": 900.0,
        "chain_position": 0,
        "correlation_id": "spent",
        "call_id": "llmc_spent",
        "purpose": "analysis",
        "use_case": "news_intelligence",
        "input_tokens": 10,
        "output_tokens": 10,
        "cost_usd": 5.0,
    }
    _write(sink, [teuer, _budget_row("openai", minutes_ago=1, nr=1)])
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.0")
    reset_ai_cost_settings()
    reset_spend_cache()
    try:
        snap = ai_health_snapshot(path=sink, settings=_settings())
    finally:
        reset_ai_cost_settings()
        reset_spend_cache()

    budget = snap["ai"]["budget"]
    assert budget["budget_state"] == "limit_reached"
    assert budget["budget_status_reason"] == "daily_limit_reached"
    assert budget["routine_calls_blocked"] is True
    assert budget["local_refusals_in_window"] == 1
    # Und der Anbieter bleibt davon unberuehrt: das Limit ist unsere Lage,
    # nicht seine.
    openai = _providers(snap)["openai"]
    assert openai["state"] == "ok"
    assert openai["failures"] == 0


def test_endpoint_carries_pots_and_normal_pot_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Toepfe aus Budget-Policy v2 muessen die Leitung ueberleben, nicht nur
    den Bauplan.

    Der Befund, gegen den dieser Test geschrieben ist (2026-09-10, am Geraet):
    `app/ai/health.py` baut `pots` und `normal_pot_exhausted` UNBEDINGT, und
    `GET /health/ai` lieferte trotzdem keines von beiden. `AICostBlock` hatte
    sie nicht deklariert, und `response_model` wirft undeklarierte Schluessel
    still weg. Der Test auf den Builder war gruen, die Zusicherung kam beim
    Operator nie an.

    Genau dieselbe Falle hat diese Datei fuer den `budget`-Block schon einmal
    dokumentiert (siehe `test_endpoint_carries_budget_block_and_local_refusals`).
    Sie ist beim naechsten Feld wiedergekommen — deshalb prueft der zweite Test
    unten nicht mehr einzelne Namen, sondern die Deckung.
    """
    sink = tmp_path / "llm_telemetry.jsonl"
    _write(sink, [_row("openai", True, minutes_ago=5, correlation_id="p1")])
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)

    from app.core.settings import get_settings

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides[get_settings] = _settings
    antwort = TestClient(app).get("/health/ai")

    assert antwort.status_code == 200
    kosten = antwort.json()["cost"]

    # 1. Beide Felder kommen ueberhaupt an.
    assert "pots" in kosten, "pots von response_model verworfen"
    assert "normal_pot_exhausted" in kosten, "normal_pot_exhausted von response_model verworfen"

    # 2. Die Toepfe sind vollstaendig und tragen ihre Struktur, nicht nur ihren
    #    Namen — ein leeres Objekt waere derselbe Informationsverlust.
    assert set(kosten["pots"]) == {"normal", "alert_reserve", "validation", "exempt"}
    for name, topf in kosten["pots"].items():
        assert set(topf) == {
            "booked_usd",
            "calls",
            "unknown_cost_calls",
            "limit_usd",
            "remaining_usd",
        }, f"Topf {name} unvollstaendig serialisiert"
        assert isinstance(topf["booked_usd"], float)
        assert isinstance(topf["calls"], int)

    assert isinstance(kosten["normal_pot_exhausted"], bool)


def test_der_kostenblock_verliert_auf_der_leitung_kein_feld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deckungstest statt Namensliste — damit das naechste Feld nicht wieder faellt.

    Zweimal ist derselbe Fehler passiert: ein Schluessel wurde im Snapshot
    ergaenzt, im Antwortmodell vergessen und von FastAPI lautlos entfernt. Ein
    Test, der einzelne Namen aufzaehlt, findet den dritten Fall nicht. Dieser
    vergleicht, was der Bauplan liefert, mit dem, was die Leitung durchlaesst.

    Bewusst als Teilmenge in EINE Richtung: das Modell darf mehr deklarieren als
    der Snapshot gerade fuellt (optionale Felder), aber der Snapshot darf nichts
    liefern, was unterwegs verschwindet.
    """
    sink = tmp_path / "llm_telemetry.jsonl"
    _write(sink, [_row("openai", True, minutes_ago=5, correlation_id="p2")])
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)

    from app.core.settings import get_settings

    schnappschuss = ai_health_snapshot(path=sink, settings=_settings())["ai"]["cost"]

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides[get_settings] = _settings
    geliefert = TestClient(app).get("/health/ai").json()["cost"]

    verloren = set(schnappschuss) - set(geliefert)
    assert not verloren, (
        f"AICostBlock deklariert diese Schluessel nicht, response_model wirft sie weg: "
        f"{sorted(verloren)}"
    )


def test_fehlversuche_ohne_verbrauch_kommen_ueber_http_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-271: der neue Zaehler ist nur dann sichtbar, wenn er die Leitung uebersteht.

    Ein Research-Fehlversuch ueber LiteLLM (500, keine Identitaet, keine Token)
    zaehlt nicht mehr als unbekannter Kostenfall -- er darf dafuer aber auch
    nicht verschwinden. Geprueft am echten Endpunkt, nicht am Builder.
    """
    from app.ai.spend import reset_spend_cache
    from app.core.settings import get_settings

    fehlversuch = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": "",
        "model": "kai-kimi-research",
        "ok": False,
        "chain_position": 0,
        "correlation_id": "research-500",
        "purpose": "research",
        "use_case": "research",
        "transport": "litellm",
        "logical_route": "research",
        "http_status": 500,
        "error_class": "server",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "cost_status": "COST_UNKNOWN",
        "cost_reason": "no_tokens",
    }
    sink = tmp_path / "llm_telemetry.jsonl"
    _write(sink, [fehlversuch])
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.spend.DEFAULT_TELEMETRY_PATH", sink)
    reset_spend_cache()

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides[get_settings] = _settings
    antwort = TestClient(app).get("/health/ai")
    reset_spend_cache()

    assert antwort.status_code == 200
    koerper = antwort.json()
    assert koerper["cost"]["failed_uncosted_calls_today"] == 1
    assert koerper["cost"]["unknown_cost_calls_today"] == 0
    assert koerper["cost"]["status"] == "OK"
    # Ohne belegten Anbieter gibt es keinen Anbieterblock -- auch keinen namens "".
    assert "" not in {block["name"] for block in koerper["providers"]}


# ── Alert-Faehigkeit gegen die Decke des NORMALEN Topfes ─────────────────────


def _kostenzeile(cost_usd: float) -> dict[str, Any]:
    """Eine bepreiste Zeile — nur so entsteht ueberhaupt ein Tagesverbrauch."""
    return {
        "ts": datetime.now(UTC).isoformat(),
        "provider": "openai",
        "model": "gpt-x",
        "actual_model": "gpt-x",
        "ok": True,
        "chain_position": 0,
        "correlation_id": "cap",
        "purpose": "analysis",
        "use_case": "news_intelligence",
        "cost_usd": cost_usd,
        "cost_status": "OK",
    }


def _budget_ueber_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, ausgegeben: float
) -> dict[str, Any]:
    """Setzt die Reserven, schreibt den Verbrauch und liest ``GET /health/ai``.

    Ueber den ECHTEN Endpunkt, nicht ueber ``_budget_block`` — der Fehler, gegen
    den diese Tests stehen, lebt in der Zusammensetzung des Blocks, und ein
    Builder-Aufruf haette ihn zwar auch gezeigt, aber die Serialisierung nicht
    mitgeprueft. Diese Datei hat zweimal gelernt, dass das ein Unterschied ist.
    """
    from app.ai.spend import reset_spend_cache
    from app.core.ai_cost_settings import reset_ai_cost_settings
    from app.core.settings import get_settings

    # 1,25 − 0,16 − 0,05 = 1,04 Decke fuer den normalen Topf.
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.25")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_USD", "0.16")
    monkeypatch.setenv("APP_AI_BUDGET_VALIDATION_RESERVE_USD", "0.05")
    reset_ai_cost_settings()

    sink = tmp_path / "llm_telemetry.jsonl"
    _write(sink, [_kostenzeile(ausgegeben)])
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)
    # DRITTE Bindung, und ohne sie misst dieser Test nichts: app/ai/spend.py holt
    # DEFAULT_TELEMETRY_PATH beim Laden per "from ... import ...", also als eigene
    # Kopie des Namens. Ein Patch am Ursprungsmodul erreicht sie nicht -- der
    # Verbrauch bliebe 0,0 und alle drei Baender saehen identisch aus.
    monkeypatch.setattr("app.ai.spend.DEFAULT_TELEMETRY_PATH", sink)
    reset_spend_cache()

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides[get_settings] = _settings
    antwort = TestClient(app).get("/health/ai")
    assert antwort.status_code == 200
    reset_spend_cache()
    return antwort.json()


def test_unter_der_topfdecke_bleibt_alles_wie_bisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gegenprobe: der Fix darf den Normalfall nicht anfassen."""
    koerper = _budget_ueber_http(tmp_path, monkeypatch, ausgegeben=0.90)

    assert koerper["cost"]["normal_pot_exhausted"] is False
    assert koerper["budget"]["routine_calls_blocked"] is False
    assert koerper["budget"]["alert_capability_for_new_documents"] == "ok"


def test_zwischen_topfdecke_und_tageslimit_meldet_der_block_nicht_mehr_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der eigentliche Befund — und er entsteht erst durch die Kombination.

    Bei 1,25 / 0,16 / 0,05 liegt die Decke des normalen Topfes bei 1,04. Im
    Bereich 1,04 <= verbraucht < 1,25 ist ``blocks_routine`` noch ``False``,
    ``normal_pot_exhausted`` aber schon ``True``.

    #954 hat ``routine_calls_blocked`` auf das ODER umgestellt, #953 gab
    ``_alert_capability_block`` weiter nur das schmale ``blocks_routine``.
    Beide Zeilen mergten sauber — textuell kollidieren sie nicht —, und danach
    meldete derselbe Block gleichzeitig "Routine gesperrt" und
    "Alert-Faehigkeit ok". Also genau die lautlose Gruen-Meldung, gegen die
    beide PRs geschrieben waren.

    Der Regelpfad erreicht das Alert-Gate strukturell nicht (Deckel 0,575 gegen
    Gate 0,615), deshalb ist ``unreachable`` hier die wahre Aussage und nicht
    ``degraded``.
    """
    koerper = _budget_ueber_http(tmp_path, monkeypatch, ausgegeben=1.10)

    assert koerper["cost"]["normal_pot_exhausted"] is True
    assert koerper["cost"]["blocks_routine"] is False, "das Tageslimit ist NICHT erreicht"
    assert koerper["budget"]["routine_calls_blocked"] is True
    assert koerper["budget"]["alert_capability_for_new_documents"] == "unreachable"
    # Die Kernzusicherung, in der Sprache des Befunds:
    assert not (
        koerper["budget"]["routine_calls_blocked"]
        and koerper["budget"]["alert_capability_for_new_documents"] == "ok"
    ), "Routine gesperrt und Alert-Faehigkeit ok — das ist die lautlose Gruen-Meldung"


def test_am_tageslimit_bleibt_die_bestehende_semantik(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oberhalb des Gesamtlimits aendert der Fix nichts — dort galt es schon."""
    koerper = _budget_ueber_http(tmp_path, monkeypatch, ausgegeben=1.30)

    assert koerper["cost"]["blocks_routine"] is True
    assert koerper["budget"]["routine_calls_blocked"] is True
    assert koerper["budget"]["alert_capability_for_new_documents"] == "unreachable"
