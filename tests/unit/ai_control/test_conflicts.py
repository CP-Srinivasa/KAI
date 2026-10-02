from typing import Any

from app.observability.ai_control.config import LiteLLMModels
from app.observability.ai_control.conflicts import find_conflicts
from app.observability.ai_control.workloads import ProviderActivity, WorkloadKey, WorkloadStats


def models(**kw: Any) -> LiteLLMModels:
    return LiteLLMModels(_env_file=None, **kw)  # type: ignore[call-arg]


def test_route_ohne_modell_und_unbekannte_route() -> None:
    k = find_conflicts(
        route_modes={"standard": "primary", "standrad": "shadow"},
        models=models(),
        lock_matches=True,
        workloads_24h={},
        activity={},
    )
    schluessel = {c.key for c in k}
    assert "konflikt:route_ohne_modell:standard" in schluessel
    assert "konflikt:unbekannte_route:standrad" in schluessel


def test_baum_lock_und_nur_fehler_und_rueckfall() -> None:
    lite = WorkloadKey(
        "analysis", "standard", "kai-server", "litellm", "deepseek/deepseek-v4-flash"
    )
    k = find_conflicts(
        route_modes={"standard": "primary"},
        models=models(standard_model="deepseek/deepseek-v4-flash"),
        lock_matches=False,
        workloads_24h={lite: WorkloadStats(calls=12, ok=0, failures=12, fallbacks=12)},
        activity={"moonshot": ProviderActivity(schema_errors_24h=3)},
    )
    schluessel = {c.key for c in k}
    assert {
        "konflikt:baum_lock",
        "konflikt:nur_fehler:standard",
        "konflikt:rueckfall:standard",
        "konflikt:schemafehler:moonshot",
    } <= schluessel


def test_ruhiger_zustand_ohne_konflikt() -> None:
    assert (
        find_conflicts(
            route_modes={"research": "advisory"},
            models=models(research_model="moonshot/kimi-k2.6"),
            lock_matches=True,
            workloads_24h={},
            activity={},
        )
        == []
    )
