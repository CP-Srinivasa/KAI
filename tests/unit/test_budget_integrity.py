"""Budget-Integritaet (Operator-Auftrag 2026-09-14).

Vier Befunde aus dem Kettentest vom 14.09., jeder hier als Regression:

1. Doppelzaehlung: seit #887 (07.09.) erzeugte die Runtime fuer jede Analyse
   eine NEUE Korrelations-ID. Die inneren Kettenzeilen trugen ``llm_...``, die
   aeussere Zeile ``doc_...`` -- ``dedupe_chain_levels`` griff nie, das Budget
   zaehlte jeden bezahlten Aufruf zweimal (14.09.: 0,8728 statt 0,4364 USD).
2. Die Sperrzeile trug ein falsches Anbieter-Etikett und keinen Grund.
3. Die Monatshochrechnung fehlte.
4. ``gemini-3.6-flash`` hatte keinen Preis.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai.spend import (
    _ALTPAAR_AB,
    _ALTPAAR_BIS,
    is_ai_row,
    load_rows,
    reset_spend_cache,
    spend_window,
)


def month_projection(*args: Any, **kwargs: Any) -> Any:
    """Spaeter Import: fehlt die Funktion, scheitert nur der Test, der sie braucht."""
    from app.ai.spend import month_projection as echt

    return echt(*args, **kwargs)


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    reset_spend_cache()


def _write(sink: Path, rows: list[dict[str, Any]]) -> None:
    sink.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
    reset_spend_cache()


_JETZT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _zeile(**kwargs: Any) -> dict[str, Any]:
    basis: dict[str, Any] = {
        "ts": _JETZT.isoformat(),
        "provider": "openai",
        "model": "gpt-4o",
        "actual_model": "gpt-4o",
        "ok": True,
        "chain_position": 0,
        "correlation_id": "llm_aaaa",
        "purpose": "analysis",
        "use_case": "news_intelligence",
        "input_tokens": 1848,
        "output_tokens": 223,
        "cost_usd": 0.00685,
        "cost_status": "OK",
    }
    basis.update(kwargs)
    return basis


def _paar(sekunden_dazwischen: float = 3.0, **gemeinsam: Any) -> list[dict[str, Any]]:
    """Innere Versuchszeile mit ``llm_``-ID und aeussere mit ``doc_``-ID -- das Altmuster."""
    innen = _zeile(chain_position=0, correlation_id="llm_innen1", **gemeinsam)
    aussen_ts = (_JETZT + timedelta(seconds=sekunden_dazwischen)).isoformat()
    aussen = _zeile(chain_position=-1, correlation_id="doc_1", ts=aussen_ts, **gemeinsam)
    return [innen, aussen]


# ── 1a. Altzeilen seit 07.09.: dieselbe physische Anfrage zaehlt einmal ─────


def test_altpaar_mit_verschiedenen_ids_zaehlt_nur_einmal(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _write(sink, _paar())

    fenster = spend_window("today", path=sink, now=_JETZT + timedelta(minutes=1))

    assert fenster.calls == 1
    assert fenster.known_cost_usd == pytest.approx(0.00685)


def test_altpaar_behaelt_die_innere_zeile(tmp_path: Path) -> None:
    """Die innere Zeile ist die physische, bezahlte Anfrage -- sie gewinnt."""
    sink = tmp_path / "llm.jsonl"
    _write(sink, _paar())

    zeilen = load_rows(sink)

    assert [z["chain_position"] for z in zeilen] == [0]


def test_verschiedene_token_sind_keine_paarung(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    innen, aussen = _paar()
    aussen["output_tokens"] = 224
    _write(sink, [innen, aussen])

    assert spend_window("today", path=sink, now=_JETZT + timedelta(minutes=1)).calls == 2


def test_zu_grosser_zeitabstand_ist_keine_paarung(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _write(sink, _paar(sekunden_dazwischen=600.0))

    assert spend_window("today", path=sink, now=_JETZT + timedelta(minutes=15)).calls == 2


def test_aeussere_zeile_vor_der_inneren_ist_keine_paarung(tmp_path: Path) -> None:
    """Die aeussere Zeile wird NACH der Kette geschrieben; umgekehrt ist es ein anderer Aufruf."""
    sink = tmp_path / "llm.jsonl"
    _write(sink, _paar(sekunden_dazwischen=-30.0))

    assert spend_window("today", path=sink, now=_JETZT + timedelta(minutes=1)).calls == 2


def test_anbietername_im_modellfeld_der_alten_aussenzeile_paart_trotzdem(tmp_path: Path) -> None:
    """Bis 09.09. stand in der aeusseren Zeile ``model=openai`` (#930)."""
    sink = tmp_path / "llm.jsonl"
    innen, aussen = _paar()
    aussen["model"] = "openai"
    aussen["actual_model"] = None
    _write(sink, [innen, aussen])

    assert spend_window("today", path=sink, now=_JETZT + timedelta(minutes=1)).calls == 1


def test_eine_innere_zeile_ersetzt_hoechstens_eine_aeussere(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    innen, aussen = _paar()
    zweite_aussen = dict(aussen, correlation_id="doc_2")
    _write(sink, [innen, aussen, zweite_aussen])

    assert spend_window("today", path=sink, now=_JETZT + timedelta(minutes=1)).calls == 2


def test_innere_zeile_mit_eigener_aussenzeile_wird_nicht_fremd_gepaart(tmp_path: Path) -> None:
    """Neue Zeilen teilen die Dokument-ID; ihre Innenzeile ist nicht verwaist."""
    sink = tmp_path / "llm.jsonl"
    innen = _zeile(chain_position=0, correlation_id="doc_1")
    aussen = _zeile(
        chain_position=-1, correlation_id="doc_1", ts=(_JETZT + timedelta(seconds=2)).isoformat()
    )
    fremd = _zeile(
        chain_position=-1, correlation_id="doc_2", ts=(_JETZT + timedelta(seconds=4)).isoformat()
    )
    _write(sink, [innen, aussen, fremd])

    assert spend_window("today", path=sink, now=_JETZT + timedelta(minutes=1)).calls == 2


# ── 1a-ii. Der Zeitraum ist Teil der Regel (Issue #973, Review der Reserve) ─


def test_innere_zeile_nach_dem_cutoff_wird_auch_ohne_aussenzeile_nicht_gepaart(
    tmp_path: Path,
) -> None:
    """Seit dem Deploy von #970 tragen Kettenzeilen die Dokument-ID. Bricht ein
    Aufruf ab, bevor die aeussere Zeile geschrieben ist, bleibt seine innere Zeile
    allein -- und darf trotzdem keine fremde aeussere Zeile verdraengen."""
    sink = tmp_path / "llm.jsonl"
    start = _ALTPAAR_BIS + timedelta(minutes=5)
    innen = _zeile(chain_position=0, correlation_id="doc_abgebrochen", ts=start.isoformat())
    fremd = _zeile(
        chain_position=-1, correlation_id="doc_fremd", ts=(start + timedelta(seconds=3)).isoformat()
    )
    _write(sink, [innen, fremd])

    assert spend_window("today", path=sink, now=start + timedelta(minutes=1)).calls == 2


def test_innere_zeile_vor_dem_zeitraum_wird_nicht_gepaart(tmp_path: Path) -> None:
    """Vor #887 (07.09.) gab es die Doppelzaehlung nicht; die Regel greift dort nicht."""
    sink = tmp_path / "llm.jsonl"
    start = _ALTPAAR_AB - timedelta(hours=6)
    innen = _zeile(chain_position=0, correlation_id="llm_alt", ts=start.isoformat())
    aussen = _zeile(
        chain_position=-1, correlation_id="doc_alt", ts=(start + timedelta(seconds=3)).isoformat()
    )
    _write(sink, [innen, aussen])

    assert spend_window("today", path=sink, now=start + timedelta(minutes=1)).calls == 2


def test_der_zeitraum_der_doppelzaehlung_ist_der_dokumentierte() -> None:
    """07.09. (#887) bis zur Aktivierung des ersten Release mit #970 (14.09., 14:12Z)."""
    assert _ALTPAAR_AB == datetime(2026, 9, 7, tzinfo=UTC)
    assert _ALTPAAR_BIS == datetime(2026, 9, 14, 14, 15, tzinfo=UTC)
    assert _ALTPAAR_AB < _JETZT < _ALTPAAR_BIS  # die uebrigen Paar-Tests liegen im Zeitraum


@pytest.mark.parametrize(("abstand", "erwartet"), [(120.0, 1), (120.001, 2)])
def test_die_grenze_von_120_sekunden_gilt_einschliesslich(
    tmp_path: Path, abstand: float, erwartet: int
) -> None:
    sink = tmp_path / "llm.jsonl"
    _write(sink, _paar(sekunden_dazwischen=abstand))

    assert spend_window("today", path=sink, now=_JETZT + timedelta(minutes=5)).calls == erwartet


def test_anbietername_im_modellfeld_paart_nicht_ueber_die_anbietergrenze(tmp_path: Path) -> None:
    """Gegenprobe zu oben: ``model=openai`` in der Aussenzeile paart keine Gemini-Innenzeile."""
    sink = tmp_path / "llm.jsonl"
    innen, aussen = _paar()
    innen["provider"] = "gemini"
    innen["model"] = innen["actual_model"] = "gemini-2.5-flash"
    aussen["model"] = "openai"
    aussen["actual_model"] = None
    _write(sink, [innen, aussen])

    assert spend_window("today", path=sink, now=_JETZT + timedelta(minutes=1)).calls == 2


# ── 1b. Neue Zeilen: die Kette erbt die Dokument-ID ────────────────────────


def test_verschachtelter_scope_ohne_id_erbt_die_umgebende() -> None:
    from app.ai.audit import correlation_scope

    with correlation_scope("doc_x"), correlation_scope(None) as innen:
        assert innen == "doc_x"


def test_scope_ohne_umgebung_erzeugt_weiter_eine_eigene_id() -> None:
    from app.ai.audit import correlation_scope, current_correlation_id

    assert current_correlation_id() is None
    with correlation_scope(None) as eigene:
        assert eigene.startswith("llm_")


async def test_runtime_kettenzeilen_tragen_die_dokument_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der eigentliche Defekt: ``invoke`` ersetzte die Dokument-ID durch eine neue."""
    from app.ai.audit import correlation_scope
    from app.ai.config import InferenceSettings
    from app.ai.runtime import invoke, reset_environment_settings
    from app.analysis.ensemble.provider import EnsembleProvider
    from app.core.ai_cost_settings import reset_ai_cost_settings
    from tests.unit.factories import make_llm_output

    reset_ai_cost_settings()
    reset_environment_settings()
    sink = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)

    anbieter = AsyncMock()
    anbieter.provider_name = "openai"
    anbieter.model = "gpt-4o"
    ausgabe = make_llm_output()
    ausgabe.prompt_tokens = 1848
    ausgabe.completion_tokens = 223
    anbieter.analyze = AsyncMock(return_value=ausgabe)
    kette = EnsembleProvider([anbieter])

    with correlation_scope("doc_71947195"):
        await invoke(
            purpose="analysis",
            direct_call=lambda: kette.analyze("titel", "Bitcoin " * 40),
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=None,
            settings=InferenceSettings(),
            telemetry_path=sink,
        )

    ids = {json.loads(z)["correlation_id"] for z in sink.read_text("utf-8").splitlines() if z}
    assert ids == {"doc_71947195"}


# ── 2. Sperrzeile ohne Anbieteretikett bleibt fuer Budget und Health sichtbar ─


def _sperrzeile(**kwargs: Any) -> dict[str, Any]:
    basis = _zeile(
        provider="",
        model="",
        actual_model=None,
        ok=False,
        chain_position=-1,
        correlation_id="doc_sperre",
        input_tokens=0,
        output_tokens=0,
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=None,
        cost_status="COST_UNKNOWN",
        error_type="BudgetExceeded",
        budget_decision="reject:daily_limit_reached",
        http_status=None,
        role="primary",
        latency_ms=1.0,
    )
    basis.update(kwargs)
    return basis


def test_sperrzeile_ohne_anbieter_ist_eine_ki_zeile() -> None:
    assert is_ai_row(_sperrzeile()) is True


def test_andere_zeile_ohne_anbieter_bleibt_draussen() -> None:
    """Die Ausnahme gilt nur fuer lokale Sperren, nicht fuer jede namenlose Zeile."""
    assert is_ai_row(_zeile(provider="", error_type="RuntimeError", ok=False)) is False


def test_sperrzeile_zaehlt_als_fehlversuch_ohne_verbrauch(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _write(sink, [_sperrzeile()])

    fenster = spend_window("today", path=sink, now=_JETZT + timedelta(minutes=1))

    assert fenster.calls == 1
    assert fenster.failed_uncosted_calls == 1
    assert fenster.unknown_calls == 0


def test_health_zaehlt_die_sperre_ohne_sie_einem_anbieter_zuzuschreiben(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from app.ai.health import ai_health_snapshot

    sink = tmp_path / "llm.jsonl"
    _write(sink, [_sperrzeile(ts=datetime.now(UTC).isoformat())])
    einstellungen = SimpleNamespace(
        providers=SimpleNamespace(
            openai_api_key="sk-x",
            gemini_api_key="gk-x",
            anthropic_api_key="",
            xai_api_key="",
            xai_fallback_enabled=False,
        )
    )

    ai = ai_health_snapshot(path=sink, settings=einstellungen)["ai"]

    assert ai["budget"]["local_refusals_in_window"] == 1
    assert all(block["name"] for block in ai["providers"])
    assert all(block["local_refusals"] == 0 for block in ai["providers"])


# ── 3. Monatshochrechnung nur aus entdoppelten Kosten ──────────────────────


def _monat(sink: Path, rows: list[dict[str, Any]], jetzt: datetime) -> Any:
    _write(sink, rows)
    return spend_window("month", path=sink, now=jetzt)


def test_hochrechnung_zur_monatsmitte(tmp_path: Path) -> None:
    jetzt = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    monat = _monat(
        tmp_path / "llm.jsonl",
        [_zeile(ts=(jetzt - timedelta(days=1)).isoformat(), cost_usd=7.0)],
        jetzt,
    )

    p = month_projection(monat, monthly_limit_usd=25.0, now=jetzt)

    assert p.days_in_month == 30
    assert p.days_elapsed == pytest.approx(14.0)
    assert p.projected_month_usd == pytest.approx(15.0)
    assert p.sustainable_daily_usd == pytest.approx(25.0 / 30)
    assert p.exceeds_monthly_limit is False
    assert p.lower_bound is False


def test_hochrechnung_ueber_dem_monatslimit_wird_benannt(tmp_path: Path) -> None:
    jetzt = datetime(2026, 9, 11, 0, 0, tzinfo=UTC)
    monat = _monat(
        tmp_path / "llm.jsonl",
        [_zeile(ts=(jetzt - timedelta(hours=5)).isoformat(), cost_usd=12.5)],
        jetzt,
    )

    p = month_projection(monat, monthly_limit_usd=25.0, now=jetzt)

    assert p.projected_month_usd == pytest.approx(37.5)
    assert p.exceeds_monthly_limit is True


def test_hochrechnung_ist_untergrenze_bei_unbepreisten_aufrufen(tmp_path: Path) -> None:
    jetzt = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    monat = _monat(
        tmp_path / "llm.jsonl",
        [
            _zeile(ts=(jetzt - timedelta(days=1)).isoformat(), cost_usd=1.0),
            _zeile(
                ts=(jetzt - timedelta(days=1)).isoformat(),
                correlation_id="llm_b",
                cost_usd=None,
                cost_status="COST_UNKNOWN",
            ),
        ],
        jetzt,
    )

    assert month_projection(monat, monthly_limit_usd=25.0, now=jetzt).lower_bound is True


def test_in_der_ersten_stunde_wird_nicht_hochgerechnet(tmp_path: Path) -> None:
    jetzt = datetime(2026, 10, 1, 0, 30, tzinfo=UTC)
    monat = _monat(tmp_path / "llm.jsonl", [_zeile(ts=jetzt.isoformat(), cost_usd=0.5)], jetzt)

    p = month_projection(monat, monthly_limit_usd=25.0, now=jetzt)

    assert p.projected_month_usd is None
    assert p.exceeds_monthly_limit is None


def test_ohne_monatslimit_keine_tragfaehige_tagesrate(tmp_path: Path) -> None:
    jetzt = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    monat = _monat(tmp_path / "llm.jsonl", [], jetzt)

    p = month_projection(monat, monthly_limit_usd=None, now=jetzt)

    assert p.sustainable_daily_usd is None
    assert p.exceeds_monthly_limit is None


def test_hochrechnung_rechnet_mit_entdoppelten_kosten(tmp_path: Path) -> None:
    jetzt = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    innen, aussen = _paar()
    innen["ts"] = (jetzt - timedelta(days=1)).isoformat()
    aussen["ts"] = (jetzt - timedelta(days=1) + timedelta(seconds=3)).isoformat()
    innen["cost_usd"] = aussen["cost_usd"] = 7.0
    monat = _monat(tmp_path / "llm.jsonl", [innen, aussen], jetzt)

    assert month_projection(monat, monthly_limit_usd=25.0, now=jetzt).projected_month_usd == (
        pytest.approx(15.0)
    )


def test_kostenblock_und_endpunkt_tragen_die_hochrechnung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nicht deklariert heisst bei ``response_model``: still weggeworfen (#957)."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers.health import router as health_router
    from app.core.settings import get_settings

    sink = tmp_path / "llm_telemetry.jsonl"
    _write(sink, [_zeile(ts=datetime.now(UTC).isoformat())])
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    monkeypatch.setattr("app.ai.health.DEFAULT_TELEMETRY_PATH", sink)

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        providers=SimpleNamespace(
            openai_api_key="sk-x",
            gemini_api_key="",
            anthropic_api_key="",
            xai_api_key="",
            xai_fallback_enabled=False,
        )
    )
    kosten = TestClient(app).get("/health/ai").json()["cost"]

    assert set(kosten) >= {
        "days_in_month",
        "month_days_elapsed",
        "projected_month_usd",
        "sustainable_daily_usd",
        "projected_exceeds_monthly_limit",
        "projection_is_lower_bound",
    }


# ── 4. Preis fuer gemini-3.6-flash mit Ablaufdatum ──────────────────────────


def test_gemini_36_flash_hat_den_listenpreis_vom_september() -> None:
    from app.ai.pricing import estimate_cost_usd

    schaetzung = estimate_cost_usd("gemini-3.6-flash", 1_000_000, 1_000_000, on=date(2026, 9, 14))

    assert schaetzung.status == "OK"
    assert schaetzung.usd == pytest.approx(0.75 + 3.75)


def test_gemini_36_flash_mit_anbieterpraefix() -> None:
    from app.ai.pricing import estimate_cost_usd

    schaetzung = estimate_cost_usd("gemini/gemini-3.6-flash", 1196, 508, on=date(2026, 9, 14))

    assert schaetzung.usd == pytest.approx((1196 * 0.75 + 508 * 3.75) / 1_000_000)


def test_preis_gilt_am_letzten_tag_noch() -> None:
    from app.ai.pricing import estimate_cost_usd

    assert estimate_cost_usd("gemini-3.6-flash", 10, 10, on=date(2026, 12, 31)).status == "OK"


def test_abgelaufener_preis_ist_unbekannt_statt_zu_billig() -> None:
    """Ab 01.01.2027 verdoppelt Google den Preis; ein alter Wert unterschluege die Haelfte."""
    from app.ai.pricing import estimate_cost_usd

    schaetzung = estimate_cost_usd("gemini-3.6-flash", 10, 10, on=date(2027, 1, 1))

    assert schaetzung.status == "COST_UNKNOWN"
    assert schaetzung.usd is None
    assert schaetzung.reason == "price_expired"


def test_preise_ohne_ablaufdatum_bleiben_unberuehrt() -> None:
    from app.ai.pricing import estimate_cost_usd

    assert estimate_cost_usd("gpt-4o", 10, 10, on=date(2030, 1, 1)).status == "OK"


# ── 2b. Die Pipeline schreibt die Sperrzeile ohne Anbieter, mit Grund ───────


async def test_pipeline_sperrzeile_ohne_anbieteretikett_mit_grund(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.budget import BudgetExceeded
    from app.analysis.pipeline import AnalysisPipeline
    from app.core.enums import AnalysisSource
    from tests.unit.test_analysis_pipeline import _btc_engine, _make_doc

    sink = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    kette = AsyncMock()
    kette.provider_name = "gemini"
    kette.model = "gemini-3.6-flash"
    kette.active_provider_name = "gemini"
    kette.analyze = AsyncMock(
        side_effect=BudgetExceeded(
            route="standard", state="LIMIT_REACHED", reason="daily_limit_reached"
        )
    )
    pipeline = AnalysisPipeline(keyword_engine=_btc_engine(), provider=kette)

    ergebnis = await pipeline.run(_make_doc())

    assert ergebnis.analysis_result is not None
    assert ergebnis.analysis_result.analysis_source == AnalysisSource.RULE
    zeilen = [json.loads(z) for z in sink.read_text("utf-8").splitlines() if z.strip()]
    sperre = [z for z in zeilen if z.get("error_type") == "BudgetExceeded"]
    assert len(sperre) == 1
    assert sperre[0]["provider"] == ""
    assert sperre[0]["model"] == ""
    assert sperre[0]["budget_decision"] == "reject:daily_limit_reached"
    assert sperre[0]["chain_position"] == -1
