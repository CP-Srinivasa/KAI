"""Wer macht was -- je Aufgabe, Dienst, Weg und Modell (Spec §4.4, §5.2).

Eingabe sind die ENTDOPPELTEN KI-Zeilen aus ``app.ai.spend.load_rows``: dieselbe
Kettenebenen-Regel wie das Budget, damit nichts doppelt zaehlt.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from app.ai.spend import row_ts, row_usage

UNBEKANNT: Final = "unbekannt"
_ZWECK_ROUTE: Final = {
    "analysis": "standard",
    "chat": "standard",
    "intent": "critical",
    "stt": "stt",
    "consensus": "reasoning",
    "research": "research",
}


@dataclass(frozen=True)
class WorkloadKey:
    purpose: str
    route: str
    service: str
    transport: str
    model: str


@dataclass
class WorkloadStats:
    calls: int = 0
    ok: int = 0
    failures: int = 0
    fallbacks: int = 0
    known_cost_usd: float = 0.0
    unknown_cost_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_call: datetime | None = None
    last_ok: datetime | None = None
    sources: dict[str, int] = field(default_factory=dict)

    @property
    def approx_kb(self) -> float:
        """Naeherung ~4 Zeichen je Token -- als Naeherung beschriftet anzeigen."""
        return (self.input_tokens + self.output_tokens) * 4 / 1024


@dataclass
class ProviderActivity:
    last_ok: datetime | None = None
    calls_1h: int = 0
    failures_1h: int = 0
    consecutive_failures: int = 0
    calls_24h: int = 0
    failures_24h: int = 0
    quota_errors_24h: int = 0
    schema_errors_24h: int = 0
    cost_7d_usd: float = 0.0


def _text(row: dict[str, Any], *names: str) -> str:
    for name in names:
        wert = row.get(name)
        if isinstance(wert, str) and wert.strip():
            return wert.strip()
    return UNBEKANNT


def _kosten(row: dict[str, Any]) -> float | None:
    wert = row.get("cost_usd")
    if isinstance(wert, (int, float)) and not isinstance(wert, bool):
        return float(wert)
    return None


def key_of(row: dict[str, Any]) -> WorkloadKey:
    zweck = _text(row, "purpose")
    route = _text(row, "logical_route")
    if route == UNBEKANNT:
        route = _ZWECK_ROUTE.get(zweck, UNBEKANNT)
    transport = _text(row, "transport")
    return WorkloadKey(
        purpose=zweck,
        route=route,
        service=_text(row, "service"),
        transport="direct" if transport == UNBEKANNT else transport,
        model=_text(row, "actual_model", "model"),
    )


def aggregate(
    rows: Iterable[dict[str, Any]], *, since: datetime, until: datetime
) -> dict[WorkloadKey, WorkloadStats]:
    ergebnis: dict[WorkloadKey, WorkloadStats] = {}
    for row in rows:
        ts = row_ts(row)
        if ts is None or not since <= ts <= until:
            continue
        st = ergebnis.setdefault(key_of(row), WorkloadStats())
        ein, aus = row_usage(row)
        st.calls += 1
        st.input_tokens += ein
        st.output_tokens += aus
        ok = row.get("ok") is True
        if ok:
            st.ok += 1
            if st.last_ok is None or ts > st.last_ok:
                st.last_ok = ts
        else:
            st.failures += 1
        if row.get("fallback_to") == "direct":
            st.fallbacks += 1
        kosten = _kosten(row)
        if kosten is not None:
            st.known_cost_usd += kosten
        elif ok:
            st.unknown_cost_calls += 1
        if st.last_call is None or ts > st.last_call:
            st.last_call = ts
        quelle = _text(row, "source")
        st.sources[quelle] = st.sources.get(quelle, 0) + 1
    return ergebnis


def provider_activity(
    rows: Iterable[dict[str, Any]], *, now: datetime
) -> dict[str, ProviderActivity]:
    datiert = sorted(
        ((ts, row) for row in rows if (ts := row_ts(row)) is not None), key=lambda p: p[0]
    )
    je: dict[str, ProviderActivity] = {}
    for ts, row in datiert:
        name = _text(row, "actual_provider", "provider")
        if name == UNBEKANNT:
            continue
        a = je.setdefault(name, ProviderActivity())
        ok = row.get("ok") is True
        a.consecutive_failures = 0 if ok else a.consecutive_failures + 1
        if ok:
            a.last_ok = ts
        if now - ts <= timedelta(hours=1):
            a.calls_1h += 1
            a.failures_1h += 0 if ok else 1
        if now - ts <= timedelta(hours=24):
            a.calls_24h += 1
            a.failures_24h += 0 if ok else 1
            a.quota_errors_24h += 1 if row.get("error_class") == "quota" else 0
            a.schema_errors_24h += 1 if row.get("error_class") == "schema" else 0
        kosten = _kosten(row)
        if kosten is not None and now - ts <= timedelta(days=7):
            a.cost_7d_usd += kosten
    return je


__all__ = [
    "UNBEKANNT",
    "ProviderActivity",
    "WorkloadKey",
    "WorkloadStats",
    "aggregate",
    "key_of",
    "provider_activity",
]
