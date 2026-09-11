"""Zwei Zeitgrenzen auf derselben Wartezeit — die kleinere gewinnt.

Am 2026-09-11 auf dem Pi gemessen: der Research-Brief-Smoke brach zweimal nach
exakt 30,0 s mit HTTP 408 ab. KAI wartete geduldig 180 s, aber der Proxy gab
nach seinem globalen ``request_timeout: 30`` auf. Die Route konnte deshalb
konstruktionsbedingt nie eine lange Antwort liefern — genau das, wofuer sie
gebaut wurde.

Der Fehler war nicht, dass ein Wert falsch stand. Der Fehler war, dass zwei
Werte dasselbe beschreiben und niemand sie aneinander gebunden hat.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.ai.config import InferenceSettings

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "litellm.yaml"


def _geladen() -> dict:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def _route(name: str) -> dict:
    for eintrag in _geladen()["model_list"]:
        if eintrag["model_name"] == name:
            return eintrag["litellm_params"]
    raise AssertionError(f"Route {name} steht nicht in config/litellm.yaml")


def test_beide_seiten_geben_der_research_route_dieselbe_zeit() -> None:
    kai_seite = InferenceSettings().route_timeout_seconds["research"]
    proxy_seite = _route("kai-kimi-research")["timeout"]
    assert float(proxy_seite) == float(kai_seite), (
        f"KAI wartet {kai_seite}s, der Proxy bricht nach {proxy_seite}s ab — die "
        "kleinere Zahl gewinnt, und die Route kann ihre Laenge nie ausschoepfen."
    )


def test_die_anderen_routen_erben_die_lange_zeit_nicht() -> None:
    """Nur Research braucht die lange Leine. Der Rest bleibt bei 30 s."""
    assert _geladen()["litellm_settings"]["request_timeout"] == 30
    for eintrag in _geladen()["model_list"]:
        if eintrag["model_name"] == "kai-kimi-research":
            continue
        assert "timeout" not in eintrag["litellm_params"], eintrag["model_name"]


def test_die_kai_seitige_zeit_bleibt_gedeckelt() -> None:
    """180 s ist die Ausnahme, nicht der Anfang einer Reihe."""
    zeiten = InferenceSettings().route_timeout_seconds
    assert set(zeiten) == {"research"}
    assert 0.0 < zeiten["research"] <= 300.0
