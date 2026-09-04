"""Der Vertragskern der AI-Control-Plane (ADR 0017), Sprint 1.

Jeder Test hier steht gegen einen benannten Defekt des ersten LiteLLM-Anlaufs.
Wo das so ist, sagt der Docstring welchen — sonst wäre in sechs Monaten nicht
mehr erkennbar, ob eine Zeile eine Zusicherung ist oder eine Gewohnheit.
"""

from __future__ import annotations

import pytest

from app.ai.audit import Purpose
from app.ai.models import (
    AttemptTrace,
    InferenceResult,
    cost_known_rate,
    total_cost_usd,
)
from app.ai.modes import (
    DEFAULT_MODE,
    MODES,
    graduated_routes,
    has_execution_authority,
    is_mode,
    parse_mode,
    resolve_mode,
    unknown_route_keys,
)
from app.ai.routes import ROUTES, is_route, route_for

# --------------------------------------------------------------------------
# Routen — eine Absicht, kein Transport, und kein zweites SSOT.
# --------------------------------------------------------------------------


def test_jeder_purpose_hat_genau_eine_route() -> None:
    """Vollständigkeit statt Rückfallwert.

    Ohne diesen Test liefe ein neu hinzugefügter Purpose still als ``standard``
    mit — und niemand hätte je entschieden, dass er das soll.
    """
    from typing import get_args

    for purpose in get_args(Purpose):
        assert route_for(purpose) in ROUTES


def test_ein_unbekannter_purpose_faellt_auf_statt_durchzurutschen() -> None:
    with pytest.raises(KeyError):
        route_for("gibtsnicht")  # type: ignore[arg-type]


def test_intent_ist_kritisch_und_consensus_denkt_nach() -> None:
    """Die beiden Zuordnungen, die eine Begründung haben und nicht offensichtlich sind."""
    assert route_for("intent") == "critical"
    assert route_for("consensus") == "reasoning"
    assert route_for("stt") == "stt"


def test_is_route_erkennt_fremdwerte() -> None:
    assert is_route("bulk")
    assert not is_route("reasonning")
    assert not is_route(None)
    assert not is_route(3)


# --------------------------------------------------------------------------
# Modi — die Regel, die wichtiger ist als die Modi selbst.
# --------------------------------------------------------------------------


def test_ohne_konfiguration_ist_nichts_an() -> None:
    assert DEFAULT_MODE == "off"
    for route in ROUTES:
        assert resolve_mode(route) == "off"


def test_ein_globales_primary_allein_aktiviert_nichts() -> None:
    """Der Defekt „implizite Production-Aktivierung", direkt adressiert.

    Im ersten Anlauf nahm ein globaler Schalter beim Umlegen jede Route mit,
    auch die nie gemessenen. Hier deckelt der globale Wert nur.
    """
    for route in ROUTES:
        assert resolve_mode(route, ceiling="primary") == "off"
    assert graduated_routes(ceiling="primary") == ()


def test_primary_entsteht_nur_wenn_die_route_selbst_es_sagt() -> None:
    per_route = {"bulk": "primary"}
    assert resolve_mode("bulk", per_route=per_route, ceiling="primary") == "primary"
    assert resolve_mode("critical", per_route=per_route, ceiling="primary") == "off"
    assert graduated_routes(per_route=per_route, ceiling="primary") == ("bulk",)


def test_der_deckel_stuft_herunter_aber_niemals_hoch() -> None:
    per_route = {"bulk": "primary", "standard": "shadow"}
    assert resolve_mode("bulk", per_route=per_route, ceiling="shadow") == "shadow"
    assert resolve_mode("standard", per_route=per_route, ceiling="primary") == "shadow"


def test_ein_globales_off_legt_alles_gleichzeitig_still() -> None:
    """Der Weg nach unten ist EIN Schalter — das ist die Zwischenfall-Zusicherung."""
    per_route = dict.fromkeys(ROUTES, "primary")
    assert graduated_routes(per_route=per_route, ceiling="primary") == ROUTES
    assert graduated_routes(per_route=per_route, ceiling="off") == ()
    for route in ROUTES:
        assert resolve_mode(route, per_route=per_route, ceiling="off") == "off"


@pytest.mark.parametrize("wert", ["primarie", "", "  ", None, 1, "PRIMARY_", object()])
def test_ein_tippfehler_aktiviert_nichts(wert: object) -> None:
    assert parse_mode(wert) == "off"
    assert resolve_mode("bulk", per_route={"bulk": wert}, ceiling="primary") == "off"


def test_grossschreibung_und_leerraum_sind_kein_tippfehler() -> None:
    assert parse_mode("  PRIMARY  ") == "primary"
    assert parse_mode("Shadow") == "shadow"


def test_is_mode_und_modes_bleiben_konsistent() -> None:
    assert MODES == ("off", "shadow", "primary")
    for mode in MODES:
        assert is_mode(mode)
    assert not is_mode("halb")


def test_ein_verschriebener_routenschluessel_faellt_auf() -> None:
    """Sonst glaubte der Operator, er habe graduiert, und nichts geschah."""
    assert unknown_route_keys({"reasonning": "primary", "bulk": "shadow"}) == ("reasonning",)
    assert unknown_route_keys({}) == ()
    assert unknown_route_keys(None) == ()


def test_nur_primary_darf_etwas_bewirken() -> None:
    assert has_execution_authority("primary")
    assert not has_execution_authority("shadow")
    assert not has_execution_authority("off")


# --------------------------------------------------------------------------
# Versuche und Ergebnisse — und „ich weiß es nicht".
# --------------------------------------------------------------------------


def _attempt(**over: object) -> AttemptTrace:
    base: dict[str, object] = {
        "transport": "litellm",
        "requested_model": "gpt-4o-mini",
        "latency_ms": 120.0,
        "actual_provider": "openai",
        "actual_model": "gpt-4o-mini",
        "cost_usd": 0.0012,
    }
    base.update(over)
    return AttemptTrace(**base)  # type: ignore[arg-type]


def test_unbekannte_kosten_sind_nicht_null() -> None:
    """Der teuerste Einzeldefekt des ersten Anlaufs.

    ``sum(a.cost_usd or 0.0 ...)`` liess ein Tagesbudget aus lauter Nullen
    bestehen und trotzdem überschritten werden.
    """
    bekannt = [_attempt(cost_usd=0.001), _attempt(cost_usd=0.002)]
    assert total_cost_usd(bekannt) == pytest.approx(0.003)

    gemischt = [_attempt(cost_usd=0.001), _attempt(cost_usd=None)]
    assert total_cost_usd(gemischt) is None, "eine unbekannte Position macht die Summe unbekannt"

    assert total_cost_usd([]) is None


def test_cost_known_rate_misst_die_luecke_statt_sie_zu_fuellen() -> None:
    assert cost_known_rate([]) is None
    assert cost_known_rate([_attempt(), _attempt(cost_usd=None)]) == pytest.approx(0.5)
    assert cost_known_rate([_attempt(cost_usd=None)]) == 0.0


def test_identitaet_gilt_erst_wenn_der_upstream_sich_selbst_nennt() -> None:
    """Der Alias ist eine Anforderung, keine Messung."""
    assert _attempt().identity_proven
    assert not _attempt(actual_provider="", actual_model="gpt-4o-mini").identity_proven
    assert not _attempt(actual_provider="openai", actual_model="").identity_proven


def test_ein_untergeschobenes_modell_faellt_auf() -> None:
    assert not _attempt().model_substituted
    assert _attempt(actual_model="gpt-4o").model_substituted
    # Ohne bewiesene Identität ist „untergeschoben" keine Aussage, sondern Raten.
    assert not _attempt(actual_provider="", actual_model="gpt-4o").model_substituted


def test_ein_geglueckter_schatten_bleibt_ein_schatten() -> None:
    """Erfolg ersetzt keine Graduation — sonst wäre die implizite Aktivierung zurück."""
    ergebnis = InferenceResult(
        route="standard", purpose="analysis", mode="shadow", attempts=(_attempt(),)
    )
    assert ergebnis.ok
    assert not ergebnis.execution_authority


def test_autoritaet_haengt_am_modus_nicht_am_ausgang() -> None:
    gescheitert = InferenceResult(
        route="bulk",
        purpose="analysis",
        mode="primary",
        attempts=(_attempt(error_class="timeout"),),
    )
    assert not gescheitert.ok
    assert gescheitert.execution_authority
    assert gescheitert.error_class == "timeout"


def test_ein_ergebnis_ohne_versuche_ist_weder_ok_noch_teuer() -> None:
    leer = InferenceResult(route="bulk", purpose="analysis", mode="off")
    assert not leer.ok
    assert leer.total_cost_usd is None
    assert leer.latency_ms == 0.0
    assert leer.error_class is None
    assert not leer.identity_proven


def test_das_ergebnis_summiert_latenz_und_erbt_die_letzte_identitaet() -> None:
    ergebnis = InferenceResult(
        route="critical",
        purpose="intent",
        mode="primary",
        attempts=(
            _attempt(latency_ms=100.0, error_class="rate_limit"),
            _attempt(latency_ms=250.0, actual_provider="anthropic", actual_model="claude"),
        ),
        fell_back_to_direct=True,
    )
    assert ergebnis.latency_ms == pytest.approx(350.0)
    assert ergebnis.ok
    assert ergebnis.identity_proven
    assert ergebnis.fell_back_to_direct


def test_die_route_eines_ergebnisses_passt_zu_seinem_purpose() -> None:
    """Kein erzwungener Vertrag im Datentyp, aber der erwartete Normalfall."""
    for purpose in ("analysis", "chat", "intent", "stt", "consensus"):
        route = route_for(purpose)  # type: ignore[arg-type]
        ergebnis = InferenceResult(route=route, purpose=purpose, mode="off")  # type: ignore[arg-type]
        assert ergebnis.route == route


# --------------------------------------------------------------------------
# Keine zweite Wahrheit: die Fehlertaxonomie bleibt, wo sie ist.
# --------------------------------------------------------------------------


def test_die_fehlerklassen_kommen_weiterhin_aus_audit() -> None:
    """ADR 0017 verbietet eine zweite Control-Plane-Wahrheit.

    ``app/ai/audit.py`` besitzt die Taxonomie seit D-CORE-001. Ein eigenes
    ``errors.py`` daneben wäre genau die Doppelung, gegen die das ADR steht —
    dieser Test hält fest, dass es sie nicht gibt.
    """
    from pathlib import Path

    import app.ai.audit as audit

    assert hasattr(audit, "classify_error")
    assert hasattr(audit, "is_retryable_error")
    assert not (Path(audit.__file__).parent / "errors.py").exists()
