"""Was heute und diesen Monat schon ausgegeben wurde — aus dem EINEN Strom.

Der Leser, der bisher fehlte. ``app.ai.budget`` konnte einen Fensterzustand
BEWERTEN (:func:`~app.ai.budget.decide`), aber niemand hat je einen gebaut:
``accumulate()`` hatte null Produktionsaufrufer, ``BudgetPolicy`` wurde
nirgends mit einem Limit konstruiert. Ein Budget ohne Leser ist eine leere
Zusage.

**Kein neuer Strom.** Gelesen wird ausschliesslich
``artifacts/llm_telemetry.jsonl`` — derselbe Pfad, den ``record_llm_call``
schreibt und ``/health/ai`` bereits auswertet. Ein zweiter Strom bräuchte
Vertrag, Leser und Freshness-Zeile (Stream-Consumer-Ratchet) und wäre die
zweite Wahrheit über dieselbe Zahl.

**Die kanonische Zählebene — die Regel, ohne die alles falsch ist.**
Derselbe Aufruf erscheint bis zu zweimal im Strom:

* eine ÄUSSERE Zeile ``chain_position == -1`` (``app/analysis/pipeline.py``),
  die die ganze Fallback-Kette umspannt, und
* je eine Zeile ``chain_position >= 0`` pro Kettenversuch
  (``app/analysis/ensemble/provider.py``).

Beide zu zählen verdoppelt jede Ensemble-Analyse — messbar: dieselben
OpenAI-Aufrufe erschienen als 1.442.260 gegen 1.499.219 Eingabetoken
(KAI_COST_SURFACE.md:64-71). Deshalb gilt: **wo Versuchszeilen derselben
``correlation_id`` existieren, gewinnen sie; die äussere Zeile entfällt.**
Nicht umgekehrt — die Versuchszeilen sind die physischen, bezahlten Requests,
und v1-Zeilen ohne ``correlation_id`` haben ohnehin kein Gegenstück und
bleiben erhalten. Das ist exakt die Regel, die ``app/ai/health.py`` seit
NEO-P-005 anwendet; sie steht jetzt hier, damit Gesundheit und Budget nicht
zwei Meinungen über dieselbe Grundgesamtheit haben.

**Altzeilen sind nicht dasselbe wie unbelegte Aufrufe.** Der Strom traegt
Zeilen aus der Zeit VOR der Kostenmessung (D-CORE-007, 2026-09-08); sie haben
kein Feld ``cost_status``, weil es das damals nicht gab. Sie als ``unknown``
zu zaehlen hiesse, die Vergangenheit gegen ein Tageslimit der Gegenwart zu
rechnen: 373 Altzeilen eines Tages loesen ``COST_UNKNOWN`` aus, ohne dass ein
einziger neuer Aufruf unbelegt waere -- fail-closed aus einem Archiv heraus.
Deshalb zaehlt :attr:`SpendWindow.unmetered_legacy_calls` sie GETRENNT: sie
bleiben sichtbar, sie erhoehen keine Summe, und sie zaehlen nicht gegen
``APP_AI_BUDGET_UNKNOWN_MAX_CALLS_PER_DAY``. Unbelegt heisst ab jetzt: die
Messung LIEF und konnte trotzdem keinen Preis nennen.

Fail-soft: ein fehlender, leerer oder halb geschriebener Strom liefert einen
Nullzustand, keine Ausnahme. Eine Kostenbremse, die beim Lesen stirbt, wäre
ein Ausfall mit Kostenbegründung.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.ai.budget import (
    LEGACY_POT,
    POTS,
    BudgetEntry,
    BudgetPolicy,
    BudgetPot,
    BudgetState,
    BudgetStatus,
    accumulate,
    evaluate_status,
)
from app.observability.llm_telemetry import DEFAULT_TELEMETRY_PATH
from app.storage.jsonl_io import iter_jsonl_tolerant

#: Die bezahlten Anbieter. Der Strom trägt im Feld ``provider`` auch
#: Quellnamen (``CNBC``) aus der Zeit vor dem eigenen ``source``-Feld — ohne
#: diesen Filter zählt jede Aggregation Feeds als Anbieter mit.
PAID_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic", "gemini", "grok"})

Window = Literal["today", "month"]


def chain_position(row: dict[str, Any]) -> int:
    """Kettenebene einer Zeile; ``-1`` (äussere Zeile) im Zweifel."""
    try:
        return int(row.get("chain_position", -1))
    except (TypeError, ValueError):
        return -1


def row_ts(row: dict[str, Any]) -> datetime | None:
    """Zeitstempel als aware ``datetime`` oder ``None``. Naive Zeit zählt nicht."""
    try:
        ts = datetime.fromisoformat(str(row.get("ts", "")))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else None


def is_ai_row(row: dict[str, Any]) -> bool:
    """Beschreibt diese Zeile einen LLM-Aufruf an einen bezahlten Anbieter?"""
    provider = row.get("provider")
    return (
        isinstance(provider, str)
        and bool(provider)
        and (
            provider in PAID_PROVIDERS
            or (
                row.get("actual_provider") == provider
                and row.get("purpose") in {"analysis", "chat", "intent", "stt", "consensus"}
            )
        )
    )


def is_unmetered_legacy_row(row: dict[str, Any]) -> bool:
    """Stammt diese Zeile aus der Zeit VOR der Kostenmessung?

    Erkennungsmerkmal ist die ABWESENHEIT des Feldes ``cost_status``. Jede
    Zeile, die :func:`app.observability.llm_telemetry.record_llm_call` seit
    D-CORE-007 schreibt, traegt es -- auch dann, wenn kein Preis ermittelt
    werden konnte (``"COST_UNKNOWN"``). Fehlt es, hat die Messung zu diesem
    Aufruf nie stattgefunden; ihn als unbelegt zu zaehlen waere ein Vorwurf an
    ein Archiv. Ein Zeitstempel-Schnitt waere die schlechtere Regel: er
    braeuchte ein gepflegtes Datum und laege beim naechsten Neuaufsetzen falsch.
    """
    return "cost_status" not in row


def dedupe_chain_levels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Genau EINE Zeile je physischem Aufruf — siehe Modul-Docstring."""
    versuchs_ids = {
        row.get("correlation_id")
        for row in rows
        if chain_position(row) >= 0 and row.get("correlation_id")
    }
    return [
        row
        for row in rows
        if not (chain_position(row) == -1 and row.get("correlation_id") in versuchs_ids)
    ]


@dataclass(frozen=True)
class SpendBucket:
    """Ein Topf (Anbieter, Modell oder Auftraggeber) mit ehrlicher Lücke."""

    calls: int = 0
    known_cost_usd: float = 0.0
    unknown_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class SpendWindow:
    """Verbrauch eines Zeitfensters — Summe UND das, was in ihr fehlt.

    ``known_cost_usd`` ist eine UNTERGRENZE, sobald ``unknown_calls > 0``.
    Die beiden Zahlen gehören zusammen und werden nirgends getrennt
    ausgewiesen; genau diese Trennung machte den ersten Budget-Anlauf wertlos.
    """

    window: Window
    since: datetime
    until: datetime
    calls: int = 0
    known_cost_usd: float = 0.0
    unknown_calls: int = 0
    #: Aufrufe aus der Zeit VOR der Messung (kein ``cost_status`` in der Zeile).
    #: Sichtbar, aber ohne Wirkung auf Summe und Schwelle -- siehe Modul-Docstring.
    unmetered_legacy_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_provider: dict[str, SpendBucket] = field(default_factory=dict)
    by_model: dict[str, SpendBucket] = field(default_factory=dict)
    by_use_case: dict[str, SpendBucket] = field(default_factory=dict)
    #: Eine Position je Aufruf, ``cost_usd=None`` fuer die unbelegten. Roh
    #: aufbewahrt, damit :meth:`budget_state` das VORHANDENE
    #: ``app.ai.budget.accumulate`` benutzen kann statt danebenzurechnen.
    entries: tuple[BudgetEntry, ...] = ()
    #: Dieselben Positionen, getrennt nach Topf (Budget-Policy v2). Getrennt
    #: aufbewahrt und nicht aus ``entries`` gefiltert: eine Reserve, deren
    #: Stand aus derselben Liste zweimal verschieden hergeleitet werden kann,
    #: hat frueher oder spaeter zwei Staende.
    pot_entries: dict[str, tuple[BudgetEntry, ...]] = field(default_factory=dict)

    @property
    def known_calls(self) -> int:
        return self.calls - self.unknown_calls - self.unmetered_legacy_calls

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def fully_accounted(self) -> bool:
        """Trägt ``known_cost_usd`` die ganze Wahrheit dieses Fensters?

        Altzeilen zaehlen hier mit: sie sperren nichts, aber sie sind
        unbezifferte Aufrufe, und ein Fenster mit unbezifferten Aufrufen ist
        nicht vollstaendig belegt.
        """
        return self.calls > 0 and self.unknown_calls == 0 and self.unmetered_legacy_calls == 0

    @property
    def top_provider(self) -> str | None:
        """Teuerster Anbieter nach BELEGTEN Kosten; ``None`` ohne Beleg."""
        return _spitzenreiter(self.by_provider)

    @property
    def top_use_case(self) -> str | None:
        """Teuerster Auftraggeber nach BELEGTEN Kosten; ``None`` ohne Beleg."""
        return _spitzenreiter(self.by_use_case)

    def budget_state(self) -> BudgetState:
        """Derselbe Zustand, den ``app.ai.budget.decide()`` erwartet.

        Über das vorhandene :func:`~app.ai.budget.accumulate` gebaut, nicht
        danebengerechnet — sonst gäbe es zwei Vorstellungen davon, was ein
        unbelegter Aufruf mit einer Summe macht.
        """
        return accumulate(self.entries)

    def pot_states(self) -> dict[BudgetPot, BudgetState]:
        """Der Zustand JE Topf — die Grundlage von ``app.ai.budget.decide_pot``.

        Jeder bekannte Topf kommt vor, auch der leere: ein fehlender Schluessel
        zwaenge jeden Aufrufer zu einem eigenen Standardwert, und irgendeiner
        von ihnen naehme irgendwann einen anderen.
        """
        return {topf: accumulate(self.pot_entries.get(topf, ())) for topf in POTS}


def _spitzenreiter(buckets: dict[str, SpendBucket]) -> str | None:
    kandidaten = [(b.known_cost_usd, name) for name, b in buckets.items() if b.known_cost_usd > 0]
    if not kandidaten:
        return None
    return max(kandidaten)[1]


def window_bounds(window: Window, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Anfang und Ende eines Fensters — beides UTC, immer.

    Lokale Zeitzonen wären hier ein stiller Fehler: der Strom schreibt UTC,
    ein Tageslimit auf Ortszeit hätte je nach Standort des Betreibers ein
    anderes Fenster als die Zahlen, die es begrenzt.
    """
    jetzt = (now or datetime.now(UTC)).astimezone(UTC)
    if window == "today":
        return jetzt.replace(hour=0, minute=0, second=0, microsecond=0), jetzt
    return jetzt.replace(day=1, hour=0, minute=0, second=0, microsecond=0), jetzt


# --- mtime-Cache -----------------------------------------------------------
# Der Strom wird pro LLM-Aufruf einmal gelesen. Ihn bei jedem Aufruf komplett
# zu parsen waere blockierendes Datei-I/O im Event-Loop -- genau die Klasse
# Fehler, gegen die app/ai/runtime.py::environment_settings antritt. Der
# Schluessel ist (Pfad, mtime_ns, Groesse): eine angehaengte Zeile aendert
# beides, also kann der Cache keine frische Zeile verschlucken.
_CACHE: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}


def _stream_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def reset_spend_cache() -> None:
    """Zwischenspeicher leeren (Tests, Neustart nach Strom-Rotation)."""
    _CACHE.clear()


def load_rows(path: Path | None = None) -> list[dict[str, Any]]:
    """Alle bezahlten, kettenentdoppelten Zeilen des Stroms. Nie eine Ausnahme."""
    sink = path if path is not None else DEFAULT_TELEMETRY_PATH
    key = os.fspath(sink)
    signatur = _stream_signature(sink)
    if signatur is None:
        _CACHE.pop(key, None)
        return []
    zwischenspeicher = _CACHE.get(key)
    if zwischenspeicher is not None and zwischenspeicher[0] == signatur:
        return zwischenspeicher[1]
    zeilen: list[dict[str, Any]] = []
    try:
        for row in iter_jsonl_tolerant(sink):
            if isinstance(row, dict) and is_ai_row(row):
                zeilen.append(row)
    except Exception:  # noqa: BLE001 — ein kaputter Strom darf nicht sperren
        return []
    zeilen.sort(key=lambda r: str(r.get("ts", "")))
    entdoppelt = dedupe_chain_levels(zeilen)
    _CACHE[key] = (signatur, entdoppelt)
    return entdoppelt


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _usage(row: dict[str, Any]) -> tuple[int, int]:
    """Ein- und Ausgabetoken einer Zeile — unter beiden Feldnamen.

    Der Direktpfad schreibt die Usage seit jeher unter ``prompt_tokens`` und
    ``completion_tokens`` (``app/integrations/anthropic/provider.py`` bildet
    Anthropics ``input_tokens``/``output_tokens`` genau dorthin ab). Die
    Telemetriezeile bekam die kanonischen Namen erst am 2026-09-07 dazu, und
    diese Funktion las nur die neuen: fuer den 02.-09.06. zaehlte sie
    **3.651.280 Token als null**, der 07.09. war ein Mischtag mit 72,1 %
    Erfassung.

    Folgenlos war das nur, weil ``SpendWindow.input_tokens`` heute niemand
    liest — Geld kommt aus ``cost_usd``, das Budget aus ``entries``. Ein
    spaeterer Kostenbericht oder ein Token-Gate haette fuer die Altdaten
    glaubwuerdige Nullen bekommen.

    Vorrang haben die kanonischen Namen, JE FELD: eine Zeile mit
    ``input_tokens`` aber ohne ``output_tokens`` soll die Ausgabe aus
    ``completion_tokens`` ziehen duerfen, statt sie zu verlieren. Doppelt
    gezaehlt werden kann dabei nichts — jedes Feld nimmt genau eine Quelle.
    """
    ein = _as_int(row.get("input_tokens"))
    if ein is None:
        ein = _as_int(row.get("prompt_tokens"))
    aus = _as_int(row.get("output_tokens"))
    if aus is None:
        aus = _as_int(row.get("completion_tokens"))
    return ein or 0, aus or 0


def _add(
    buckets: dict[str, SpendBucket],
    name: str,
    row_cost: float | None,
    *,
    ein: int,
    aus: int,
) -> None:
    vorher = buckets.get(name, SpendBucket())
    buckets[name] = SpendBucket(
        calls=vorher.calls + 1,
        known_cost_usd=vorher.known_cost_usd + (row_cost or 0.0),
        unknown_calls=vorher.unknown_calls + (1 if row_cost is None else 0),
        input_tokens=vorher.input_tokens + ein,
        output_tokens=vorher.output_tokens + aus,
    )


def _topf_der_zeile(row: dict[str, Any]) -> BudgetPot:
    """Welchem Topf diese Zeile zugeschlagen wird.

    Zwei Faelle enden beide bei :data:`~app.ai.budget.LEGACY_POT`, und das ist
    Absicht:

    * die Zeile stammt aus der Zeit vor v8 und nennt keinen Topf,
    * die Zeile nennt einen Topf, den es nicht gibt (Tippfehler, fremder
      Schreiber, spaeter entfernter Topf).

    Der zweite Fall duerfte NICHT stillschweigend verschwinden: ein
    unbekannter Schluessel, den niemand einsammelt, waere Verbrauch, der in
    keiner Reserve auftaucht und in keiner Summe fehlt -- also unsichtbar. Er
    faellt hier auf den normalen Topf, wo er den Verbrauch erhoeht statt ihn zu
    verstecken.
    """
    wert = row.get("budget_pot")
    if isinstance(wert, str) and wert in POTS:
        return wert
    return LEGACY_POT


def spend_window(
    window: Window,
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> SpendWindow:
    """Verbrauch eines Fensters, aufgeschlüsselt nach Anbieter/Modell/Auftraggeber."""
    since, until = window_bounds(window, now)
    calls = 0
    bekannt = 0.0
    unbekannt = 0
    altzeilen = 0
    eingabe = 0
    ausgabe = 0
    nach_provider: dict[str, SpendBucket] = {}
    nach_modell: dict[str, SpendBucket] = {}
    nach_use_case: dict[str, SpendBucket] = {}
    positionen: list[BudgetEntry] = []
    nach_topf: dict[str, list[BudgetEntry]] = {}

    for row in load_rows(path):
        ts = row_ts(row)
        if ts is None or not since <= ts <= until:
            continue
        kosten = row.get("cost_usd")
        zeilen_kosten: float | None
        if isinstance(kosten, (int, float)) and not isinstance(kosten, bool):
            zeilen_kosten = float(kosten)
        else:
            zeilen_kosten = None
        zeilen_ein, zeilen_aus = _usage(row)

        calls += 1
        eingabe += zeilen_ein
        ausgabe += zeilen_aus
        # Eine Zeile ohne Preis ist entweder unbelegt (die Messung lief und
        # fand keinen) oder eine Altzeile (die Messung lief nie). Nur die
        # erste Sorte zaehlt gegen die Schwelle.
        alt = zeilen_kosten is None and is_unmetered_legacy_row(row)
        if alt:
            altzeilen += 1
        elif zeilen_kosten is None:
            unbekannt += 1
        else:
            bekannt += zeilen_kosten

        provider = str(row.get("provider") or "unknown")
        modell = str(row.get("actual_model") or row.get("model") or "unknown")
        use_case = str(row.get("use_case") or "unknown")
        _add(nach_provider, provider, zeilen_kosten, ein=zeilen_ein, aus=zeilen_aus)
        _add(nach_modell, modell, zeilen_kosten, ein=zeilen_ein, aus=zeilen_aus)
        _add(nach_use_case, use_case, zeilen_kosten, ein=zeilen_ein, aus=zeilen_aus)
        if not alt:
            # Altzeilen gehen NICHT in den Budgetzustand: ``accumulate`` kennt
            # nur "gebucht" und "unbekannt", und als unbekannt gezaehlt haetten
            # sie genau die Sperre ausgeloest, die dieser Nachtrag verhindert.
            position = BudgetEntry(route="standard", cost_usd=zeilen_kosten)
            positionen.append(position)
            nach_topf.setdefault(_topf_der_zeile(row), []).append(position)

    return SpendWindow(
        window=window,
        since=since,
        until=until,
        calls=calls,
        known_cost_usd=round(bekannt, 8),
        unknown_calls=unbekannt,
        unmetered_legacy_calls=altzeilen,
        input_tokens=eingabe,
        output_tokens=ausgabe,
        by_provider=nach_provider,
        by_model=nach_modell,
        by_use_case=nach_use_case,
        entries=tuple(positionen),
        pot_entries={topf: tuple(eintraege) for topf, eintraege in nach_topf.items()},
    )


def current_spend(
    *, path: Path | None = None, now: datetime | None = None
) -> tuple[SpendWindow, SpendWindow]:
    """(heute UTC, laufender Monat UTC) — ein Lesevorgang, zwei Fenster."""
    return spend_window("today", path=path, now=now), spend_window("month", path=path, now=now)


def current_budget_status(
    *, path: Path | None = None, now: datetime | None = None
) -> tuple[BudgetStatus, SpendWindow, SpendWindow]:
    """Zustand des Budgets aus dem Strom und der Konfiguration.

    Die einzige Stelle, an der Lesen (Strom), Politik (Env) und Bewertung
    (``app.ai.budget``) zusammenkommen. Fail-soft in beide Richtungen: ein
    unlesbarer Strom ergibt einen Nullzustand, eine unlesbare Konfiguration
    ergibt keine Limits — beides fuehrt zu ``OK`` und damit zum heutigen
    Verhalten, nie zu einer Sperre aus einem Defekt heraus.
    """
    from app.core.ai_cost_settings import get_ai_cost_settings

    heute, monat = current_spend(path=path, now=now)
    grenzen = get_ai_cost_settings()
    policy = BudgetPolicy(
        daily_limit_usd=grenzen.budget_daily_usd,
        monthly_limit_usd=grenzen.budget_monthly_usd,
    )
    status = evaluate_status(
        daily=heute.budget_state(),
        monthly=monat.budget_state(),
        policy=policy,
        warn_pct=grenzen.budget_warn_pct,
        unknown_max_calls_per_day=grenzen.budget_unknown_max_calls_per_day,
    )
    return status, heute, monat


__all__ = [
    "PAID_PROVIDERS",
    "SpendBucket",
    "SpendWindow",
    "Window",
    "chain_position",
    "current_budget_status",
    "current_spend",
    "dedupe_chain_levels",
    "is_ai_row",
    "is_unmetered_legacy_row",
    "load_rows",
    "reset_spend_cache",
    "row_ts",
    "spend_window",
    "window_bounds",
]
