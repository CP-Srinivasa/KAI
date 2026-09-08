"""LLM call telemetry — failure-rate + latency p50/p95 (B-002, Audit F-5).

Append-only JSONL per call (canonical writer pattern: frozen record ->
``append_lock`` -> append), read back tolerantly. Consumed by the dashboard
integrations surface and the B-002 re-entry capability check.

**Kostenmessung (KAI COST CONTROL v0.1, D-CORE-007).** Die Kosten werden GENAU
HIER berechnet, an der einzigen Stelle, durch die jede Telemetriezeile läuft.
Vorher war ``cost_usd`` strukturell tot: gefüllt hat es nur der LiteLLM-Header,
und LiteLLM ist aus — auf 14.886 Zeilen stand ``null``. Die Berechnung an jeden
der sieben Aufrufer zu hängen hätte sieben Wahrheiten über denselben Preis
ergeben.

Getrennt gehalten, weil es zwei verschiedene Dinge sind:

* ``cost_source="upstream"`` — der Anbieter hat den Betrag GENANNT. Das ist
  eine Abrechnung.
* ``cost_source="list_price:<version>"`` — aus Token und Listenpreis
  GERECHNET. Das ist eine Schätzung, und die Tabellenversion steht dabei,
  damit sie nachrechenbar bleibt.
* ``cost_usd=None`` + ``cost_status="COST_UNKNOWN"`` + Grund — weder noch.
  Niemals ``0.0``: eine Summe mit unsichtbaren Nullen sieht aus wie eine
  Abrechnung und ist keine.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.file_lock import append_lock
from app.storage.jsonl_io import iter_jsonl_tolerant

DEFAULT_TELEMETRY_PATH = Path("artifacts/llm_telemetry.jsonl")

#: Der Anbieter hat den Betrag selbst genannt (heute nur der LiteLLM-Header).
COST_SOURCE_UPSTREAM = "upstream"


def _cost_fields(
    *,
    cost_usd: float | None,
    model: str,
    actual_model: str | None,
    requested_model_alias: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> dict[str, Any]:
    """Kosten, Herkunft und — wenn unbekannt — der Grund dafür.

    Der Import von :mod:`app.ai.pricing` liegt bewusst IM Funktionsrumpf:
    ``app.ai.audit`` importiert dieses Modul auf Modulebene, ein Gegenimport
    oben wäre ein Zyklus. Der Kostenpfad darf die Telemetrie nicht
    zerbrechlicher machen, als sie ohne ihn wäre.
    """
    if cost_usd is not None:
        # Abrechnung schlägt Schätzung. Immer.
        return {
            "cost_usd": float(cost_usd),
            "cost_known": True,
            "cost_source": COST_SOURCE_UPSTREAM,
            "cost_status": "OK",
            "cost_reason": "",
        }
    try:
        from app.ai.pricing import estimate_cost_usd, resolve_priced_model

        preismodell = resolve_priced_model(
            requested_model_alias=requested_model_alias or model,
            actual_model=actual_model,
        )
        estimate = estimate_cost_usd(preismodell, input_tokens, output_tokens)
    except Exception:  # noqa: BLE001 — Telemetrie darf den Aufruf nie mitreissen
        return {
            "cost_usd": None,
            "cost_known": False,
            "cost_source": None,
            "cost_status": "COST_UNKNOWN",
            "cost_reason": "pricing_unavailable",
        }
    return {
        "cost_usd": estimate.usd,
        "cost_known": estimate.usd is not None,
        "cost_source": estimate.source or None,
        "cost_status": estimate.status,
        "cost_reason": estimate.reason,
    }


def record_llm_call(
    *,
    provider: str,
    model: str,
    ok: bool,
    latency_ms: float,
    role: str = "primary",
    error_type: str | None = None,
    # ``None`` resolves to DEFAULT_TELEMETRY_PATH at CALL time, so a test can
    # redirect every writer (including the ones nested deep inside providers)
    # by monkeypatching that module attribute.
    path: Path | None = None,
    # --- v2 (NEO-P-001, 2026-09-02) -------------------------------------
    # Purely additive keyword-only fields. Every v1 call site stays valid and
    # keeps writing the same v1 keys at the same place, so the dashboard
    # reader (llm_telemetry_summary) is unaffected. No new artifact stream.
    correlation_id: str | None = None,
    call_id: str | None = None,
    # --- v3 (2026-09-07): der Paarungsschluessel --------------------
    # `correlation_id` haelt eine Kette zusammen, `call_id` beschreibt
    # eine Zeile. Fuer die Frage "welche DIRECT- und welche SHADOW-Zeile
    # beschreiben denselben Aufruf?" taugt keine von beiden. Additiv:
    # jeder bestehende Aufrufer bleibt gueltig und schreibt hier `null`.
    evaluation_id: str | None = None,
    purpose: str | None = None,
    chain_position: int = 0,
    attempt: int = 1,
    error_class: str | None = None,
    http_status: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    outcome: str | None = None,
    logical_route: str | None = None,
    mode: str | None = None,
    transport: str | None = None,
    requested_model_alias: str | None = None,
    actual_provider: str | None = None,
    actual_model: str | None = None,
    identity_proven: bool = False,
    retry_count: int = 0,
    fallback_from: str | None = None,
    fallback_to: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    # --- v4 (2026-09-08, D-CORE-007): Kostenzuordnung ----------------------
    # Additiv. Jeder bestehende Aufrufer bleibt gueltig; wer nichts uebergibt,
    # bekommt den aus dem Purpose abgeleiteten use_case ueber app.ai.audit
    # bzw. "unknown" -- nie eine Vermutung.
    use_case: str | None = None,
    escalation_reason: str = "",
    #: Woher die Quelle des Inhalts stammt (Feed-/Kanalname). GETRENNT von
    #: ``provider``: dort gehoert der bezahlte Anbieter hin und sonst nichts.
    #: Der Strom trug beides im selben Feld ("CNBC" neben "openai"), und jede
    #: Aggregation ohne Anbieter-Filter war dadurch falsch.
    source: str | None = None,
    schema_status: str | None = None,
    budget_decision: str | None = None,
    circuit_state: str | None = None,
    execution_authority: bool | None = None,
    upstream_request_id: str | None = None,
) -> None:
    """Append one telemetry row. Never raises into the caller (best-effort)."""
    row: dict[str, Any] = {
        "schema_version": "v2",
        "ts": datetime.now(UTC).isoformat(),
        "provider": provider,
        "model": model,
        "role": role,
        "ok": bool(ok),
        "latency_ms": round(float(latency_ms), 3),
        "error_type": error_type,
        "correlation_id": correlation_id,
        "call_id": call_id or f"llmc_{uuid4().hex[:8]}",
        "purpose": purpose,
        "chain_position": int(chain_position),
        "attempt": int(attempt),
        "error_class": error_class,
        "http_status": http_status,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "outcome": outcome or ("success" if ok else "exhausted"),
        "evaluation_id": evaluation_id,
        "logical_route": logical_route,
        "mode": mode,
        "transport": transport or "direct",
        "requested_model_alias": requested_model_alias,
        "actual_provider": actual_provider,
        "actual_model": actual_model,
        "identity_proven": bool(identity_proven),
        "retry_count": int(retry_count),
        "fallback_from": fallback_from,
        "fallback_to": fallback_to,
        # UNKNOWN != 0, auch bei Token. Die v1-Felder ``prompt_tokens`` /
        # ``completion_tokens`` sind ``int`` und tragen 0 sowohl fuer "null
        # Token" als auch fuer "nicht mitgeteilt" -- fuer den v1-Leser bleibt
        # das so. Die v2-Felder duerfen diese Vermengung nicht erben: ein
        # fehlgeschlagener Versuch ohne Usage-Block haette sonst 0 Token
        # gezaehlt und die Auswertung haette den Verbrauch zu niedrig gesehen.
        # 0 gibt es bei einem echten Aufruf nicht, deshalb ist die Umdeutung
        # nach ``None`` hier verlustfrei.
        "input_tokens": input_tokens if input_tokens is not None else (int(prompt_tokens) or None),
        "output_tokens": (
            output_tokens if output_tokens is not None else (int(completion_tokens) or None)
        ),
        "schema_status": schema_status,
        "budget_decision": budget_decision,
        "circuit_state": circuit_state,
        "execution_authority": execution_authority,
        "upstream_request_id": upstream_request_id,
        # --- v4: Zuordnung ------------------------------------------------
        "use_case": use_case or "unknown",
        "escalation_reason": escalation_reason or "",
        "source": source,
    }
    gemessene_eingabe = row["input_tokens"]
    gemessene_ausgabe = row["output_tokens"]
    # Auch die Summe erbt UNKNOWN != 0: nur wenn BEIDE Seiten bekannt sind,
    # gibt es eine Gesamtzahl. Sonst waere 0 eine Behauptung ueber Verbrauch.
    row["total_tokens"] = (
        int(gemessene_eingabe) + int(gemessene_ausgabe)
        if gemessene_eingabe is not None and gemessene_ausgabe is not None
        else None
    )
    row.update(
        _cost_fields(
            cost_usd=cost_usd,
            model=model,
            actual_model=actual_model,
            requested_model_alias=requested_model_alias,
            input_tokens=gemessene_eingabe,
            output_tokens=gemessene_ausgabe,
        )
    )
    try:
        sink = path if path is not None else DEFAULT_TELEMETRY_PATH
        sink.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, sort_keys=True, separators=(",", ":"))
        with append_lock(sink):
            with sink.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:  # noqa: BLE001 — telemetry must never break the analysis path
        pass


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (p50 of [a,b,c,d] = b, p95 = d)."""
    if not sorted_values:
        return None
    import math

    idx = min(len(sorted_values) - 1, max(0, math.ceil(pct * len(sorted_values)) - 1))
    return sorted_values[idx]


def llm_telemetry_summary(
    window_hours: float = 24.0, path: Path = DEFAULT_TELEMETRY_PATH
) -> dict[str, Any]:
    """Failure-rate + latency percentiles over the window. Honest n=0 when empty."""
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    n = failures = 0
    latencies: list[float] = []
    if path.exists():
        for row in iter_jsonl_tolerant(path):
            try:
                ts = datetime.fromisoformat(str(row.get("ts", "")))
            except ValueError:
                continue
            if ts < cutoff:
                continue
            n += 1
            if not row.get("ok", False):
                failures += 1
            try:
                latencies.append(float(row.get("latency_ms", 0.0)))
            except (TypeError, ValueError):
                continue
    latencies.sort()
    return {
        "implemented": True,  # B-002 landed 2026-07-11 (Audit F-5)
        "window_hours": window_hours,
        "n": n,
        "failures": failures,
        "failure_rate_pct": round(100.0 * failures / n, 2) if n else None,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
    }
