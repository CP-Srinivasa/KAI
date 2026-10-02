"""Vertrag ``ai-control/v1`` -- liest nur Dateien, bewertet mit ``app.ai.control.states``.

Spec §4-§6. Jedes ``null`` in ``summary`` und ``connections.proxy.state`` traegt einen
Grund in ``null_reasons`` (No-Fake, wie ``ai-transport/v1``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.ai.circuit_export import ServiceCircuit, read_circuits
from app.ai.config import InferenceSettings
from app.ai.control import history, protocol
from app.ai.control.accounts import read_accounts
from app.ai.control.config import ControlPaths, ControlThresholds, LiteLLMModels
from app.ai.control.conflicts import find_conflicts
from app.ai.control.states import Signals, State, Verdict, classify
from app.ai.control.workloads import (
    ProviderActivity,
    WorkloadKey,
    WorkloadStats,
    aggregate,
    provider_activity,
)
from app.ai.modes import resolve_mode
from app.ai.routes import ROUTES

SCHEMA: Final = "ai-control/v1"
ACCOUNTS_STALE: Final = timedelta(hours=3)
#: Aufgabe -> Route -> Anzeigename. Feste Reihenfolge auf der Seite.
TASKS: Final = (
    ("analysis", "standard", "Analyse"),
    ("chat", "standard", "Chat"),
    ("intent", "critical", "Freitext"),
    ("stt", "stt", "Sprache"),
    ("consensus", "reasoning", "Konsens"),
    ("research", "research", "Research"),
)
#: Anbieter hinter LiteLLM -> Modellpraefix in KAI_LITELLM_<ROUTE>_MODEL.
LITELLM_UPSTREAMS: Final = {"deepseek": "deepseek/", "moonshot": "moonshot/", "gemini": "gemini/"}
PROVIDERS: Final = ("openai", "deepseek", "moonshot", "xai", "anthropic", "gemini")


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).isoformat() if ts else None


def _verdict(v: Verdict) -> dict[str, Any]:
    return {"state": v.state.value, "reason": v.reason, "since": _iso(v.since)}


@dataclass(frozen=True)
class _Lage:
    """Was alle Abschnitte gemeinsam brauchen -- einmal gelesen."""

    now: datetime
    rows: list[dict[str, Any]]
    heute: dict[WorkloadKey, WorkloadStats]
    tag24: dict[WorkloadKey, WorkloadStats]
    stunde: dict[WorkloadKey, WorkloadStats]
    aktiv: dict[str, ProviderActivity]
    kreise: list[ServiceCircuit]
    modus: Callable[[str], str]
    budget_leer: bool
    morgen: datetime


def _summary(
    lage: _Lage, status: Any, today: Any, month: Any, gruende: dict[str, str]
) -> tuple[dict[str, Any], datetime | None]:
    from app.ai.spend import month_projection

    now = lage.now
    mitternacht = now.replace(hour=0, minute=0, second=0, microsecond=0)
    projektion = month_projection(month, monthly_limit_usd=status.policy.monthly_limit_usd, now=now)
    ende = history.budget_exhausted_at(lage.rows, day=now.date().isoformat())
    limit = status.policy.daily_limit_usd
    schaetzung: str | None = None
    stunden = max((now - mitternacht).total_seconds() / 3600, 0.25)
    rate = today.known_cost_usd / stunden
    if ende is None and limit is not None and rate > 0:
        reicht = now + timedelta(hours=max(limit - today.known_cost_usd, 0.0) / rate)
        schaetzung = _iso(reicht) if reicht.date() == now.date() else "tagesende"
    summary: dict[str, Any] = {
        "today_usd": round(today.known_cost_usd, 4),
        "today_limit_usd": limit,
        "month_usd": round(month.known_cost_usd, 4),
        "month_limit_usd": status.policy.monthly_limit_usd,
        "projected_month_usd": projektion.projected_month_usd,
        "budget_state": status.state,
        "budget_exhausted_at": _iso(ende),
        "budget_end_estimate": schaetzung,
        "calls_today": today.calls,
        "tokens_in_today": today.input_tokens,
        "tokens_out_today": today.output_tokens,
    }
    if limit is None:
        gruende["summary.today_limit_usd"] = "kein Tageslimit gesetzt (APP_AI_BUDGET_DAILY_USD)"
    if status.policy.monthly_limit_usd is None:
        gruende["summary.month_limit_usd"] = "kein Monatslimit gesetzt (APP_AI_BUDGET_MONTHLY_USD)"
    if projektion.projected_month_usd is None:
        gruende["summary.projected_month_usd"] = "zu wenig Monatsdaten fuer eine Hochrechnung"
    if ende is None:
        gruende["summary.budget_exhausted_at"] = "Tagesbudget heute noch nicht erschoepft"
    if schaetzung is None:
        gruende["summary.budget_end_estimate"] = (
            "bereits erschoepft" if ende else "ohne Tageslimit oder ohne Verbrauch keine Schaetzung"
        )
    return summary, ende


def _proxy(
    transport: dict[str, Any] | None, now: datetime, gruende: dict[str, str]
) -> dict[str, Any]:
    if not transport:
        gruende["connections.proxy.state"] = "KI-Transport-Status nicht lesbar"
        return {"state": None, "reason": None, "since": None, "version": None, "lock_matches": None}
    t = transport.get("transport") or {}
    lebt = bool(t.get("proxy_alive"))
    v = classify(
        Signals(now=now, configured=True, proxy_down=not lebt, last_ok=now if lebt else None)
    )
    return {
        **_verdict(v),
        "version": t.get("version"),
        "lock_matches": t.get("lock_matches"),
        "verified_at": t.get("verified_at"),
        "status_code": t.get("proxy_status_code"),
    }


def _kreis(kreise: list[ServiceCircuit], alias: str, zustand: str) -> bool:
    return any(
        k.get("alias") == alias and k.get("state") == zustand
        for c in kreise
        if not c.stale
        for k in c.keys
    )


def _aliases(
    lage: _Lage, inference: InferenceSettings, models: LiteLLMModels, proxy_down: bool
) -> list[dict[str, Any]]:
    aus: list[dict[str, Any]] = []
    for route in ROUTES:
        modell = getattr(models, f"{route}_model", "")
        m = lage.modus(route)
        alias = inference.route_aliases.get(route, route)
        lite = [
            st
            for key, st in lage.heute.items()
            if key.route == route and key.transport == "litellm"
        ]
        v = classify(
            Signals(
                now=lage.now,
                configured=m != "off" and bool(modell),
                disabled_reason="Route aus" if m == "off" else "kein Modell konfiguriert",
                proxy_down=proxy_down,
                circuit_open=_kreis(lage.kreise, alias, "open"),
                paused_reason="Circuit prueft Wiederanlauf"
                if _kreis(lage.kreise, alias, "half_open")
                else "",
                last_ok=max((s.last_ok for s in lite if s.last_ok), default=None),
            )
        )
        aus.append(
            {
                "alias": alias,
                "route": route,
                "mode": m,
                "upstream_model": modell or None,
                **_verdict(v),
            }
        )
    return aus


def _providers(
    lage: _Lage,
    models: LiteLLMModels,
    providers_configured: dict[str, bool],
    konto_je: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    genutzt = {
        p
        for p, praefix in LITELLM_UPSTREAMS.items()
        if any(
            getattr(models, f"{r}_model", "").startswith(praefix) and lage.modus(r) != "off"
            for r in ROUTES
        )
    }
    aus: list[dict[str, Any]] = []
    for name in PROVIDERS:
        a = lage.aktiv.get(name)
        if name == "openai":
            an, grund = providers_configured.get("openai", False), "kein OPENAI_API_KEY"
        elif name in LITELLM_UPSTREAMS:
            an, grund = name in genutzt, "keine aktive Route nutzt diesen Anbieter"
        else:
            hat = providers_configured.get(name, False)
            an = hat and a is not None and a.calls_24h > 0
            grund = "Schluessel gesetzt, von KAI nicht genutzt" if hat else "kein Schluessel"
        konto = konto_je.get(name) or {}
        leer = konto.get("balance") is not None and float(konto["balance"]) <= 0
        budget = lage.budget_leer and name == "openai"
        v = classify(
            Signals(
                now=lage.now,
                configured=an,
                disabled_reason=grund,
                consecutive_failures=a.consecutive_failures if a else 0,
                calls_1h=a.calls_1h if a else 0,
                failures_1h=a.failures_1h if a else 0,
                balance_exhausted=leer or bool(a and a.quota_errors_24h),
                paused_reason="Tagesbudget leer" if budget else "",
                paused_until=lage.morgen if budget else None,
                last_ok=a.last_ok if a else None,
            )
        )
        aus.append(
            {
                "name": name,
                "kind": "direct" if name in ("openai", "xai", "anthropic") else "litellm",
                **_verdict(v),
                "calls_24h": a.calls_24h if a else 0,
                "failures_24h": a.failures_24h if a else 0,
                "circuits": [
                    {"service": c.service, "stale": c.stale, "keys": c.keys} for c in lage.kreise
                ],
            }
        )
    return aus


def _workloads(lage: _Lage) -> list[dict[str, Any]]:
    from app.analysis.llm_sparfenster import resolve_sparfenster

    fenster = resolve_sparfenster()
    aus: list[dict[str, Any]] = []
    for zweck, route, titel in TASKS:
        m = lage.modus(route)
        teile = [(k, s) for k, s in lage.heute.items() if k.purpose == zweck]
        teile24 = [s for k, s in lage.tag24.items() if k.purpose == zweck]
        stunde = [s for k, s in lage.stunde.items() if k.purpose == zweck]
        aufrufe24 = sum(s.calls for s in teile24)
        fehler24 = sum(s.failures for s in teile24)
        spar = fenster.verdict(source=None, at=lage.now) if zweck == "analysis" else None
        budget = lage.budget_leer and route != "critical"
        pausiert = "Tagesbudget leer" if budget else ("Sparfenster aktiv" if spar else "")
        v = classify(
            Signals(
                now=lage.now,
                configured=True,
                paused_reason=pausiert,
                paused_until=lage.morgen if budget else None,
                calls_1h=sum(s.calls for s in stunde),
                failures_1h=sum(s.failures for s in stunde),
                last_ok=max((s.last_ok for _, s in teile if s.last_ok), default=None),
            )
        )
        aus.append(
            {
                "purpose": zweck,
                "title": titel,
                "route": route,
                "mode": m,
                **_verdict(v),
                "calls_today": sum(s.calls for _, s in teile),
                "tokens_in_today": sum(s.input_tokens for _, s in teile),
                "tokens_out_today": sum(s.output_tokens for _, s in teile),
                "approx_kb_today": round(sum(s.approx_kb for _, s in teile), 1),
                "cost_today_usd": round(sum(s.known_cost_usd for _, s in teile), 4),
                "unknown_cost_calls_today": sum(s.unknown_cost_calls for _, s in teile),
                "failure_rate_24h": round(fehler24 / aufrufe24, 4) if aufrufe24 else None,
                "fallbacks_today": sum(s.fallbacks for _, s in teile),
                "sparfenster": fenster.mode if zweck == "analysis" else None,
                "parts": [
                    {
                        "service": k.service,
                        "transport": k.transport,
                        "model": k.model,
                        "calls": s.calls,
                        "cost_usd": round(s.known_cost_usd, 4),
                        "last_call": _iso(s.last_call),
                        "top_sources": sorted(s.sources.items(), key=lambda x: -x[1])[:5],
                    }
                    for k, s in teile
                ],
            }
        )
    return aus


def _accounts(lage: _Lage, konten: list[dict[str, Any]], veraltet: bool) -> list[dict[str, Any]]:
    aus: list[dict[str, Any]] = []
    for k in konten:
        a = lage.aktiv.get(str(k.get("provider")))
        tagesrate = a.cost_7d_usd / 7 if a and a.cost_7d_usd > 0 else None
        bal = k.get("balance")
        reichweite = float(bal) / tagesrate if (bal is not None and tagesrate) else None
        aus.append(
            {
                **k,
                "runway_days": round(reichweite, 1) if reichweite is not None else None,
                "stale": veraltet,
            }
        )
    return aus


def build_snapshot(
    *,
    now: datetime,
    paths: ControlPaths,
    inference: InferenceSettings,
    transport: dict[str, Any] | None,
    thresholds: ControlThresholds,
    models: LiteLLMModels,
    providers_configured: dict[str, bool],
    budget: tuple[Any, Any, Any] | None = None,
) -> dict[str, Any]:
    from app.ai.spend import current_budget_status, load_rows

    gruende: dict[str, str] = {}
    rows = load_rows(paths.telemetry)
    mitternacht = now.replace(hour=0, minute=0, second=0, microsecond=0)
    status, today, month = budget or current_budget_status(path=paths.telemetry, now=now)
    ceiling = inference.mode_ceiling if inference.enabled else "off"

    def modus(route: str) -> str:
        return str(resolve_mode(route, per_route=inference.route_modes, ceiling=ceiling))  # type: ignore[arg-type]

    vorlaeufig = _Lage(
        now=now,
        rows=rows,
        heute=aggregate(rows, since=mitternacht, until=now),
        tag24=aggregate(rows, since=now - timedelta(hours=24), until=now),
        stunde=aggregate(rows, since=now - timedelta(hours=1), until=now),
        aktiv=provider_activity(rows, now=now),
        kreise=read_circuits(now=now, directory=paths.runtime_dir),
        modus=modus,
        budget_leer=False,
        morgen=mitternacht + timedelta(days=1),
    )
    summary, ende = _summary(vorlaeufig, status, today, month, gruende)
    lage = _Lage(
        **{
            **vorlaeufig.__dict__,
            "budget_leer": status.state == "LIMIT_REACHED" or ende is not None,
        }
    )

    proxy = _proxy(transport, now, gruende)
    proxy_down = bool(transport) and not ((transport or {}).get("transport") or {}).get(
        "proxy_alive"
    )
    aliase = _aliases(lage, inference, models, proxy_down)
    konten, konten_stand = read_accounts(paths.accounts)
    konto_je = {str(k.get("provider")): k for k in konten}
    anbieter = _providers(lage, models, providers_configured, konto_je)
    workloads = _workloads(lage)
    veraltet = konten_stand is None or now - konten_stand > ACCOUNTS_STALE
    konten_aus = _accounts(lage, konten, veraltet)

    hinweise = _attention(
        lage=lage,
        thresholds=thresholds,
        inference=inference,
        models=models,
        transport=transport,
        summary=summary,
        ende=ende,
        proxy=proxy,
        aliase=aliase,
        anbieter=anbieter,
        workloads=workloads,
        konten=konten_aus,
        veraltet=veraltet,
    )
    zaehler: dict[str, int] = {}
    for obj in [proxy, *aliase, *anbieter, *workloads]:
        if obj.get("state"):
            zaehler[obj["state"]] = zaehler.get(obj["state"], 0) + 1
    summary["state_counts"] = zaehler
    return {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "summary": summary,
        "attention": hinweise,
        "connections": {"proxy": proxy, "aliases": aliase, "providers": anbieter},
        "workloads": workloads,
        "accounts": konten_aus,
        "accounts_written_at": _iso(konten_stand),
        "protocol": protocol.read(paths.protocol)[:30],
        "null_reasons": gruende,
    }


def _attention(
    *,
    lage: _Lage,
    thresholds: ControlThresholds,
    inference: InferenceSettings,
    models: LiteLLMModels,
    transport: dict[str, Any] | None,
    summary: dict[str, Any],
    ende: datetime | None,
    proxy: dict[str, Any],
    aliase: list[dict[str, Any]],
    anbieter: list[dict[str, Any]],
    workloads: list[dict[str, Any]],
    konten: list[dict[str, Any]],
    veraltet: bool,
) -> list[dict[str, Any]]:
    now = lage.now
    hinweise: list[dict[str, Any]] = []

    def hinweis(
        key: str,
        title: str,
        detail: str,
        *,
        severity: str = "warn",
        min_age_min: int = 0,
        action: dict[str, Any] | None = None,
    ) -> None:
        hinweise.append(
            {
                "key": key,
                "severity": severity,
                "title": title,
                "detail": detail,
                "since": _iso(now),
                "min_age_min": min_age_min,
                "action": action or {"kind": "details"},
            }
        )

    if veraltet:
        hinweis(
            "konten_veraltet",
            "Kontoabfrage veraltet",
            "Keine frische Guthaben-Abfrage seit ueber 3 h -- laeuft kai-ai-control.timer?",
        )
    for k in konten:
        p = str(k.get("provider"))
        if k.get("status") == "fehler":
            hinweis(f"konto_abfrage:{p}", f"Kontoabfrage {p} fehlgeschlagen", str(k.get("error")))
        roh, runway = k.get("balance"), k.get("runway_days")
        if roh is None:
            continue
        bal = float(roh)
        knapp = bal < thresholds.balance_min_usd or (
            runway is not None and runway < thresholds.runway_min_days
        )
        if knapp:
            reicht = f" · reicht ~{runway:.0f} Tage" if runway is not None else ""
            hinweis(
                f"guthaben:{p}",
                f"Guthaben {p} knapp",
                f"{bal:.2f} ${reicht}",
                action={"kind": "topup", "url": k.get("topup_url")},
            )
    if ende is not None and ende.hour < thresholds.early_budget_hour_utc:
        hinweis(
            f"budget_frueh:{now.date().isoformat()}",
            "Tagesbudget frueh aufgebraucht",
            f"seit {ende:%H:%M} UTC nur noch Regelanalyse",
        )
    prognose, monatslimit = summary["projected_month_usd"], summary["month_limit_usd"]
    if prognose is not None and monatslimit is not None and prognose > monatslimit:
        hinweis(
            f"monat:{now:%Y-%m}",
            "Monatsprognose ueber Limit",
            f"{prognose:.2f} $ > {monatslimit:.2f} $",
        )
    primary = any(lage.modus(r) == "primary" for r in ROUTES)
    if proxy.get("state") == State.GESTOERT.value:
        hinweis(
            "gestoert:proxy:litellm",
            "LiteLLM-Proxy gestoert",
            str(proxy.get("reason")),
            severity="crit" if primary else "warn",
            min_age_min=thresholds.proxy_down_minutes,
        )
    for a in aliase:
        if a["state"] == State.GESTOERT.value:
            hinweis(
                f"gestoert:alias:{a['alias']}",
                f"Route {a['route']} gestoert",
                a["reason"],
                severity="crit" if a["mode"] == "primary" else "warn",
            )
    for anb in anbieter:
        if anb["state"] == State.GESTOERT.value:
            hinweis(
                f"gestoert:anbieter:{anb['name']}",
                f"Anbieter {anb['name']} gestoert",
                anb["reason"],
            )
    for w in workloads:
        if w["state"] == State.GESTOERT.value:
            hinweis(
                f"gestoert:aufgabe:{w['purpose']}",
                f"{w['title']} gestoert",
                w["reason"],
                severity="crit" if w["mode"] == "primary" else "warn",
            )
    lock = ((transport or {}).get("transport") or {}).get("lock_matches") if transport else None
    for c in find_conflicts(
        route_modes={k: str(v) for k, v in inference.route_modes.items()},
        models=models,
        lock_matches=lock,
        workloads_24h=lage.tag24,
        activity=lage.aktiv,
    ):
        hinweis(c.key, c.title, c.detail)
    return hinweise


__all__ = ["SCHEMA", "TASKS", "build_snapshot"]
