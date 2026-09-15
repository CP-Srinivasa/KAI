"""MB-06.4: laufende Aufrufe zaehlen gegen den Topf, bevor ihre Zeile geschrieben ist.

Das Budget liest den Telemetriestrom VOR dem Aufruf; die Zeile des Aufrufs
entsteht NACH ihm. Die Analyse-Pipeline laesst fuenf Aufrufe je Batch parallel
laufen (``app/analysis/pipeline.py::_MAX_CONCURRENT``). Alle fuenf sahen bis
6e5a7b6e denselben Stand — bei einer Alert-Reserve von 20 Aufrufen konnten so
bis zu vier Aufrufe zu viel durchgehen, und die Decke des normalen Topfes um
bis zu vier Mittelwerte ueberschossen werden. Der Codex-Plan "MindBlower"
(15.09.2026) nannte den Punkt; das Red-Team bestaetigte ihn am Code
(ART-B-003).

Die Regel: ein zugelassener Aufruf reserviert seinen Topf im Prozess, bis er
zurueck ist — mit dem Mittelwert des Topfes als Kosten, oder nur als Aufruf,
wenn es noch keinen Mittelwert gibt. Dieselbe Doktrin wie im Budget selbst:
unbekannt heisst gezaehlt, nicht null.

Grenze: prozessweit. Zwei Prozesse (kai-server und ein CLI-Lauf) sehen
einander weiterhin erst ueber den Strom.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ai.audit import budget_intent_scope
from app.ai.budget import BudgetExceeded
from app.ai.runtime import inflight_reservations, invoke, reset_inflight_reservations
from app.ai.spend import reset_spend_cache
from app.core.ai_cost_settings import reset_ai_cost_settings


@pytest.fixture(autouse=True)
def _reserven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.25")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_USD", "0.15")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_MAX_CALLS", "20")
    monkeypatch.setenv("APP_AI_BUDGET_VALIDATION_RESERVE_USD", "0.05")
    monkeypatch.setenv("APP_AI_BUDGET_VALIDATION_RESERVE_MAX_CALLS", "10")
    reset_ai_cost_settings()
    reset_inflight_reservations()
    yield
    reset_inflight_reservations()
    reset_ai_cost_settings()


def _verbraucht(sink: Path, *, usd: float, pot: str = "normal", calls: int = 1) -> None:
    for _ in range(calls):
        zeile = {
            "ts": datetime.now(UTC).isoformat(),
            "provider": "openai",
            "model": "gpt-4o",
            "actual_model": "gpt-4o",
            "ok": True,
            "chain_position": 0,
            "purpose": "analysis",
            "use_case": "news_intelligence",
            "cost_usd": usd,
            "cost_status": "known",
            "budget_pot": pot,
        }
        with sink.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile) + "\n")
    reset_spend_cache()


async def _ruf(sink: Path, *, alert_eligible: bool, freigabe: asyncio.Event | None = None) -> str:
    async def direct_call() -> str:
        if freigabe is not None:
            await freigabe.wait()
        return "analysiert"

    with budget_intent_scope(alert_eligible=alert_eligible, validation=False):
        routed = await invoke(
            purpose="analysis",
            direct_call=direct_call,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=None,
            telemetry_path=sink,
        )
    return routed.value


async def _laufend(sink: Path, *, alert_eligible: bool) -> tuple[asyncio.Task[str], asyncio.Event]:
    """Einen Aufruf starten und ihn im Aufruf festhalten — er ist dann 'unterwegs'."""
    freigabe = asyncio.Event()
    task = asyncio.create_task(_ruf(sink, alert_eligible=alert_eligible, freigabe=freigabe))
    await asyncio.sleep(0)
    assert not task.done(), "der erste Aufruf muss im Flug sein, nicht abgelehnt"
    return task, freigabe


async def test_der_letzte_reserve_aufruf_wird_nicht_doppelt_vergeben(tmp_path: Path) -> None:
    """19 von 20 Reserve-Aufrufen verbraucht, Normaltopf zu: EIN Aufruf passt noch."""
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=1.10)
    _verbraucht(sink, usd=0.005, pot="alert_reserve", calls=19)

    erster, freigabe = await _laufend(sink, alert_eligible=True)
    assert inflight_reservations()["alert_reserve"]["calls"] == 1

    with pytest.raises(BudgetExceeded) as fehler:
        await _ruf(sink, alert_eligible=True)
    assert fehler.value.reason == "alert_reserve_exhausted"

    freigabe.set()
    assert await erster == "analysiert"
    assert inflight_reservations() == {}


async def test_die_reservierung_endet_mit_dem_aufruf(tmp_path: Path) -> None:
    """Ohne Telemetriezeile des ersten Aufrufs (Direktpfad im Test) ist der Platz danach
    wieder frei — die Reservierung ist eine Klammer um den Aufruf, kein Zaehler."""
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=1.10)
    _verbraucht(sink, usd=0.005, pot="alert_reserve", calls=19)

    erster, freigabe = await _laufend(sink, alert_eligible=True)
    freigabe.set()
    await erster

    assert await _ruf(sink, alert_eligible=True) == "analysiert"


async def test_die_decke_des_normaltopfes_wird_prospektiv_um_laufende_aufrufe_geprueft(
    tmp_path: Path,
) -> None:
    """Decke 1,05 (1,25 − 0,15 − 0,05). Gebucht 0,90 ueber 9 Aufrufe, Mittel 0,10.

    Erster Aufruf: 0,90 + 0,10 = 1,00 ≤ 1,05 → erlaubt und reserviert.
    Zweiter, waehrend der erste laeuft: 1,00 + 0,10 = 1,10 > 1,05 → gesperrt.
    """
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=0.10, calls=9)

    erster, freigabe = await _laufend(sink, alert_eligible=False)
    assert inflight_reservations()["normal"] == {"calls": 1, "known_calls": 1, "usd": 0.1}

    with pytest.raises(BudgetExceeded) as fehler:
        await _ruf(sink, alert_eligible=False)
    assert fehler.value.reason == "normal_budget_exhausted"

    freigabe.set()
    await erster


async def test_ohne_mittelwert_zaehlt_die_reservierung_als_aufruf_ohne_kosten(
    tmp_path: Path,
) -> None:
    """Reserve noch nie benutzt: kein Mittelwert, also nur die Aufrufgrenze."""
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=1.10)

    erster, freigabe = await _laufend(sink, alert_eligible=True)
    assert inflight_reservations()["alert_reserve"] == {"calls": 1, "known_calls": 0, "usd": 0.0}
    freigabe.set()
    await erster


async def test_ein_fehlschlag_gibt_die_reservierung_frei(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=0.10, calls=9)

    async def kaputt() -> str:
        raise RuntimeError("provider down")

    with budget_intent_scope(alert_eligible=False, validation=False), pytest.raises(RuntimeError):
        await invoke(
            purpose="analysis",
            direct_call=kaputt,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=None,
            telemetry_path=sink,
        )

    assert inflight_reservations() == {}


async def test_ein_abgelehnter_aufruf_reserviert_nichts(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=1.10)

    with pytest.raises(BudgetExceeded):
        await _ruf(sink, alert_eligible=False)

    assert inflight_reservations() == {}
