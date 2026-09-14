"""Die Alert-Reserve bleibt unter dem Tageslimit (D-274, Randfall aus #954).

Mit gesetzter Reserve prueft ``decide_pot`` einen Reserveaufruf bisher nur
gegen den Reservetopf. Liegt der Normaltopf bereits UEBER seiner Decke --
etwa weil die Reserve mitten am Tag nach mehr als 1,10 USD aktiviert wurde --,
kann der Tag real bis zu Reserve ueber dem Tageslimit enden. Das Tageslimit ist
aber die Zusage nach aussen; die Reserve verteilt es nur, sie erhoeht es nicht.

Die Regel hier: die Summe ALLER Toepfe des Tages haelt das Tageslimit, auch fuer
alert-faehige Aufrufe, und zwar vorausschauend wie jede andere Decke. Genau
so haengt die Reserve schon am Monatslimit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.ai.budget import (
    BudgetPolicy,
    BudgetState,
    ReservePolicy,
    decide_pot,
)
from app.ai.spend import reset_spend_cache
from app.core.ai_cost_settings import reset_ai_cost_settings

_POLICY = BudgetPolicy(daily_limit_usd=1.25, monthly_limit_usd=25.0)
_RESERVEN = ReservePolicy(alert_reserve_usd=0.15, alert_reserve_max_calls=20)


def _toepfe(normal: float, reserve: float = 0.0, exempt: float = 0.0) -> dict[str, BudgetState]:
    return {
        "normal": BudgetState(normal, 1 if normal else 0, 0),
        "alert_reserve": BudgetState(reserve, 1 if reserve else 0, 0),
        "validation": BudgetState(0.0, 0, 0),
        "exempt": BudgetState(exempt, 1 if exempt else 0, 0),
    }


def _urteil(**toepfe: float) -> Any:
    return decide_pot(
        route="standard",
        pots=_toepfe(**toepfe),
        policy=_POLICY,
        reserves=_RESERVEN,
        monthly=BudgetState(5.0, 10, 0),
        alert_eligible=True,
    )


# ── Reine Entscheidung ─────────────────────────────────────────────────────


def test_reserve_zahlt_solange_der_tag_unter_dem_limit_bleibt() -> None:
    urteil = _urteil(normal=1.12, reserve=0.05)

    assert urteil.allowed is True
    assert urteil.pot == "alert_reserve"


def test_normaltopf_ueber_der_decke_verbraucht_die_reserve_mit() -> None:
    """Der Randfall: 1,20 im Normaltopf -- frueher haette die Reserve weitere
    0,15 gezahlt, der Tag waere bei 1,35 geendet. Jetzt endet er bei 1,25:
    die ersten 0,05 aus der Reserve laufen noch, danach ist der Tag voll."""
    assert _urteil(normal=1.20, reserve=0.0).allowed is True
    urteil = _urteil(normal=1.20, reserve=0.05)

    assert urteil.allowed is False
    assert urteil.pot == "alert_reserve"
    assert urteil.reason == "daily_limit_reached"


def test_tageslimit_greift_vorausschauend() -> None:
    urteil = decide_pot(
        route="standard",
        pots=_toepfe(normal=1.12, reserve=0.12),
        policy=_POLICY,
        reserves=_RESERVEN,
        alert_eligible=True,
        estimated_request_cost_usd=0.02,
    )

    # Reserve 0,12 + 0,02 = 0,14 passt noch; Tag 1,24 + 0,02 = 1,26 nicht.
    assert urteil.allowed is False
    assert urteil.reason == "daily_limit_reached"


def test_genau_am_tageslimit_ist_nichts_mehr_frei() -> None:
    assert _urteil(normal=1.10, reserve=0.15).allowed is False


def test_reservegrenze_bleibt_die_erste_antwort_unter_dem_tageslimit() -> None:
    """Reserve voll, Tag noch nicht: der Grund bleibt die Reserve."""
    urteil = decide_pot(
        route="standard",
        pots=_toepfe(normal=1.00, reserve=0.15),
        policy=_POLICY,
        reserves=_RESERVEN,
        alert_eligible=True,
    )

    assert urteil.allowed is False
    assert urteil.reason == "alert_reserve_exhausted"


def test_exempt_zaehlt_zum_tag_wie_vor_v2() -> None:
    """``critical`` ist von der SPERRE ausgenommen, nicht vom Tageslimit --
    die v1-Zusage rechnete den ganzen Tag."""
    assert _urteil(normal=1.05, reserve=0.05, exempt=0.20).allowed is False


def test_ohne_tageslimit_kein_tagesdeckel() -> None:
    urteil = decide_pot(
        route="standard",
        pots=_toepfe(normal=5.0),
        policy=BudgetPolicy(daily_limit_usd=None, monthly_limit_usd=25.0),
        reserves=_RESERVEN,
        alert_eligible=True,
    )

    assert urteil.allowed is True


def test_critical_bleibt_ausgenommen() -> None:
    urteil = decide_pot(
        route="critical",
        pots=_toepfe(normal=1.30),
        policy=_POLICY,
        reserves=_RESERVEN,
        alert_eligible=False,
    )

    assert urteil.allowed is True
    assert urteil.pot == "exempt"


# ── Am echten Aufruf und in der Anzeige ────────────────────────────────────


@pytest.fixture
def _reserve_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.25")
    monkeypatch.setenv("APP_AI_BUDGET_MONTHLY_USD", "25.00")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_USD", "0.15")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_MAX_CALLS", "20")
    monkeypatch.delenv("APP_AI_BUDGET_VALIDATION_RESERVE_USD", raising=False)
    monkeypatch.delenv("APP_AI_BUDGET_VALIDATION_RESERVE_MAX_CALLS", raising=False)
    reset_ai_cost_settings()
    reset_spend_cache()
    yield
    reset_ai_cost_settings()
    reset_spend_cache()


def _gebucht(sink: Path, *, usd: float, pot: str = "normal") -> None:
    zeile = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": "openai",
        "model": "gpt-4o",
        "actual_model": "gpt-4o",
        "ok": True,
        "chain_position": 0,
        "correlation_id": f"llm_{pot}_{usd}",
        "purpose": "analysis",
        "use_case": "news_intelligence",
        "input_tokens": 1000,
        "output_tokens": 100,
        "cost_usd": usd,
        "cost_status": "OK",
        "budget_pot": pot,
    }
    with sink.open("a", encoding="utf-8") as f:
        f.write(json.dumps(zeile) + "\n")
    reset_spend_cache()


async def test_runtime_lehnt_den_reserveaufruf_ueber_dem_tageslimit_ab(
    tmp_path: Path, _reserve_env: Any
) -> None:
    from app.ai.audit import budget_intent_scope
    from app.ai.budget import BudgetExceeded
    from app.ai.runtime import invoke, reset_environment_settings

    reset_environment_settings()
    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=1.30)

    async def direkt() -> str:
        return "bezahlt"

    with pytest.raises(BudgetExceeded) as fehler, budget_intent_scope(alert_eligible=True):
        await invoke(
            purpose="analysis",
            direct_call=direkt,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=None,
            telemetry_path=sink,
        )

    assert fehler.value.reason == "daily_limit_reached"


def test_anzeige_nennt_das_tageslimit_als_grund(tmp_path: Path, _reserve_env: Any) -> None:
    from app.ai.health import _budget_block, cost_block

    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=1.30)

    kosten = cost_block(path=sink)
    budget = _budget_block(kosten, [])

    assert kosten["alert_eligible_call_allowed"] is False
    assert kosten["alert_eligible_call_reason"] == "daily_limit_reached"
    assert budget["alert_capability_for_new_documents"] == "unreachable"
    assert budget["alert_capability_reason"] == "daily_limit_reached"
