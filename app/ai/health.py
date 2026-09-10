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

# Der Marker fuer lokale Abweisungen wohnt neben der Ausnahme, die ihn erzeugt.
from app.ai.budget import LOCAL_REFUSAL_ERROR_TYPES

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


def _is_local_refusal(row: dict[str, Any]) -> bool:
    """Wurde dieser Aufruf abgelehnt, bevor ein Provider kontaktiert wurde?

    Am 2026-09-09 stand auf kai-pi5 ``openai: state=error,
    status_reason=recent_calls_down`` — bei 308 erfolgreichen Aufrufen und null
    Anbieterfehlern. Die fuenf "Fehler" waren Budget-Abweisungen: das
    Tageslimit war um 12:04 UTC erreicht, jeder weitere Routineaufruf wurde
    lokal verworfen (``http_status: null``, ``prompt_tokens: 0``, 200 ms). Das
    Gateway hat davor gewarnt und die typisierte Ausnahme genau dafuer gebaut
    (``app/ai/gateway.py`` "ein Budgetende darf nicht wie ein Ausfall
    aussehen"); die Gesundheitsschicht las den Marker nur nicht.
    """
    error_type = row.get("error_type")
    return isinstance(error_type, str) and error_type in LOCAL_REFUSAL_ERROR_TYPES


def _provider_block(
    name: str, rows: list[dict[str, Any]], *, configured: bool, enabled: bool
) -> dict[str, Any]:
    # Erreichbarkeit wird AUSSCHLIESSLICH aus echten Anbieterkontakten
    # abgeleitet. Eine lokale Abweisung hat den Anbieter nie erreicht: sie
    # gehoert weder in ``failures`` noch in die Latenzverteilung (eine
    # 200-ms-Absage verschoebe p50 und p95 nach unten und liesse einen
    # langsamen Anbieter schnell aussehen).
    contacts = [row for row in rows if not _is_local_refusal(row)]
    local_refusals = len(rows) - len(contacts)

    calls = len(contacts)
    failures = sum(1 for row in contacts if not row.get("ok", False))

    latencies: list[float] = []
    for row in contacts:
        try:
            latencies.append(float(row.get("latency_ms", 0.0)))
        except (TypeError, ValueError):
            continue
    latencies.sort()

    last_ok_ts: str | None = None
    last_error_class: str | None = None
    for row in contacts:
        if row.get("ok", False):
            last_ok_ts = str(row.get("ts")) if row.get("ts") else last_ok_ts
        else:
            error_class = row.get("error_class")
            last_error_class = str(error_class) if error_class else last_error_class

    consecutive_failures = 0
    for row in reversed(contacts):
        if row.get("ok", False):
            break
        consecutive_failures += 1

    observed = _classify_state(calls, failures, consecutive_failures)
    if not configured:
        state, reason = "not_configured", "not_in_factory_configuration"
    elif not enabled:
        state, reason = "disabled", "not_in_enabled_chain"
    elif not calls:
        # Ohne Kontakt gibt es keinen gemessenen Anbieterzustand. Der Grund
        # dafuer ist aber nicht derselbe: "es rief niemand an" und "wir haben
        # den Anruf selbst verweigert" sind zwei verschiedene Lagen, und nur
        # die zweite ist von uns behebbar.
        state = "unavailable"
        reason = "no_provider_contact_local_refusal" if local_refusals else "no_recent_calls"
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
        # Nicht verschwiegen, nur nicht als Anbieterfehler gezaehlt: die
        # Abweisungen bleiben sichtbar, damit niemand aus ``calls`` schliesst,
        # es habe keine Arbeit gegeben.
        "local_refusals": local_refusals,
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
        # Zeilen aus der Zeit VOR der Messung. Eigenes Feld, weil sie eine
        # andere Frage beantworten als ``unknown_cost_calls_*``: dort hat die
        # Messung versagt, hier hat sie nie stattgefunden. Sie loesen kein
        # COST_UNKNOWN aus (app/ai/spend.py::is_unmetered_legacy_row).
        "unmetered_legacy_calls_today": heute.unmetered_legacy_calls,
        "unmetered_legacy_calls_month": monat.unmetered_legacy_calls,
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
        # Die Toepfe (Budget-Policy v2). Sie stehen hier auch dann, wenn keine
        # Reserve gesetzt ist -- dann liegt aller Verbrauch in ``normal``, und
        # genau das soll ablesbar sein. Eine Reserve, deren Stand nur im Code
        # existiert, waere fuer den Operator dasselbe wie keine.
        "pots": _topf_block(heute, status),
        # Mit gesetzten Reserven endet gewoehnliche Arbeit an der Decke des
        # NORMALEN Topfes, nicht am Tageslimit. Ohne dieses Feld meldete die
        # Gesundheitsanzeige "nicht gesperrt", waehrend die Routine bereits
        # stand -- genau die lautlose Lage, gegen die Budget-Policy v2
        # geschrieben ist.
        "normal_pot_exhausted": _normaler_topf_erschoepft(heute, status),
        "price_table_version": PRICE_TABLE_VERSION,
        "note": _COST_NOTE,
    }


def _alert_capability_block(routine_blocked: bool) -> dict[str, Any]:
    """Kann ein NEU analysiertes Dokument ueberhaupt noch einen Alert ausloesen?

    Der Befund, der diesen Block erzwungen hat (Spur A, 09./10.09.2026): nach
    Erreichen des Tagesbudgets laeuft die Analyse weiter — nur nicht mehr ueber
    den LLM. Der Zufluss bleibt gleich, ``is_analyzed`` bleibt bei ~100 %, und
    genau deshalb sah jede Pruefung gruen aus, waehrend 373 bzw. 43 Dokumente in
    Folge KEINEN Alert mehr ausloesen konnten.

    Die Ursache ist rechnerisch, nicht zufaellig: der Regelpfad kommt hoechstens
    auf ``raw = 0.575`` (siehe ``rule_path_raw_ceiling``), das Alert-Gate liegt
    bei ``ALERT_GATE_RAW = 0.615``. Ueber 44.018 regelanalysierte Dokumente
    wurde nie eine Prioritaet ueber 6 vergeben; alle 11.863 Dokumente ab
    Prioritaet 7 kamen aus ``external_llm``.

    Bewusst NUR beobachtend: dieser Block aendert weder ein Limit noch eine
    Schwelle noch das Routing. Er sagt, was gilt — die Entscheidung, was daraus
    folgt, gehoert dem Operator.
    """
    from app.analysis.scoring import (
        ALERT_GATE_RAW,
        rule_path_can_reach_alert_gate,
        rule_path_priority_ceiling,
        rule_path_raw_ceiling,
    )

    erreichbar = rule_path_can_reach_alert_gate()
    if not routine_blocked:
        zustand, grund = "ok", ""
    elif erreichbar:
        # Budget blockiert, aber der Regelpfad kaeme durch: dann ist die
        # Alert-Faehigkeit nicht verloren, nur die Analysetiefe.
        zustand, grund = "degraded", "budget_blocked_rule_path_still_passes_gate"
    else:
        zustand, grund = "unreachable", "budget_blocked_rule_path_below_alert_gate"
    return {
        # "Neu" ist woertlich: bereits analysierte Dokumente mit hoher
        # Prioritaet bleiben unberuehrt. Verloren ist die Faehigkeit, aus dem
        # laufenden Zufluss noch einen Alert zu erzeugen.
        "alert_capability_for_new_documents": zustand,
        "alert_capability_reason": grund,
        "alert_gate_raw": round(ALERT_GATE_RAW, 6),
        "rule_path_raw_ceiling": round(rule_path_raw_ceiling(), 6),
        "rule_path_priority_ceiling": rule_path_priority_ceiling(),
    }
def _normaler_topf_erschoepft(heute: Any, status: Any) -> bool:
    """Steht die gewoehnliche Arbeit? ``False`` auch dann, wenn es keine
    Reserven gibt -- dann ist das Tageslimit die einzige Decke, und dafuer gibt
    es ``blocks_routine`` bereits."""
    from app.core.ai_cost_settings import get_ai_cost_settings

    try:
        reserven = get_ai_cost_settings().reserve_policy
    except Exception:  # noqa: BLE001 - eine Gesundheitsanzeige stirbt nicht an Kosten
        return False
    if not reserven.any_reserve_set:
        return False
    decke = reserven.normal_ceiling_usd(status.policy.daily_limit_usd)
    if decke is None:
        return False
    return heute.pot_states()["normal"].booked_usd >= decke


def _topf_block(heute: Any, status: Any) -> dict[str, Any]:
    """Verbrauch je Topf, mit dem jeweiligen Limit daneben.

    ``remaining_usd`` bleibt ``None``, sobald der Topf unbelegte Aufrufe
    enthaelt: der wahre Verbrauch liegt dann ueber der Summe, und eine
    Restgroesse auszuweisen waere eine Genauigkeit, die es nicht gibt --
    dieselbe Regel wie in ``app.ai.budget.headroom_usd``.
    """
    from app.ai.budget import headroom_usd
    from app.core.ai_cost_settings import get_ai_cost_settings

    try:
        reserven = get_ai_cost_settings().reserve_policy
    except Exception:  # noqa: BLE001 - eine Gesundheitsanzeige stirbt nicht an Kosten
        reserven = None
    grenzen: dict[str, float | None] = {
        "normal": (
            reserven.normal_ceiling_usd(status.policy.daily_limit_usd)
            if reserven is not None
            else status.policy.daily_limit_usd
        ),
        "alert_reserve": reserven.alert_reserve_usd if reserven is not None else None,
        "validation": reserven.validation_reserve_usd if reserven is not None else None,
        "exempt": None,
    }
    block: dict[str, Any] = {}
    for topf, zustand in heute.pot_states().items():
        limit = grenzen.get(topf)
        block[topf] = {
            "booked_usd": round(zustand.booked_usd, 6),
            "calls": zustand.total_calls,
            "unknown_cost_calls": zustand.unknown_calls,
            "limit_usd": limit,
            "remaining_usd": headroom_usd(zustand, limit),
        }
    return block


def _budget_block(cost: dict[str, Any], provider_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Der Budgetzustand, getrennt vom Anbieterzustand und in dessen Sprache.

    Beide Lagen sind operativ verschieden: ein Anbieterausfall wartet man ab
    oder umgeht ihn ueber die Kette, ein erreichtes Limit ist eine
    Operator-Entscheidung. Wer sie in einem Feld zusammenzieht, schickt den
    Operator zum falschen Anbieter — genau das ist am 2026-09-09 passiert.
    """
    status = cost.get("status")
    return {
        "budget_state": str(status).lower() if isinstance(status, str) else "unknown",
        "budget_status_reason": str(cost.get("reason") or ""),
        # ODER, nicht nur das eine: mit Reserven steht die Routine an der Decke
        # des normalen Topfes, ohne sie am Tageslimit. Der Operator fragt hier
        # nach der Wirkung, nicht nach der Bauart.
        "routine_calls_blocked": bool(cost.get("blocks_routine", False))
        or bool(cost.get("normal_pot_exhausted", False)),
        # Wie viele Aufrufe die Abweisung im Fenster tatsaechlich getroffen hat.
        # Ohne diese Zahl bliebe "limit_reached" eine Ansage ohne Wirkung.
        "local_refusals_in_window": sum(
            int(block.get("local_refusals", 0)) for block in provider_blocks
        ),
        **_alert_capability_block(bool(cost.get("blocks_routine", False))),
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
    # EINE Berechnung, zwei Lesarten: der Budgetblock projiziert den bereits
    # berechneten Kostenblock, er rechnet nicht nach. Ein zweiter Rechenweg
    # waere ein zweiter Wahrheitsstand ueber derselben Zahl.
    kosten = cost_block(path)
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
            "cost": kosten,
            "budget": _budget_block(kosten, bloecke),
            "providers": bloecke,
        }
    }
