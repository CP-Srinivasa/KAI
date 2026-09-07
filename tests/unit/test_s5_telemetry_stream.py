"""Ein Strom, nicht zwei — und UNKNOWN bleibt UNKNOWN (ADR 0017, Luecke D).

``artifacts/llm_telemetry.jsonl`` ist der einzige Ort, an dem ein LLM-Aufruf
Spuren hinterlaesst. Ein zweiter Strom waere nicht doppelte Sicherheit, sondern
zwei Zahlen fuer dieselbe Frage — und in der Auswertung gewinnt dann die, die
gerade jemand aufgeschlagen hat.

Der zweite Punkt ist unscheinbarer und teurer: **fehlende Kosten sind nicht 0**.
Ein Versuch, dessen Preis der Upstream nicht mitgeteilt hat, mit 0.0 zu
verbuchen, macht aus einer Wissensluecke eine Tatsache. Die Tagessumme sieht
danach vertrauenswuerdig aus und ist zu niedrig.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.gateway import execute_async
from app.ai.models import AttemptResult, AttemptTrace
from app.ai.retry import RetryPolicy

PFLICHTFELDER = (
    "correlation_id",
    "call_id",
    "purpose",
    "logical_route",
    "mode",
    "transport",
    "requested_model_alias",
    "actual_provider",
    "actual_model",
    "identity_proven",
    "attempt",
    "retry_count",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "error_class",
    "outcome",
    "execution_authority",
)


def _zeilen(pfad: Path) -> list[dict[str, object]]:
    if not pfad.exists():
        return []
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]


async def _lauf(
    pfad: Path,
    *,
    mode: str,
    traces: list[AttemptTrace],
    max_attempts: int = 3,
) -> object:
    rest = list(traces)

    async def direct() -> AttemptResult[str]:
        return AttemptResult(trace=AttemptTrace("direct", "gpt-4o", 1.0), value="direkt")

    async def lite() -> AttemptResult[str]:
        trace = rest.pop(0) if rest else traces[-1]
        return AttemptResult(trace=trace, value="litellm" if trace.ok else None)

    return await execute_async(
        purpose="chat",
        alias="kai-standard",
        direct_call=direct,
        litellm_call=lite,
        per_route={"standard": mode},
        ceiling=mode,
        retry_policy=RetryPolicy(max_attempts=max_attempts, base_backoff_s=0.0, max_jitter_s=0.0),
        correlation_id="corr-1",
        telemetry_path=pfad,
    )


def _ok(cost: float | None = 0.002) -> AttemptTrace:
    return AttemptTrace(
        transport="litellm",
        requested_model="kai-standard",
        latency_ms=12.0,
        actual_provider="openai",
        actual_model="gpt-4o-mini",
        input_tokens=11,
        output_tokens=5,
        cost_usd=cost,
    )


def _fehler(klasse: str = "timeout", status: int | None = None) -> AttemptTrace:
    return AttemptTrace(
        transport="litellm",
        requested_model="kai-standard",
        latency_ms=9.0,
        error_class=klasse,  # type: ignore[arg-type]
        detail={"status_code": status} if status is not None else {},
    )


async def test_jeder_physische_versuch_bekommt_genau_eine_zeile(tmp_path: Path) -> None:
    """Ein Retry wird nicht weggemittelt: er kostet Geld, Zeit und Kontingent."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    await _lauf(pfad, mode="shadow", traces=[_fehler(), _fehler(), _ok()])

    zeilen = _zeilen(pfad)
    assert len(zeilen) == 3, "drei Versuche, drei Zeilen"
    assert [z["attempt"] for z in zeilen] == [1, 2, 3]
    assert [z["retry_count"] for z in zeilen] == [0, 1, 2]
    assert [z["outcome"] for z in zeilen] == ["fallthrough", "fallthrough", "success"]


async def test_die_pflichtfelder_stehen_in_jeder_zeile(tmp_path: Path) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    await _lauf(pfad, mode="shadow", traces=[_ok()])

    (zeile,) = _zeilen(pfad)
    fehlend = [feld for feld in PFLICHTFELDER if feld not in zeile]
    assert not fehlend, fehlend
    assert zeile["correlation_id"] == "corr-1"
    assert zeile["logical_route"] == "standard"
    assert zeile["mode"] == "shadow"
    assert zeile["transport"] == "litellm"
    assert zeile["requested_model_alias"] == "kai-standard"
    assert zeile["actual_provider"] == "openai"
    assert zeile["actual_model"] == "gpt-4o-mini"
    assert zeile["identity_proven"] is True


async def test_unbekannte_kosten_werden_null_nicht_nullkomma(tmp_path: Path) -> None:
    """0.0 waere eine Behauptung ueber Geld, das niemand gezaehlt hat."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    await _lauf(pfad, mode="shadow", traces=[_ok(cost=None)])

    (zeile,) = _zeilen(pfad)
    assert zeile["cost_usd"] is None
    assert zeile["cost_known"] is False
    assert zeile["cost_usd"] != 0


async def test_unbekannte_token_werden_nicht_zu_null_gerundet(tmp_path: Path) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    # `auth` wird nicht wiederholt: genau ein Versuch, genau eine Zeile.
    await _lauf(pfad, mode="shadow", traces=[_fehler("auth")])

    (zeile,) = _zeilen(pfad)
    assert zeile["input_tokens"] is None
    assert zeile["output_tokens"] is None


async def test_schatten_traegt_keine_ausfuehrungsautoritaet(tmp_path: Path) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    await _lauf(pfad, mode="shadow", traces=[_ok()])

    (zeile,) = _zeilen(pfad)
    assert zeile["execution_authority"] is False
    assert zeile["role"] == "shadow"


async def test_der_rueckfall_auf_direct_ist_in_der_zeile_zu_sehen(tmp_path: Path) -> None:
    """Ohne diese beiden Felder waere ein Fallback in der Auswertung unsichtbar."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    await _lauf(pfad, mode="primary", traces=[_fehler("auth")], max_attempts=3)

    (zeile,) = _zeilen(pfad)
    assert zeile["outcome"] == "exhausted", "auth wird nicht wiederholt"
    assert zeile["fallback_from"] == "litellm"
    assert zeile["fallback_to"] == "direct"
    assert zeile["execution_authority"] is True


async def test_es_entsteht_kein_zweiter_strom(tmp_path: Path) -> None:
    """Der Transport schreibt nicht selbst — die Control-Plane schreibt fuer ihn."""
    pfad = tmp_path / "llm_telemetry.jsonl"
    await _lauf(pfad, mode="shadow", traces=[_fehler(), _ok()])

    # Die `.lock`-Datei gehoert zum kanonischen Schreibmuster (append_lock),
    # sie traegt keine Daten. Ein zweiter Datenstrom waere eine zweite .jsonl.
    stroeme = sorted(p.name for p in tmp_path.rglob("*.jsonl") if p.is_file())
    assert stroeme == ["llm_telemetry.jsonl"], stroeme
    fremd = [
        p.name for p in tmp_path.rglob("*") if p.is_file() and p.suffix not in (".jsonl", ".lock")
    ]
    assert not fremd, fremd


def test_der_kanonische_pfad_ist_unveraendert() -> None:
    from app.observability.llm_telemetry import DEFAULT_TELEMETRY_PATH

    assert DEFAULT_TELEMETRY_PATH == Path("artifacts/llm_telemetry.jsonl")


def test_es_gibt_genau_einen_schreiber() -> None:
    """Zwei Schreiber waeren zwei Formate, sobald einer ein Feld ergaenzt."""
    import ast

    schreiber: list[str] = []
    for datei in Path("app").rglob("*.py"):
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Call) and ast.unparse(knoten.func).endswith(
                "DEFAULT_TELEMETRY_PATH.open"
            ):
                schreiber.append(str(datei))
    assert not schreiber, schreiber

    quelle = Path("app/observability/llm_telemetry.py").read_text(encoding="utf-8")
    assert quelle.count("def record_llm_call(") == 1


@pytest.mark.parametrize("mode", ["off", "shadow", "primary"])
async def test_off_schreibt_keine_transportzeile(tmp_path: Path, mode: str) -> None:
    pfad = tmp_path / "llm_telemetry.jsonl"
    await _lauf(pfad, mode=mode, traces=[_ok()])
    assert len(_zeilen(pfad)) == (0 if mode == "off" else 1)
