"""Provider health derived from the telemetry stream — no probe calls.

NEO-F-009: ``/health`` reported liveness and the dashboard reported *configuration*
("active" because a key is set). Neither says whether a provider actually works.

This snapshot is computed purely from ``artifacts/llm_telemetry.jsonl``. That is
a deliberate constraint, not a shortcut: a probe call would cost money, add a
failure domain to a health endpoint, and still only prove that one synthetic
request worked. Real traffic is the better evidence.

No recent calls means unavailable evidence, never proven provider failure or health.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Eine Zaehlebene, ein Definitionsort: die Primitive wohnen in app.ai.spend.
from app.ai.spend import dedupe_chain_levels, is_ai_row, row_ts
from app.observability.llm_telemetry import (
    DEFAULT_TELEMETRY_PATH,
    _percentile,  # identical nearest-rank definition; re-implementing it would drift
)
from app.storage.jsonl_io import iter_jsonl_tolerant

#: Rueckwaertskompatible Namen. Die Definitionen wohnen seit D-CORE-007 in
#: ``app.ai.spend``, damit Gesundheit und Budget nicht zwei Meinungen ueber
#: dieselbe Grundgesamtheit haben.
_is_ai_row = is_ai_row
_row_ts = row_ts

CHAIN_SOURCE = "app/analysis/factory.py"

#: Steht in jeder Kostenantwort. Ein Betrag ohne diesen Satz waere eine
#: Abrechnung; er ist keine.
_COST_NOTE = "estimates from list prices; billing amounts are separate"

_DEGRADED_AT_PCT = 10.0
_DOWN_AT_PCT = 50.0
_DOWN_AT_CONSECUTIVE_FAILURES = 3


def _load_rows(path: Path, window_hours: float) -> list[dict[str, Any]]:
    """In-window rows, oldest first, with outer wrapper rows de-duplicated.

    An ``chain_position == -1`` row spans a whole fallback chain. When the same
    correlation id also produced per-attempt rows, counting both would double
    every ensemble call, so the wrapper is dropped. v1 rows (no correlation id)
    are always kept — they have no per-attempt counterpart.
    """
    if not path.exists():
        return []
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=window_hours)
    rows: list[dict[str, Any]] = []
    for row in iter_jsonl_tolerant(path):
        if not isinstance(row, dict):
            continue
        if not _is_ai_row(row):
            continue
        ts = _row_ts(row)
        if ts is None or not cutoff <= ts <= now:
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("ts", "")))

    return dedupe_chain_levels(rows)


def _classify_state(calls: int, failures: int, consecutive_failures: int) -> str:
    if calls == 0:
        return "unavailable"
    if consecutive_failures >= _DOWN_AT_CONSECUTIVE_FAILURES:
        return "down"
    rate = 100.0 * failures / calls
    if rate > _DOWN_AT_PCT:
        return "down"
    if rate >= _DEGRADED_AT_PCT:
        return "degraded"
    return "ok"


def _provider_block(
    name: str, rows: list[dict[str, Any]], *, configured: bool, enabled: bool
) -> dict[str, Any]:
    calls = len(rows)
    failures = sum(1 for row in rows if not row.get("ok", False))

    latencies: list[float] = []
    for row in rows:
        try:
            latencies.append(float(row.get("latency_ms", 0.0)))
        except (TypeError, ValueError):
            continue
    latencies.sort()

    last_ok_ts: str | None = None
    last_error_class: str | None = None
    for row in rows:
        if row.get("ok", False):
            last_ok_ts = str(row.get("ts")) if row.get("ts") else last_ok_ts
        else:
            error_class = row.get("error_class")
            last_error_class = str(error_class) if error_class else last_error_class

    consecutive_failures = 0
    for row in reversed(rows):
        if row.get("ok", False):
            break
        consecutive_failures += 1

    observed = _classify_state(calls, failures, consecutive_failures)
    if not configured:
        state, reason = "not_configured", "not_in_factory_configuration"
    elif not enabled:
        state, reason = "disabled", "not_in_enabled_chain"
    elif not calls:
        state, reason = "unavailable", "no_recent_calls"
    else:
        state = "error" if observed in {"down", "degraded"} else "ok"
        reason = "recent_calls_" + observed
    return {
        "name": name,
        "configured": configured,
        "state": state,
        "status_reason": reason,
        "observed_state": observed,
        "calls": calls,
        "failures": failures,
        "failure_rate_pct": round(100.0 * failures / calls, 2) if calls else None,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "last_ok_ts": last_ok_ts,
        "last_error_class": last_error_class,
        "consecutive_failures": consecutive_failures,
    }


def cost_block(path: Path | None = None) -> dict[str, Any]:
    """Kostenlage fuer ``/health/ai`` — Schaetzung, und sie sagt es selbst.

    Zwei Fenster (heute UTC, laufender Monat UTC) statt eines Rollfensters:
    ein Tagesbudget wird um Mitternacht zurueckgesetzt, nicht 24 Stunden nach
    dem letzten Aufruf. Ein Rollfenster haette den Zustand nie zurueckgesetzt.

    ``*_known`` im Namen ist kein Schmuck: die Summe ist eine UNTERGRENZE,
    solange ``unknown_cost_calls_* > 0``. Beide Zahlen stehen deshalb
    nebeneinander und werden nirgends getrennt ausgewiesen.

    Kein Probe-Call, kein neuer Strom, kein Dashboard — derselbe Vertrag wie
    der Rest dieser Datei.
    """
    from app.ai.pricing import PRICE_TABLE_VERSION
    from app.ai.spend import current_budget_status

    try:
        status, heute, monat = current_budget_status(path=path)
    except Exception:  # noqa: BLE001 - eine Gesundheitsanzeige stirbt nicht an Kosten
        return {
            "status": "COST_UNKNOWN",
            "reason": "spend_unreadable",
            "price_table_version": PRICE_TABLE_VERSION,
            "note": _COST_NOTE,
        }
    return {
        "today_usd_known": round(heute.known_cost_usd, 6),
        "month_usd_known": round(monat.known_cost_usd, 6),
        "unknown_cost_calls_today": heute.unknown_calls,
        "unknown_cost_calls_month": monat.unknown_calls,
        "calls_today": heute.calls,
        "calls_month": monat.calls,
        "daily_limit_usd": status.policy.daily_limit_usd,
        "monthly_limit_usd": status.policy.monthly_limit_usd,
        "warn_pct": status.warn_pct,
        "unknown_max_calls_per_day": status.unknown_max_calls_per_day,
        "status": status.state,
        "reason": status.reason,
        "blocks_routine": status.blocks_routine,
        "top_provider": heute.top_provider or monat.top_provider,
        "top_use_case": heute.top_use_case or monat.top_use_case,
        "fully_accounted_today": heute.fully_accounted,
        "price_table_version": PRICE_TABLE_VERSION,
        "note": _COST_NOTE,
    }


def ai_health_snapshot(
    window_hours: float = 24.0,
    path: Path | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Per-provider health over *window_hours*, plus the configured chain.

    Args:
        window_hours: look-back window over the telemetry stream.
        path: telemetry sink; ``None`` uses the default at call time.
        settings: AppSettings; ``None`` loads them. Only read, never written.

    Returns:
        ``{"ai": {"chain": ..., "window_hours": ..., "providers": [...],
        "cost": {...}}}`` — ``cost`` ist ADDITIV: ein bestehender Leser, der
        den Schluessel nicht kennt, bleibt gueltig.
    """
    from app.analysis.factory import describe_primary_chain, describe_shadow_chain

    if settings is None:
        from app.core.settings import get_settings

        settings = get_settings()

    primary = describe_primary_chain(settings)
    shadow = describe_shadow_chain(settings)
    configured = set(primary) | set(shadow)
    credentials = {
        name: bool(getattr(settings.providers, key, ""))
        for name, key in {
            "openai": "openai_api_key",
            "anthropic": "anthropic_api_key",
            "gemini": "gemini_api_key",
            "grok": "xai_api_key",
        }.items()
    }

    sink = path if path is not None else DEFAULT_TELEMETRY_PATH
    rows = _load_rows(sink, window_hours)

    by_provider: dict[str, list[dict[str, Any]]] = {name: [] for name in credentials}
    for row in rows:
        provider = row.get("provider")
        if not isinstance(provider, str):
            continue
        by_provider.setdefault(provider, []).append(row)

    # Chain order first (stable, reviewable), then anything else seen in traffic.
    ordered = primary + [name for name in shadow if name not in primary]
    ordered += sorted(name for name in by_provider if name not in ordered)

    bloecke = [
        _provider_block(
            name,
            # A chain name without its own credential entry and without
            # traffic has no bucket: /health/ai must not 500 over that.
            by_provider.get(name, []),
            configured=credentials.get(name, name in configured),
            enabled=name in configured,
        )
        for name in ordered
    ]
    # Schluessel vorhanden ist nicht Provider verfuegbar.
    #
    # `primary` und `shadow` kommen aus der Factory, und die bildet sie
    # ausschliesslich aus der Key-Praesenz. Ein Konto ohne Guthaben, ein
    # widerrufener Schluessel, ein abgeschaltetes Modell -- in allen drei Faellen
    # steht der Provider weiter in der Kette, und wer nur die Kette liest, haelt
    # ihn fuer einsatzbereit. Genau das ist am 2026-09-08 aufgefallen, als das
    # Anthropic-Auto-Aufladen abgeschaltet wurde: `chain.shadow=[anthropic]`
    # haette unveraendert dagestanden, waehrend jeder Aufruf scheitert.
    #
    # `observed` beantwortet die andere Frage und erfindet dafuer nichts: es
    # liest die Zustaende, die die Provider-Bloecke oben aus der Telemetrie
    # gebildet haben. Eine Quelle, zwei Lesarten -- kein zweiter Wahrheitsstand.
    # Ohne Aufrufe im Fenster steht dort `unavailable`, nicht `ok`: unbeobachtet
    # ist nicht gesund.
    zustaende = {block["name"]: block["state"] for block in bloecke}
    return {
        "ai": {
            "chain": {
                "primary": primary,
                "shadow": shadow,
                "source": CHAIN_SOURCE,
                "derived_from": "credentials_only",
                "observed": {name: zustaende.get(name, "unavailable") for name in primary + shadow},
            },
            "window_hours": window_hours,
            "cost": cost_block(path),
            "providers": bloecke,
        }
    }
