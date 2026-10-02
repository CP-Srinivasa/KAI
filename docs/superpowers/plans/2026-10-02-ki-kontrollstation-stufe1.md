# KI-Kontrollstation Stufe 1 (sehen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine nur lesende Dashboard-Seite „KI-Kontrolle“ beantwortet „Läuft alles? Wer macht was, und was verbraucht es? Wo muss ich eingreifen?“. Dazu kommen Guthaben-Abfrage, Telegram-Schwellen und ein KI-Block im Operator-Digest.

**Architecture:**
- **Backend:** neues Paket `app/ai/control/`. Reine Funktionen bilden aus Telemetrie, Konfiguration, Routenbericht, Circuit-Exporten und Kontodatei den Vertrag `ai-control/v1`.
- **Endpunkte:** ein neuer Router liefert ihn aus und liest dabei nur Dateien.
- **Timer:** ein Oneshot-Timer (alle 10 min) holt stündlich die Guthaben, schreibt das Protokoll der Schalteränderungen und schickt entprellte Telegram-Hinweise.
- **Frontend:** eine neue Seite im vorhandenen Neon-System.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, httpx (sync für den Timer), pytest; React 18 + TypeScript + Tailwind, vitest; systemd-Units.

**Spec:** `docs/superpowers/specs/2026-10-02-ki-kontrollstation-design.md` (PR #1163, freigegeben 02.10.2026)

## Global Constraints

- Stufe 1 ist **nur lesend**: kein Schalter, keine Konfigurationsänderung, kein Modellaufruf. Nach außen gehen nur die Guthaben-Abfragen im Timer.
- Zustände genau: `aktiv`, `bereit`, `pausiert`, `gestoert`, `ausser_kraft` (in Stufe 1 nie gesetzt), `deaktiviert`. Prüfreihenfolge wie Spec §3.
- Jedes `null` im Vertrag hat einen Eintrag in `null_reasons` (No-Fake, wie `ai-transport/v1`).
- Schlüssel erscheinen nie in Dateien, Logs, Antworten oder Telegram-Texten.
- `app/core/settings.py`, `app/alerts/health_check.py` und `app/api/routers/dashboard.py` bekommen **keine** Zeile dazu (God-File-Ratchet ohne Spielraum). Neue Settings leben in `app/ai/control/config.py`.
- Keine neue `*.jsonl`-Datei (Stream-Consumer-Ratchet). Das Protokoll ist eine begrenzte JSON-Datei (letzte 200 Einträge). Das weicht bewusst von Spec §5.5 ab, die `.jsonl` nennt.
- Schwellen-Standards (Spec §6.1): Guthaben < 5 $ oder Reichweite < 7 Tage · Tagesbudget vor 12:00 UTC leer · Fehlerquote > 20 % in 1 h bei ≥ 10 Aufrufen · ≥ 5 Fehlversuche in Folge · Proxy > 5 min nicht erreichbar · Ruhezeit 23–07 Uhr Europe/Berlin (Ausnahme „gestört“ auf `primary`) · Erinnerung nach 24 h.
- Telemetrie-Schema steigt auf `v10` mit dem Feld `service` (aus `/proc/self/cgroup`).
- Zeiten im Vertrag immer ISO-8601 UTC.
- Neue Env-Schalter stehen in `.env.example`; Geheimnisse dort leer (`OPENAI_ADMIN_KEY=`).

## Review Focus

1. **Leere oder fehlende Telemetrie** (frische Pi, Datei fehlt): Die Seite muss „keine Daten (Grund)“ zeigen statt 0 oder 500. Test in Task 7 (`test_snapshot_ohne_telemetrie_hat_gruende`).
2. **Kontodatei veraltet, weil der Timer tot ist:** Ohne Warnung würden alte Guthaben als aktuell gelten. Erwartet: Hinweis „Kontoabfrage veraltet“ ab 3 h. Test in Task 7 (`test_veraltete_konten_erzeugen_hinweis`).
3. **Circuit-Datei eines Dienstes bleibt auf `open` stehen**, weil danach keine Aufrufe mehr kamen: Das dürfte nicht ewig als „gestört“ gelten. Erwartet: nach Ablauf der Abkühlzeit `half_open` → `pausiert`, nach 24 h „unbekannt“. Test in Task 2 (`test_offener_circuit_altert_zu_half_open`).
4. **Ruhezeit:** Ein Hinweis entsteht um 02:00 und erledigt sich um 04:00. Um 07:00 muss genau eine Sammelnachricht mit „neu“ und „behoben“ kommen, nachts nichts. Test in Task 9 (`test_ruhezeit_sammelt_und_liefert_morgens`).
5. **Telemetriezeilen mit fremden oder fehlenden Feldern** (v1 ohne `purpose`, `transport` fehlt, unbekannter `purpose`): Sie dürfen die Aggregation nicht sprengen und fallen in `unbekannt`. Test in Task 4 (`test_alte_und_fremde_zeilen_landen_in_unbekannt`).

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `app/observability/service_name.py` (neu) | Dienstname aus `/proc/self/cgroup` |
| `app/observability/llm_telemetry.py` (ändern) | Schema v10, Feld `service` |
| `scripts/litellm_route_report/inputs.py` (ändern) | `v10` als bekanntes Schema |
| `app/ai/circuit_export.py` (neu) | Circuit-Zustand je Dienst schreiben/lesen |
| `app/ai/runtime.py` (ändern) | Export nach jedem Aufruf (nur bei Änderung) |
| `app/ai/spend.py` (ändern) | öffentliche `row_usage()` |
| `app/ai/control/__init__.py` (neu) | Paket |
| `app/ai/control/config.py` (neu) | `AccountKeys`, `LiteLLMModels`, `ControlThresholds`, `ControlPaths` |
| `app/ai/control/states.py` (neu) | Zustandsmodell (rein) |
| `app/ai/control/workloads.py` (neu) | Wer-macht-was je Aufgabe/Dienst/Weg/Modell + Quellen |
| `app/ai/control/history.py` (neu) | 14-Tage-Verlauf + Budgetende je Tag |
| `app/ai/control/accounts.py` (neu) | Guthaben-Abfrage + Parser + Kontodatei |
| `app/ai/control/conflicts.py` (neu) | Konfliktregeln (rein) |
| `app/ai/control/protocol.py` (neu) | Fingerabdruck der KI-Schalter + Protokoll |
| `app/ai/control/snapshot.py` (neu) | Vertrag `ai-control/v1` inkl. Handlungsbedarf |
| `app/ai/control/alerts.py` (neu) | Telegram-Entprellung, Ruhezeit, Texte |
| `app/ai/control/digest.py` (neu) | KI-Zeilen für den Operator-Digest |
| `app/ai/control/tick.py` (neu) | Timer-Einstieg |
| `app/api/routers/ai_control.py` (neu) | `GET /dashboard/api/ai/control`, `…/history` |
| `app/api/main.py` (ändern) | Router einbinden |
| `scripts/digest_ops_block.py` (ändern) | KI-Zeilen nach der Kostenzeile |
| `deploy/systemd/kai-ai-control.service` + `.timer` (neu) | Timer |
| `deploy/systemd/enabled_set.txt` (ändern) | Timer ins Soll-Set |
| `.env.example` (ändern) | neue Schalter |
| `web/src/lib/aiControl.ts` (+ `.test.ts`) (neu) | Typen, Fetcher, Anzeige-Helfer |
| `web/src/pages/AIControl.tsx` (neu) | Seite |
| `web/src/components/aicontrol/*.tsx` (neu) | Bereiche der Seite |
| `web/src/state/Router.tsx`, `web/src/layout/AppShell.tsx`, `web/src/layout/Sidebar.tsx`, `web/src/i18n/strings.ts` (ändern) | Menüpunkt |
| `KAI-mirror/reminders/ki_kontrolle_timer_aktivieren.ps1` (neu, außerhalb Repo) | Operator-Skript für die Units |

---

### Task 1: Telemetrie v10 mit `service` + öffentliche `row_usage`

**Files:**
- Create: `app/observability/service_name.py`
- Modify: `app/observability/llm_telemetry.py` (SCHEMA_VERSION + Zeilenaufbau nach `row["runtime_commit"], row["runtime_source"] = _runtime_provenance()`)
- Modify: `scripts/litellm_route_report/inputs.py:34`
- Modify: `app/ai/spend.py` (nach `_usage`)
- Test: `tests/unit/test_service_name.py` (neu), `tests/unit/test_telemetry_schema_version.py` (Zeilen 56 und 122: `"v9"` → `"v10"`, plus Feldprüfung)

**Interfaces:**
- Produces: `service_name() -> str` (z. B. `"kai-server"`, sonst `"unbekannt"`); Telemetriefeld `service`; `spend.row_usage(row: dict) -> tuple[int, int]`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_service_name.py
from pathlib import Path

from app.observability.service_name import UNKNOWN, parse_cgroup, service_name


def test_cgroup_v2_nennt_die_unit() -> None:
    assert parse_cgroup("0::/system.slice/kai-server.service\n") == "kai-server"


def test_template_unit_und_v1_zeilen() -> None:
    text = "12:pids:/system.slice/kai-unit-failure-notify@x.service\n1:name=systemd:/\n"
    assert parse_cgroup(text) == "kai-unit-failure-notify@x"


def test_ohne_service_unbekannt() -> None:
    assert parse_cgroup("0::/user.slice/user-1000.slice/session-3.scope\n") == UNKNOWN


def test_ohne_datei_unbekannt(tmp_path: Path) -> None:
    service_name.cache_clear()
    assert service_name(tmp_path / "fehlt") == UNKNOWN
    service_name.cache_clear()
```

Ergänze in `tests/unit/test_telemetry_schema_version.py::test_die_zeile_nennt_die_version_die_sie_traegt`: `"v9"` → `"v10"` (beide Literale, Zeilen 56 und 122) und füge an:

```python
    assert "service" in zeile, "und das v10-Feld"
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_service_name.py tests/unit/test_telemetry_schema_version.py -q` → ImportError bzw. `'v9' == 'v10'`.

- [ ] **Step 3: Implement**

```python
# app/observability/service_name.py
"""Welcher systemd-Dienst schreibt -- einmal je Prozess aus ``/proc/self/cgroup``.

Telemetrie v10 (KI-Kontrollstation, 02.10.2026): ohne den Dienstnamen sieht das
Dashboard, DASS analysiert wurde, aber nicht von wem. Keine Unit-Aenderung noetig.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final

UNKNOWN: Final = "unbekannt"
_UNIT: Final = re.compile(r"/([A-Za-z0-9@_.:-]+)\.service(?:/|$)")


def parse_cgroup(text: str) -> str:
    for line in text.splitlines():
        treffer = _UNIT.search(line.strip())
        if treffer:
            return treffer.group(1)
    return UNKNOWN


@lru_cache(maxsize=1)
def service_name(path: Path = Path("/proc/self/cgroup")) -> str:
    try:
        return parse_cgroup(path.read_text(encoding="utf-8"))
    except OSError:
        return UNKNOWN
```

In `app/observability/llm_telemetry.py`: Kommentarblock über `SCHEMA_VERSION` ergänzen und Konstante setzen:

```python
#: v10 (2026-10-02): `service` -- welcher Dienst die Zeile schrieb (KI-Kontrollstation).
SCHEMA_VERSION = "v10"
```

direkt nach `row["runtime_commit"], row["runtime_source"] = _runtime_provenance()`:

```python
    row["service"] = _service_label()
```

und als Helfer neben `_runtime_provenance`:

```python
def _service_label() -> str:
    """Der schreibende Dienst (v10) -- nie eine Ausnahme in die Telemetrie."""
    try:
        from app.observability.service_name import service_name

        return service_name()
    except Exception:  # noqa: BLE001 - Telemetrie reisst den Aufruf nie mit
        return "unbekannt"
```

In `scripts/litellm_route_report/inputs.py:34`: `"v9"}` → `"v9", "v10"}`.

In `app/ai/spend.py` direkt nach `_usage`:

```python
def row_usage(row: dict[str, Any]) -> tuple[int, int]:
    """Oeffentlicher Zugang zu den Token einer Zeile -- dieselbe Regel wie das Budget."""
    return _usage(row)
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_service_name.py tests/unit/test_telemetry_schema_version.py tests/unit/litellm_route_report/ -q`

- [ ] **Step 5: Commit** — `git add app/observability/service_name.py app/observability/llm_telemetry.py scripts/litellm_route_report/inputs.py app/ai/spend.py tests/unit/test_service_name.py tests/unit/test_telemetry_schema_version.py && git commit -m "feat(telemetry): v10 mit Dienstname (service) fuer die KI-Kontrollstation"`

---

### Task 2: Circuit-Zustand je Dienst exportieren

**Files:**
- Create: `app/ai/circuit_export.py`
- Modify: `app/ai/runtime.py` — in `invoke`, direkt **nach** dem Block `async with AsyncExitStack() as stack:` (vor `selected = outcome.authoritative_attempt`)
- Test: `tests/unit/test_circuit_export.py`

**Interfaces:**
- Consumes: `app.ai.runtime.circuit_state()` (Liste von Dicts mit `route`, `alias`, `upstream`, `state`, `consecutive_failures`, `probe_in_flight`), `app.ai.circuit.DEFAULT_COOLDOWN_S`
- Produces: `export_circuit(states, *, service, now, directory=EXPORT_DIR) -> bool`; `read_circuits(*, now, directory=EXPORT_DIR, max_age_h=24.0) -> list[ServiceCircuit]`; `ServiceCircuit(service: str, written_at: datetime, stale: bool, keys: list[dict])` — in `keys[i]["state"]` ist ein offener Kreis nach Ablauf der Abkühlung schon zu `half_open` gealtert.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_circuit_export.py
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ai import circuit_export as ce

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
OFFEN = [{"route": "standard", "alias": "kai-standard", "upstream": None,
          "state": "open", "consecutive_failures": 5, "probe_in_flight": False}]


def test_schreibt_nur_bei_aenderung(tmp_path: Path) -> None:
    ce._LETZTER.clear()
    assert ce.export_circuit(OFFEN, service="kai-server", now=T0, directory=tmp_path) is True
    assert ce.export_circuit(OFFEN, service="kai-server", now=T0, directory=tmp_path) is False
    daten = json.loads((tmp_path / "circuit_kai-server.json").read_text())
    assert daten["schema"] == "ai-circuit/v1" and daten["keys"][0]["state"] == "open"


def test_offener_circuit_altert_zu_half_open(tmp_path: Path) -> None:
    ce._LETZTER.clear()
    ce.export_circuit(OFFEN, service="kai-server", now=T0, directory=tmp_path)
    frisch = ce.read_circuits(now=T0 + timedelta(seconds=10), directory=tmp_path)
    assert frisch[0].keys[0]["state"] == "open" and not frisch[0].stale
    spaeter = ce.read_circuits(now=T0 + timedelta(seconds=ce.DEFAULT_COOLDOWN_S + 1), directory=tmp_path)
    assert spaeter[0].keys[0]["state"] == "half_open"
    alt = ce.read_circuits(now=T0 + timedelta(hours=25), directory=tmp_path)
    assert alt[0].stale is True


def test_kaputte_datei_wird_uebersprungen(tmp_path: Path) -> None:
    (tmp_path / "circuit_kaputt.json").write_text("{nicht json")
    assert ce.read_circuits(now=T0, directory=tmp_path) == []
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_circuit_export.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
# app/ai/circuit_export.py
"""Circuit-Zustand je Dienst als Datei (KI-Kontrollstation, 02.10.2026).

Der Circuit lebt prozesslokal (``app.ai.runtime._KREISE``): das Dashboard sah bisher
nur den des Servers. Jeder Dienst schreibt seinen Stand bei JEDER Aenderung atomar
nach ``artifacts/runtime/circuit_<dienst>.json``; ohne Aenderung kein Schreiben.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from app.ai.circuit import DEFAULT_COOLDOWN_S

EXPORT_DIR: Final = Path("artifacts/runtime")
SCHEMA: Final = "ai-circuit/v1"
_LETZTER: dict[str, str] = {}


@dataclass(frozen=True)
class ServiceCircuit:
    service: str
    written_at: datetime
    stale: bool
    keys: list[dict[str, Any]]


def export_circuit(
    states: list[dict[str, Any]], *, service: str, now: datetime, directory: Path = EXPORT_DIR
) -> bool:
    fingerabdruck = json.dumps(states, sort_keys=True, default=str)
    if _LETZTER.get(service) == fingerabdruck:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    daten = {"schema": SCHEMA, "service": service, "written_at": now.astimezone(UTC).isoformat(),
             "cooldown_s": DEFAULT_COOLDOWN_S, "keys": states}
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".circuit-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, sort_keys=True, default=str)
    os.replace(tmp, directory / f"circuit_{service}.json")
    _LETZTER[service] = fingerabdruck
    return True


def read_circuits(
    *, now: datetime, directory: Path = EXPORT_DIR, max_age_h: float = 24.0
) -> list[ServiceCircuit]:
    ergebnis: list[ServiceCircuit] = []
    for datei in sorted(directory.glob("circuit_*.json")):
        try:
            daten = json.loads(datei.read_text(encoding="utf-8"))
            geschrieben = datetime.fromisoformat(daten["written_at"])
            keys = list(daten.get("keys") or [])
            abkuehlung = float(daten.get("cooldown_s") or DEFAULT_COOLDOWN_S)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        alter = now - geschrieben
        gealtert = [
            {**k, "state": "half_open"}
            if k.get("state") == "open" and alter > timedelta(seconds=abkuehlung)
            else k
            for k in keys
        ]
        ergebnis.append(ServiceCircuit(
            service=str(daten.get("service") or datei.stem.removeprefix("circuit_")),
            written_at=geschrieben, stale=alter > timedelta(hours=max_age_h), keys=gealtert,
        ))
    return ergebnis


__all__ = ["EXPORT_DIR", "SCHEMA", "ServiceCircuit", "export_circuit", "read_circuits"]
```

In `app/ai/runtime.py`, nach dem `async with AsyncExitStack() as stack:`-Block (also auf der Einrückung von `selected = outcome.authoritative_attempt`), **vor** `selected = …`:

```python
    _circuit_exportieren()
```

und als Modulfunktion unter `reset_circuit_state`:

```python
def _circuit_exportieren() -> None:
    """Circuit-Stand fuer die Kontrollstation -- nur bei Aenderung, nie eine Ausnahme."""
    try:
        from app.ai.circuit_export import export_circuit
        from app.observability.service_name import service_name

        export_circuit(circuit_state(), service=service_name(), now=datetime.now(UTC))
    except Exception:  # noqa: BLE001 -- die Anzeige darf keinen Aufruf kosten
        return
```

(`datetime`/`UTC` importieren, falls noch nicht vorhanden: `from datetime import UTC, datetime`.)

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_circuit_export.py tests/unit/test_caller_wiring_s5.py tests/unit/test_s5_control_plane_invariants.py -q`

- [ ] **Step 5: Commit** — `git commit -m "feat(ai): Circuit-Zustand je Dienst fuer die Kontrollstation exportieren"` (Dateien aus Files)

---

### Task 3: Zustandsmodell

**Files:**
- Create: `app/ai/control/__init__.py` (leer bis auf Docstring), `app/ai/control/states.py`
- Test: `tests/unit/ai_control/__init__.py` (leer), `tests/unit/ai_control/test_states.py`

**Interfaces:**
- Produces: `State` (StrEnum), `Signals` (dataclass), `Verdict(state, reason, since)`, `classify(s: Signals) -> Verdict`, Konstanten `ACTIVE_WINDOW`, `FAILURE_RATE`, `MIN_CALLS_FOR_RATE`, `MAX_CONSECUTIVE`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/ai_control/test_states.py
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.control.states import Signals, State, classify

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def s(**kw):  # noqa: ANN001, ANN202
    return Signals(now=NOW, configured=True, **kw)


def test_reihenfolge_konfiguration_vor_allem() -> None:
    v = classify(Signals(now=NOW, configured=False, disabled_reason="Route aus", proxy_down=True))
    assert v.state is State.DEAKTIVIERT and v.reason == "Route aus"


@pytest.mark.parametrize(("kw", "erwartet"), [
    ({"proxy_down": True}, "Proxy nicht erreichbar"),
    ({"circuit_open": True}, "Circuit offen nach Fehlern"),
    ({"consecutive_failures": 5}, "5 Fehlversuche in Folge"),
    ({"calls_1h": 10, "failures_1h": 3}, "Fehlerquote 30 % in 1 h"),
    ({"balance_exhausted": True}, "Guthaben leer"),
])
def test_gestoert(kw: dict, erwartet: str) -> None:
    v = classify(s(**kw))
    assert v.state is State.GESTOERT and erwartet in v.reason


def test_wenige_aufrufe_sind_keine_quote() -> None:
    assert classify(s(calls_1h=9, failures_1h=9, last_ok=NOW)).state is State.AKTIV


def test_pausiert_mit_wiederanlauf() -> None:
    bis = NOW + timedelta(hours=12)
    v = classify(s(paused_reason="Tagesbudget leer", paused_until=bis))
    assert v.state is State.PAUSIERT and "00:00 UTC" in v.reason and v.since == bis


def test_aktiv_und_bereit() -> None:
    assert classify(s(last_ok=NOW - timedelta(minutes=14))).state is State.AKTIV
    v = classify(s(last_ok=NOW - timedelta(minutes=16)))
    assert v.state is State.BEREIT and "letzter Aufruf" in v.reason
    assert classify(s()).reason == "noch kein Aufruf"


def test_ausser_kraft_nur_mit_eingriff() -> None:
    assert classify(s(override_reason="standard -> off bis 03.10.")).state is State.AUSSER_KRAFT
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_states.py -q`

- [ ] **Step 3: Implement**

```python
# app/ai/control/__init__.py
"""KI-Kontrollstation (Spec docs/superpowers/specs/2026-10-02-ki-kontrollstation-design.md)."""
```

```python
# app/ai/control/states.py
"""Ein Zustand je Objekt -- Regeln in fester Reihenfolge (Spec §3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

ACTIVE_WINDOW: Final = timedelta(minutes=15)
FAILURE_RATE: Final = 0.20
MIN_CALLS_FOR_RATE: Final = 10
MAX_CONSECUTIVE: Final = 5


class State(StrEnum):
    AKTIV = "aktiv"
    BEREIT = "bereit"
    PAUSIERT = "pausiert"
    GESTOERT = "gestoert"
    AUSSER_KRAFT = "ausser_kraft"
    DEAKTIVIERT = "deaktiviert"


@dataclass(frozen=True)
class Signals:
    now: datetime
    configured: bool
    disabled_reason: str = ""
    override_reason: str = ""
    proxy_down: bool = False
    circuit_open: bool = False
    consecutive_failures: int = 0
    calls_1h: int = 0
    failures_1h: int = 0
    balance_exhausted: bool = False
    paused_reason: str = ""
    paused_until: datetime | None = None
    last_ok: datetime | None = None


@dataclass(frozen=True)
class Verdict:
    state: State
    reason: str
    since: datetime | None


def classify(s: Signals) -> Verdict:
    if not s.configured:
        return Verdict(State.DEAKTIVIERT, s.disabled_reason or "laut Konfiguration aus", None)
    if s.override_reason:
        return Verdict(State.AUSSER_KRAFT, s.override_reason, None)
    gruende: list[str] = []
    if s.proxy_down:
        gruende.append("Proxy nicht erreichbar")
    if s.circuit_open:
        gruende.append("Circuit offen nach Fehlern")
    if s.consecutive_failures >= MAX_CONSECUTIVE:
        gruende.append(f"{s.consecutive_failures} Fehlversuche in Folge")
    if s.calls_1h >= MIN_CALLS_FOR_RATE and s.failures_1h / s.calls_1h > FAILURE_RATE:
        gruende.append(f"Fehlerquote {round(100 * s.failures_1h / s.calls_1h)} % in 1 h")
    if s.balance_exhausted:
        gruende.append("Guthaben leer")
    if gruende:
        return Verdict(State.GESTOERT, " · ".join(gruende), None)
    if s.paused_reason:
        bis = f" bis {s.paused_until:%H:%M} UTC" if s.paused_until else ""
        return Verdict(State.PAUSIERT, s.paused_reason + bis, s.paused_until)
    if s.last_ok is not None and s.now - s.last_ok <= ACTIVE_WINDOW:
        return Verdict(State.AKTIV, f"letzter Aufruf {s.last_ok:%H:%M} UTC", s.last_ok)
    if s.last_ok is None:
        return Verdict(State.BEREIT, "noch kein Aufruf", None)
    return Verdict(State.BEREIT, f"letzter Aufruf {s.last_ok:%d.%m. %H:%M} UTC", s.last_ok)


__all__ = ["ACTIVE_WINDOW", "FAILURE_RATE", "MAX_CONSECUTIVE", "MIN_CALLS_FOR_RATE",
           "Signals", "State", "Verdict", "classify"]
```

Note: Spec §3 nennt „Circuit in Abkühlung“ unter PAUSIERT und „offen nach Fehlern“ unter GESTÖRT. Umsetzung: `open` → `circuit_open=True` (gestört), `half_open` → `paused_reason="Circuit prüft Wiederanlauf"` (Task 7).

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/test_states.py -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(ai-control): Zustandsmodell (aktiv/bereit/pausiert/gestoert/ausser Kraft/deaktiviert)"`

---

### Task 4: Wer macht was + 14-Tage-Verlauf

**Files:**
- Create: `app/ai/control/workloads.py`, `app/ai/control/history.py`
- Test: `tests/unit/ai_control/test_workloads.py`, `tests/unit/ai_control/test_history.py`

**Interfaces:**
- Consumes: `spend.load_rows(path) -> list[dict]` (entdoppelt, nur KI-Zeilen), `spend.row_ts(row) -> datetime | None`, `spend.row_usage(row)`
- Produces:
  - `WorkloadKey(purpose, route, service, transport, model)` (frozen dataclass)
  - `WorkloadStats` mit `calls, ok, failures, fallbacks, known_cost_usd, unknown_cost_calls, input_tokens, output_tokens, last_call, last_ok, sources: dict[str, int]` und Property `approx_kb: float`
  - `aggregate(rows, *, since, until) -> dict[WorkloadKey, WorkloadStats]`
  - `provider_activity(rows, *, now) -> dict[str, ProviderActivity]` mit `ProviderActivity(last_ok, calls_1h, failures_1h, consecutive_failures, calls_24h, failures_24h, quota_errors_24h, schema_errors_24h, cost_7d_usd)`
  - `history.daily(rows, *, now, days=14) -> list[DayRow]` mit `DayRow(day: str, cost_by_provider: dict[str, float], calls: int, input_tokens: int, output_tokens: int, budget_exhausted_at: str | None)`
  - `history.budget_exhausted_at(rows, *, day) -> datetime | None`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/ai_control/test_workloads.py
from datetime import UTC, datetime, timedelta

from app.ai.control.workloads import WorkloadKey, aggregate, provider_activity

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def zeile(min_alt: float, **kw):  # noqa: ANN001, ANN202
    r = {"ts": (NOW - timedelta(minutes=min_alt)).isoformat(), "purpose": "analysis",
         "logical_route": "standard", "service": "kai-server", "transport": "direct",
         "provider": "openai", "actual_provider": "openai", "model": "gpt-4o",
         "actual_model": "gpt-4o", "ok": True, "cost_usd": 0.0085, "input_tokens": 2000,
         "output_tokens": 300, "source": "cryptobriefing", "chain_position": 0}
    r.update(kw)
    return r


def test_summe_je_schluessel() -> None:
    rows = [zeile(5), zeile(10, source="coindesk"), zeile(20, ok=False, cost_usd=None, input_tokens=0,
                                                          output_tokens=0, error_class="server")]
    agg = aggregate(rows, since=NOW - timedelta(hours=1), until=NOW)
    k = WorkloadKey("analysis", "standard", "kai-server", "direct", "gpt-4o")
    st = agg[k]
    assert (st.calls, st.ok, st.failures) == (3, 2, 1)
    assert round(st.known_cost_usd, 4) == 0.017 and st.input_tokens == 4000
    assert st.sources == {"cryptobriefing": 2, "coindesk": 1}
    assert st.last_ok == NOW - timedelta(minutes=5)
    assert round(st.approx_kb, 1) == round(4600 * 4 / 1024, 1)


def test_alte_und_fremde_zeilen_landen_in_unbekannt() -> None:
    alt = {"ts": (NOW - timedelta(minutes=1)).isoformat(), "provider": "openai", "ok": True}
    fremd = zeile(2, purpose="wahrsagen", logical_route=None, transport=None, service=None)
    agg = aggregate([alt, fremd], since=NOW - timedelta(hours=1), until=NOW)
    assert {k.purpose for k in agg} == {"unbekannt", "wahrsagen"}
    assert all(k.service == "unbekannt" and k.transport == "direct" for k in agg)


def test_fallback_wird_gezaehlt() -> None:
    agg = aggregate([zeile(1, transport="litellm", fallback_to="direct", ok=False)],
                    since=NOW - timedelta(hours=1), until=NOW)
    assert next(iter(agg.values())).fallbacks == 1


def test_anbieter_aktivitaet() -> None:
    rows = [zeile(m, ok=False, cost_usd=None, error_class="quota", actual_provider="deepseek",
                  provider="deepseek") for m in (1, 2, 3, 4, 5)]
    a = provider_activity(rows, now=NOW)["deepseek"]
    assert a.consecutive_failures == 5 and a.quota_errors_24h == 5 and a.calls_1h == 5
```

```python
# tests/unit/ai_control/test_history.py
from datetime import UTC, datetime

from app.ai.control.history import budget_exhausted_at, daily

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_tage_und_budgetende() -> None:
    rows = [
        {"ts": "2026-10-01T09:00:00+00:00", "provider": "openai", "cost_usd": 0.5, "ok": True,
         "input_tokens": 10, "output_tokens": 1},
        {"ts": "2026-10-01T13:55:00+00:00", "provider": "", "ok": False,
         "budget_decision": "reject:alert_reserve_exhausted"},
        {"ts": "2026-10-01T14:10:00+00:00", "provider": "", "ok": False,
         "budget_decision": "reject:alert_reserve_exhausted"},
        {"ts": "2026-10-02T08:00:00+00:00", "provider": "openai", "cost_usd": 0.25, "ok": True},
    ]
    tage = {t.day: t for t in daily(rows, now=NOW, days=2)}
    assert tage["2026-10-01"].cost_by_provider == {"openai": 0.5}
    assert tage["2026-10-01"].budget_exhausted_at == "2026-10-01T13:55:00+00:00"
    assert tage["2026-10-02"].budget_exhausted_at is None
    assert budget_exhausted_at(rows, day="2026-10-01").hour == 13
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_workloads.py tests/unit/ai_control/test_history.py -q`

- [ ] **Step 3: Implement**

```python
# app/ai/control/workloads.py
"""Wer macht was -- je Aufgabe, Dienst, Weg und Modell (Spec §4.4, §5.2).

Eingabe sind die ENTDOPPELTEN KI-Zeilen aus ``app.ai.spend.load_rows``: dieselbe
Kettenebenen-Regel wie das Budget, damit nichts doppelt zaehlt.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.ai.spend import row_ts, row_usage

UNBEKANNT = "unbekannt"
_ZWECK_ROUTE = {"analysis": "standard", "chat": "standard", "intent": "critical", "stt": "stt",
                "consensus": "reasoning", "research": "research"}


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


def key_of(row: dict[str, Any]) -> WorkloadKey:
    zweck = _text(row, "purpose")
    route = _text(row, "logical_route")
    if route == UNBEKANNT:
        route = _ZWECK_ROUTE.get(zweck, UNBEKANNT)
    transport = _text(row, "transport")
    return WorkloadKey(
        purpose=zweck, route=route, service=_text(row, "service"),
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
        if row.get("ok") is True:
            st.ok += 1
            st.last_ok = ts if st.last_ok is None or ts > st.last_ok else st.last_ok
        else:
            st.failures += 1
        if row.get("fallback_to") == "direct":
            st.fallbacks += 1
        kosten = row.get("cost_usd")
        if isinstance(kosten, (int, float)) and not isinstance(kosten, bool):
            st.known_cost_usd += float(kosten)
        elif row.get("ok") is True:
            st.unknown_cost_calls += 1
        st.last_call = ts if st.last_call is None or ts > st.last_call else st.last_call
        quelle = _text(row, "source")
        st.sources[quelle] = st.sources.get(quelle, 0) + 1
    return ergebnis


def provider_activity(rows: Iterable[dict[str, Any]], *, now: datetime) -> dict[str, ProviderActivity]:
    je: dict[str, ProviderActivity] = {}
    folge: Counter[str] = Counter()
    for row in sorted((r for r in rows if row_ts(r) is not None), key=lambda r: row_ts(r)):  # type: ignore[arg-type,return-value]
        ts = row_ts(row)
        assert ts is not None
        name = _text(row, "actual_provider", "provider")
        if name == UNBEKANNT:
            continue
        a = je.setdefault(name, ProviderActivity())
        ok = row.get("ok") is True
        folge[name] = 0 if ok else folge[name] + 1
        a.consecutive_failures = folge[name]
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
        kosten = row.get("cost_usd")
        if now - ts <= timedelta(days=7) and isinstance(kosten, (int, float)) and not isinstance(kosten, bool):
            a.cost_7d_usd += float(kosten)
    return je


__all__ = ["UNBEKANNT", "ProviderActivity", "WorkloadKey", "WorkloadStats", "aggregate",
           "key_of", "provider_activity"]
```

```python
# app/ai/control/history.py
"""14-Tage-Verlauf: Kosten je Anbieter, Aufrufe, Token, Budgetende je Tag (UTC)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.ai.spend import row_ts, row_usage

_ERSCHOEPFT = ("normal_budget_exhausted", "alert_reserve_exhausted", "daily_limit_reached")


@dataclass
class DayRow:
    day: str
    cost_by_provider: dict[str, float] = field(default_factory=dict)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    budget_exhausted_at: str | None = None


def _ist_sperre(row: dict[str, Any]) -> bool:
    wert = row.get("budget_decision")
    return isinstance(wert, str) and wert.startswith("reject:") and wert.split(":", 1)[1] in _ERSCHOEPFT


def budget_exhausted_at(rows: Iterable[dict[str, Any]], *, day: str) -> datetime | None:
    zeiten = [ts for r in rows if _ist_sperre(r) and (ts := row_ts(r)) is not None and ts.date().isoformat() == day]
    return min(zeiten) if zeiten else None


def daily(rows: Iterable[dict[str, Any]], *, now: datetime, days: int = 14) -> list[DayRow]:
    liste = list(rows)
    tage = {(now.date() - timedelta(days=i)).isoformat(): DayRow(day=(now.date() - timedelta(days=i)).isoformat())
            for i in range(days)}
    for row in liste:
        ts = row_ts(row)
        if ts is None or (tag := tage.get(ts.date().isoformat())) is None:
            continue
        tag.calls += 1
        ein, aus = row_usage(row)
        tag.input_tokens += ein
        tag.output_tokens += aus
        kosten = row.get("cost_usd")
        if isinstance(kosten, (int, float)) and not isinstance(kosten, bool):
            name = str(row.get("actual_provider") or row.get("provider") or "unbekannt")
            tag.cost_by_provider[name] = round(tag.cost_by_provider.get(name, 0.0) + float(kosten), 6)
    for tag in tage.values():
        ende = budget_exhausted_at(liste, day=tag.day)
        tag.budget_exhausted_at = ende.isoformat() if ende else None
    return sorted(tage.values(), key=lambda t: t.day)


__all__ = ["DayRow", "budget_exhausted_at", "daily"]
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/ -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(ai-control): Wer-macht-was und 14-Tage-Verlauf aus der Telemetrie"`

---

### Task 5: Konten und Guthaben

**Files:**
- Create: `app/ai/control/config.py`, `app/ai/control/accounts.py`
- Modify: `.env.example` (Block unter `MOONSHOT_API_KEY=`)
- Test: `tests/unit/ai_control/test_accounts.py`

**Interfaces:**
- Produces:
  - `config.AccountKeys` (BaseSettings, env ohne Präfix): `deepseek_api_key`, `moonshot_api_key`, `moonshot_api_base="https://api.moonshot.ai/v1"`, `openai_admin_key`
  - `config.LiteLLMModels` (Präfix `KAI_LITELLM_`): `bulk_model`, `standard_model`, `reasoning_model`, `critical_model`, `stt_model`, `research_model`
  - `config.ControlThresholds` (Präfix `KAI_AI_CONTROL_`): `balance_min_usd=5.0`, `runway_min_days=7.0`, `early_budget_hour_utc=12`, `proxy_down_minutes=5`, `quiet_start_hour=23`, `quiet_end_hour=7`, `quiet_tz="Europe/Berlin"`, `remind_after_hours=24.0`, `accounts_every_minutes=55`
  - `config.ControlPaths` (dataclass): `telemetry`, `accounts`, `runtime_dir`, `protocol`, `alert_state`, `env_file`
  - `accounts.Account` (dataclass), `parse_deepseek(body)`, `parse_moonshot(body)`, `parse_openai_costs(body)`, `fetch_accounts(keys, *, client, now) -> list[Account]`, `merge_with_previous(new, previous) -> list[Account]`, `write_accounts(path, accounts, now)`, `read_accounts(path) -> tuple[list[dict], datetime | None]`
  - `accounts.TOPUP_URLS: dict[str, str]`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/ai_control/test_accounts.py
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.ai.control import accounts as ac
from app.ai.control.config import AccountKeys

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
DEEPSEEK = {"is_available": True, "balance_infos": [
    {"currency": "USD", "total_balance": "21.61", "granted_balance": "0.00", "topped_up_balance": "21.61"}]}
MOONSHOT = {"code": 0, "data": {"available_balance": 23.78, "voucher_balance": 4.24, "cash_balance": 19.54},
            "scode": "0x0", "status": True}
OPENAI = {"object": "page", "data": [
    {"object": "bucket", "results": [{"object": "organization.costs.result", "amount": {"value": 1.21, "currency": "usd"}}]},
    {"object": "bucket", "results": [{"object": "organization.costs.result", "amount": {"value": 2.89, "currency": "usd"}}]}],
    "has_more": False}


def test_parser() -> None:
    assert ac.parse_deepseek(DEEPSEEK)[:2] == (21.61, "USD")
    bal, cur, detail = ac.parse_moonshot(MOONSHOT)
    assert (bal, cur, detail["voucher_balance"]) == (23.78, "USD", 4.24)
    assert round(ac.parse_openai_costs(OPENAI), 2) == 4.10


def _client(antworten: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        for teil, antwort in antworten.items():
            if teil in str(request.url):
                return antwort
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_abfrage_ohne_schluessel_und_mit_fehler() -> None:
    keys = AccountKeys(_env_file=None, deepseek_api_key="sk-x", moonshot_api_key="", openai_admin_key="")
    client = _client({"/user/balance": httpx.Response(401, json={"error": "nope"})})
    konten = {a.provider: a for a in ac.fetch_accounts(keys, client=client, now=NOW)}
    assert konten["deepseek"].status == "fehler" and konten["deepseek"].error == "HTTP 401"
    assert konten["moonshot"].status == "kein_schluessel"
    assert konten["openai"].status == "kein_api"
    assert "sk-x" not in json.dumps([a.__dict__ for a in konten.values()], default=str)


def test_letzter_guter_wert_bleibt(tmp_path: Path) -> None:
    alt = [ac.Account("deepseek", "ok", 21.61, "USD", {}, None, "2026-10-02T10:00:00+00:00", ac.TOPUP_URLS["deepseek"])]
    neu = [ac.Account("deepseek", "fehler", None, None, {}, "HTTP 500", NOW.isoformat(), ac.TOPUP_URLS["deepseek"])]
    gemischt = ac.merge_with_previous(neu, [a.__dict__ for a in alt])
    assert gemischt[0].balance == 21.61 and gemischt[0].status == "fehler"
    assert gemischt[0].detail["balance_from"] == "2026-10-02T10:00:00+00:00"
    pfad = tmp_path / "ai_accounts.json"
    ac.write_accounts(pfad, gemischt, NOW)
    gelesen, geschrieben = ac.read_accounts(pfad)
    assert gelesen[0]["balance"] == 21.61 and geschrieben == NOW
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_accounts.py -q`

- [ ] **Step 3: Implement**

```python
# app/ai/control/config.py
"""Schalter der KI-Kontrollstation -- hier und NICHT in app/core/settings.py (God-File-Ratchet)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AccountKeys(BaseSettings):
    """Schluessel fuer die Guthaben-Abfrage. ``repr=False``: nie in Logs/Fehlertexten."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deepseek_api_key: str = Field(default="", repr=False)
    moonshot_api_key: str = Field(default="", repr=False)
    moonshot_api_base: str = Field(default="https://api.moonshot.ai/v1")
    openai_admin_key: str = Field(default="", repr=False)


class LiteLLMModels(BaseSettings):
    """Upstream-Modell je LiteLLM-Alias (dieselben Variablen wie config/litellm.yaml)."""

    model_config = SettingsConfigDict(env_prefix="KAI_LITELLM_", env_file=".env", extra="ignore")

    bulk_model: str = ""
    standard_model: str = ""
    reasoning_model: str = ""
    critical_model: str = ""
    stt_model: str = ""
    research_model: str = ""


class ControlThresholds(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAI_AI_CONTROL_", env_file=".env", extra="ignore")

    balance_min_usd: float = 5.0
    runway_min_days: float = 7.0
    early_budget_hour_utc: int = 12
    proxy_down_minutes: int = 5
    quiet_start_hour: int = 23
    quiet_end_hour: int = 7
    quiet_tz: str = "Europe/Berlin"
    remind_after_hours: float = 24.0
    accounts_every_minutes: int = 55


@dataclass(frozen=True)
class ControlPaths:
    telemetry: Path = Path("artifacts/llm_telemetry.jsonl")
    accounts: Path = Path("artifacts/ai_accounts.json")
    runtime_dir: Path = Path("artifacts/runtime")
    protocol: Path = Path("artifacts/runtime/ai_control_protocol.json")
    alert_state: Path = Path("artifacts/runtime/ai_control_alert_state.json")
    env_file: Path = Path(".env")


__all__ = ["AccountKeys", "ControlPaths", "ControlThresholds", "LiteLLMModels"]
```

```python
# app/ai/control/accounts.py
"""Guthaben je Anbieter -- vom Anbieter selbst (Spec §5.4). Nur der Timer ruft das auf."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from app.ai.control.config import AccountKeys

TIMEOUT_S: Final = 10.0
TOPUP_URLS: Final = {
    "deepseek": "https://platform.deepseek.com/top_up",
    "moonshot": "https://platform.moonshot.ai/console/pay",
    "openai": "https://platform.openai.com/settings/organization/billing/overview",
}


@dataclass
class Account:
    provider: str
    status: str  # ok | fehler | kein_schluessel | kein_api
    balance: float | None
    currency: str | None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    fetched_at: str = ""
    topup_url: str = ""


def parse_deepseek(body: dict[str, Any]) -> tuple[float, str, dict[str, Any]]:
    info = (body.get("balance_infos") or [{}])[0]
    return float(info["total_balance"]), str(info.get("currency") or "USD"), {
        "granted_balance": float(info.get("granted_balance") or 0),
        "topped_up_balance": float(info.get("topped_up_balance") or 0),
        "is_available": bool(body.get("is_available")),
    }


def parse_moonshot(body: dict[str, Any]) -> tuple[float, str, dict[str, Any]]:
    data = body["data"]
    return float(data["available_balance"]), "USD", {
        "cash_balance": float(data.get("cash_balance") or 0),
        "voucher_balance": float(data.get("voucher_balance") or 0),
    }


def parse_openai_costs(body: dict[str, Any]) -> float:
    return sum(float(r["amount"]["value"]) for b in body.get("data") or [] for r in b.get("results") or [])


def _abfrage(client: httpx.Client, url: str, schluessel: str, **params: Any) -> dict[str, Any]:
    antwort = client.get(url, headers={"Authorization": f"Bearer {schluessel}"}, params=params or None,
                         timeout=TIMEOUT_S)
    if antwort.status_code != 200:
        raise RuntimeError(f"HTTP {antwort.status_code}")
    return antwort.json()


def fetch_accounts(keys: AccountKeys, *, client: httpx.Client, now: datetime) -> list[Account]:
    stempel = now.astimezone(UTC).isoformat()
    konten: list[Account] = []

    def versuch(provider: str, schluessel: str, holen) -> None:  # noqa: ANN001
        if not schluessel:
            konten.append(Account(provider, "kein_schluessel", None, None, {}, None, stempel, TOPUP_URLS[provider]))
            return
        try:
            balance, waehrung, detail = holen()
            konten.append(Account(provider, "ok", balance, waehrung, detail, None, stempel, TOPUP_URLS[provider]))
        except Exception as exc:  # noqa: BLE001 -- ein Anbieter stoppt die anderen nicht
            text = str(exc) if str(exc).startswith("HTTP ") else type(exc).__name__
            konten.append(Account(provider, "fehler", None, None, {}, text, stempel, TOPUP_URLS[provider]))

    versuch("deepseek", keys.deepseek_api_key,
            lambda: parse_deepseek(_abfrage(client, "https://api.deepseek.com/user/balance", keys.deepseek_api_key)))
    versuch("moonshot", keys.moonshot_api_key,
            lambda: parse_moonshot(_abfrage(client, f"{keys.moonshot_api_base.rstrip('/')}/users/me/balance",
                                            keys.moonshot_api_key)))
    if keys.openai_admin_key:
        monatsanfang = int(now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())

        def openai() -> tuple[float, str, dict[str, Any]]:
            kosten = parse_openai_costs(_abfrage(client, "https://api.openai.com/v1/organization/costs",
                                                 keys.openai_admin_key, start_time=monatsanfang,
                                                 bucket_width="1d", limit=31))
            return kosten, "USD", {"kind": "month_cost"}

        versuch("openai", keys.openai_admin_key, openai)
    else:
        konten.append(Account("openai", "kein_api", None, None, {"hint": "OPENAI_ADMIN_KEY fehlt"},
                              None, stempel, TOPUP_URLS["openai"]))
    return konten


def merge_with_previous(new: list[Account], previous: list[dict[str, Any]]) -> list[Account]:
    alt = {p.get("provider"): p for p in previous}
    for konto in new:
        vorher = alt.get(konto.provider) or {}
        if konto.status == "fehler" and vorher.get("balance") is not None:
            konto.balance = float(vorher["balance"])
            konto.currency = vorher.get("currency")
            konto.detail = {**(vorher.get("detail") or {}),
                            "balance_from": (vorher.get("detail") or {}).get("balance_from") or vorher.get("fetched_at")}
    return new


def write_accounts(path: Path, accounts: list[Account], now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    daten = {"schema": "ai-accounts/v1", "written_at": now.astimezone(UTC).isoformat(),
             "accounts": [asdict(a) for a in accounts]}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-accounts-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, sort_keys=True)
    os.replace(tmp, path)


def read_accounts(path: Path) -> tuple[list[dict[str, Any]], datetime | None]:
    try:
        daten = json.loads(path.read_text(encoding="utf-8"))
        return list(daten.get("accounts") or []), datetime.fromisoformat(daten["written_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return [], None


__all__ = ["Account", "TOPUP_URLS", "fetch_accounts", "merge_with_previous", "parse_deepseek",
           "parse_moonshot", "parse_openai_costs", "read_accounts", "write_accounts"]
```

`.env.example` direkt unter `MOONSHOT_API_KEY=`:

```
# KI-Kontrollstation (02.10.2026): Guthaben-Abfrage. Basis der Kimi-Konto-API (international).
MOONSHOT_API_BASE=https://api.moonshot.ai/v1
# Optional: OpenAI-Admin-Schluessel (Organisation -> Admin keys) fuer den echten Monatsverbrauch.
# Leer = die Kontrollstation zeigt fuer OpenAI nur KAIs eigene Messung.
OPENAI_ADMIN_KEY=
# Schwellen der KI-Kontrollstation (Telegram + Handlungsbedarf)
KAI_AI_CONTROL_BALANCE_MIN_USD=5.0
KAI_AI_CONTROL_RUNWAY_MIN_DAYS=7.0
KAI_AI_CONTROL_EARLY_BUDGET_HOUR_UTC=12
KAI_AI_CONTROL_PROXY_DOWN_MINUTES=5
KAI_AI_CONTROL_QUIET_START_HOUR=23
KAI_AI_CONTROL_QUIET_END_HOUR=7
KAI_AI_CONTROL_QUIET_TZ=Europe/Berlin
KAI_AI_CONTROL_REMIND_AFTER_HOURS=24.0
KAI_AI_CONTROL_ACCOUNTS_EVERY_MINUTES=55
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/test_accounts.py tests/unit/test_env_example_drift.py -q`. Meldet der Drift-Wächter eine neue Lücke, den gemeldeten Namen in `.env.example` nachtragen, nie in die Baseline.
- [ ] **Step 5: Commit** — `git commit -m "feat(ai-control): Guthaben-Abfrage DeepSeek/Kimi/OpenAI-Admin mit letztem gutem Wert"`

---

### Task 6: Konflikte + Protokoll der Schalteränderungen

**Files:**
- Create: `app/ai/control/conflicts.py`, `app/ai/control/protocol.py`
- Test: `tests/unit/ai_control/test_conflicts.py`, `tests/unit/ai_control/test_protocol.py`

**Interfaces:**
- Consumes: `WorkloadKey`, `WorkloadStats`, `ProviderActivity` (Task 4), `LiteLLMModels` (Task 5), `app.ai.modes.unknown_route_keys`
- Produces:
  - `Conflict(key: str, title: str, detail: str)`
  - `find_conflicts(*, route_modes: dict[str, str], models: LiteLLMModels, lock_matches: bool | None, workloads_24h: dict[WorkloadKey, WorkloadStats], activity: dict[str, ProviderActivity]) -> list[Conflict]`
  - `protocol.safe_switches(env_file: Path) -> dict[str, str]`, `protocol.diff(old, new) -> list[dict]`, `protocol.record(paths: ControlPaths, *, now, extra: dict[str, str] | None = None) -> list[dict]` (`extra` z. B. `{"RELEASE": "7446c486"}`), `protocol.read(path) -> list[dict]`, `MAX_ENTRIES = 200`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/ai_control/test_conflicts.py
from app.ai.control.config import LiteLLMModels
from app.ai.control.conflicts import find_conflicts
from app.ai.control.workloads import ProviderActivity, WorkloadKey, WorkloadStats


def models(**kw):  # noqa: ANN001, ANN202
    return LiteLLMModels(_env_file=None, **kw)


def test_route_ohne_modell_und_unbekannte_route() -> None:
    k = find_conflicts(route_modes={"standard": "primary", "standrad": "shadow"}, models=models(),
                       lock_matches=True, workloads_24h={}, activity={})
    schluessel = {c.key for c in k}
    assert "konflikt:route_ohne_modell:standard" in schluessel
    assert "konflikt:unbekannte_route:standrad" in schluessel


def test_baum_lock_und_nur_fehler_und_rueckfall() -> None:
    lite = WorkloadKey("analysis", "standard", "kai-server", "litellm", "deepseek/deepseek-v4-flash")
    k = find_conflicts(
        route_modes={"standard": "primary"}, models=models(standard_model="deepseek/deepseek-v4-flash"),
        lock_matches=False,
        workloads_24h={lite: WorkloadStats(calls=12, ok=0, failures=12, fallbacks=12)},
        activity={"moonshot": ProviderActivity(schema_errors_24h=3)},
    )
    schluessel = {c.key for c in k}
    assert {"konflikt:baum_lock", "konflikt:nur_fehler:standard", "konflikt:rueckfall:standard",
            "konflikt:schemafehler:moonshot"} <= schluessel


def test_ruhiger_zustand_ohne_konflikt() -> None:
    assert find_conflicts(route_modes={"research": "advisory"}, models=models(research_model="moonshot/kimi-k2.6"),
                          lock_matches=True, workloads_24h={}, activity={}) == []
```

```python
# tests/unit/ai_control/test_protocol.py
from datetime import UTC, datetime
from pathlib import Path

from app.ai.control import protocol
from app.ai.control.config import ControlPaths

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_nur_sichere_schalter_ohne_geheimnisse(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('KAI_INFERENCE_ROUTE_MODES={"standard":"primary"}\nKAI_INFERENCE_LITELLM_API_KEY=sk-geheim\n'
                   "OPENAI_API_KEY=sk-x\nSOURCE_LLM_SPARFENSTER_MODE=off\n# kommentar\n")
    werte = protocol.safe_switches(env)
    assert werte == {"KAI_INFERENCE_ROUTE_MODES": '{"standard":"primary"}', "SOURCE_LLM_SPARFENSTER_MODE": "off"}


def test_aenderung_landet_im_protokoll_begrenzt(tmp_path: Path) -> None:
    paths = ControlPaths(env_file=tmp_path / ".env", protocol=tmp_path / "rt" / "p.json",
                         runtime_dir=tmp_path / "rt")
    paths.env_file.write_text("SOURCE_LLM_SPARFENSTER_MODE=off\n")
    assert protocol.record(paths, now=NOW) == []  # erster Lauf = Basis, keine Aenderung
    paths.env_file.write_text("SOURCE_LLM_SPARFENSTER_MODE=enforce\n")
    neu = protocol.record(paths, now=NOW)
    assert neu[0]["key"] == "SOURCE_LLM_SPARFENSTER_MODE" and neu[0]["old"] == "off" and neu[0]["new"] == "enforce"
    assert protocol.read(paths.protocol)[0]["new"] == "enforce"
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_conflicts.py tests/unit/ai_control/test_protocol.py -q`

- [ ] **Step 3: Implement**

```python
# app/ai/control/conflicts.py
"""Widersprueche zwischen Konfiguration und Messwerten (Spec §5.5) -- rein, ohne I/O."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.control.config import LiteLLMModels
from app.ai.control.workloads import ProviderActivity, WorkloadKey, WorkloadStats
from app.ai.modes import unknown_route_keys

_AN = {"shadow", "primary", "advisory"}


@dataclass(frozen=True)
class Conflict:
    key: str
    title: str
    detail: str


def find_conflicts(
    *,
    route_modes: dict[str, str],
    models: LiteLLMModels,
    lock_matches: bool | None,
    workloads_24h: dict[WorkloadKey, WorkloadStats],
    activity: dict[str, ProviderActivity],
) -> list[Conflict]:
    funde: list[Conflict] = []
    for route in unknown_route_keys(route_modes):
        funde.append(Conflict(f"konflikt:unbekannte_route:{route}", f"Unbekannte Route „{route}“",
                              "KAI_INFERENCE_ROUTE_MODES nennt eine Route, die es nicht gibt -- Tippfehler?"))
    for route, modus in route_modes.items():
        if str(modus).strip().lower() in _AN and not getattr(models, f"{route}_model", "x"):
            funde.append(Conflict(f"konflikt:route_ohne_modell:{route}", f"Route {route} ohne Modell",
                                  f"Modus {modus}, aber KAI_LITELLM_{route.upper()}_MODEL ist leer."))
    if lock_matches is False:
        funde.append(Conflict("konflikt:baum_lock", "LiteLLM-Baum passt nicht zum Lock",
                              "Der laufende Transportbaum ist nicht der gepruefte."))
    je_route: dict[str, WorkloadStats] = {}
    for key, st in workloads_24h.items():
        if key.transport != "litellm":
            continue
        summe = je_route.setdefault(key.route, WorkloadStats())
        summe.calls += st.calls
        summe.ok += st.ok
        summe.fallbacks += st.fallbacks
    for route, st in je_route.items():
        if st.calls > 0 and st.ok == 0:
            funde.append(Conflict(f"konflikt:nur_fehler:{route}", f"LiteLLM-Route {route}: nur Fehler",
                                  f"{st.calls} Aufrufe in 24 h, keiner erfolgreich."))
        if str(route_modes.get(route, "")).lower() == "primary" and st.calls >= 10 and st.fallbacks / st.calls > 0.2:
            funde.append(Conflict(f"konflikt:rueckfall:{route}", f"Route {route} faellt oft zurueck",
                                  f"{st.fallbacks} von {st.calls} Aufrufen auf direct zurueckgefallen."))
    for name, a in activity.items():
        if a.schema_errors_24h >= 3:
            funde.append(Conflict(f"konflikt:schemafehler:{name}", f"{name}: wiederholt ungueltige Antworten",
                                  f"{a.schema_errors_24h} Schema-Fehler in 24 h (z. B. Codeblock statt JSON)."))
    return funde


__all__ = ["Conflict", "find_conflicts"]
```

```python
# app/ai/control/protocol.py
"""Protokoll der KI-Schalter: Fingerabdruck der NICHT geheimen Werte, Aenderungen als JSON (max. 200).

Bewusst JSON statt JSONL: ein neuer *.jsonl-Strom braeuchte einen Strom-Vertrag mit
Frische-Pruefung in app/alerts/health_check.py (God-File ohne Spielraum).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.ai.control.config import ControlPaths

PREFIXES: Final = ("KAI_INFERENCE_", "KAI_LITELLM_", "SOURCE_LLM_SPARFENSTER_", "APP_AI_BUDGET_",
                   "KAI_AI_CONTROL_")
GEHEIM: Final = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS")
MAX_ENTRIES: Final = 200


def safe_switches(env_file: Path) -> dict[str, str]:
    werte: dict[str, str] = {}
    try:
        zeilen = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return werte
    for zeile in zeilen:
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, _, wert = zeile.partition("=")
        name = name.strip()
        if name.startswith(PREFIXES) and not any(g in name for g in GEHEIM):
            werte[name] = wert.strip().strip('"').strip("'")
    return werte


def diff(old: dict[str, str], new: dict[str, str]) -> list[dict[str, Any]]:
    return [{"key": k, "old": old.get(k), "new": new.get(k)}
            for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]


def _atomar(path: Path, daten: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-protocol-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, sort_keys=True)
    os.replace(tmp, path)


def read(path: Path) -> list[dict[str, Any]]:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("entries") or [])
    except (OSError, ValueError, AttributeError):
        return []


def record(paths: ControlPaths, *, now: datetime, extra: dict[str, str] | None = None) -> list[dict[str, Any]]:
    basis = paths.runtime_dir / "ai_control_switches.json"
    jetzt = {**safe_switches(paths.env_file), **(extra or {})}
    try:
        vorher = json.loads(basis.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _atomar(basis, jetzt)
        return []
    aenderungen = diff(vorher, jetzt)
    if aenderungen:
        stempel = now.astimezone(UTC).isoformat()
        neu = [{"ts": stempel, "actor": "Konfiguration", "kind": "schalter", **a} for a in aenderungen]
        _atomar(paths.protocol, {"schema": "ai-control-protocol/v1",
                                 "entries": (neu + read(paths.protocol))[:MAX_ENTRIES]})
        _atomar(basis, jetzt)
    return aenderungen


__all__ = ["MAX_ENTRIES", "diff", "read", "record", "safe_switches"]
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/ -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(ai-control): Konfliktregeln und Protokoll der KI-Schalter"`

---

### Task 7: Vertrag `ai-control/v1` (Snapshot + Handlungsbedarf)

**Files:**
- Create: `app/ai/control/snapshot.py`
- Test: `tests/unit/ai_control/test_snapshot.py`

**Interfaces:**
- Consumes: alles aus Task 2–6; `app.ai.spend.load_rows/current_budget_status/month_projection`; `app.ai.modes.resolve_mode`; `app.ai.routes.ROUTES`; `app.analysis.llm_sparfenster.resolve_sparfenster`; Transport-Snapshot (`ai-transport/v1`-Dict oder `None`)
- Produces: `build_snapshot(*, now, paths, inference, transport, thresholds, models, providers_configured: dict[str, bool], budget=None) -> dict` mit den Schlüsseln `schema, generated_at, summary, attention, connections, workloads, accounts, protocol, null_reasons`. Jedes Element in `attention`: `{key, severity: "warn"|"crit", title, detail, since, min_age_min, action}`.

Regeln für `attention` (Spec §6.1):
- Konto mit Guthaben < Schwelle oder Reichweite < Schwelle → `guthaben:<p>`, Aktion `topup`
- Konto `fehler` → `konto_abfrage:<p>`
- Kontodatei fehlt oder ist älter als 3 h → `konten_veraltet`
- Budgetende heute vor `early_budget_hour_utc` → `budget_frueh:<datum>`
- Monatsprognose > Monatslimit → `monat:<yyyy-mm>`
- jedes Objekt `gestoert` → `gestoert:<art>:<name>`; `crit`, wenn die zugehörige Route `primary` ist; beim Proxy `min_age_min = proxy_down_minutes`
- jeder Konflikt → sein Schlüssel

- [ ] **Step 1: Failing tests**

```python
# tests/unit/ai_control/test_snapshot.py
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ai.config import InferenceSettings
from app.ai.control.config import ControlPaths, ControlThresholds, LiteLLMModels
from app.ai.control.snapshot import build_snapshot

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _paths(tmp: Path) -> ControlPaths:
    return ControlPaths(telemetry=tmp / "t.jsonl", accounts=tmp / "a.json", runtime_dir=tmp / "rt",
                        protocol=tmp / "rt" / "p.json", alert_state=tmp / "rt" / "s.json", env_file=tmp / ".env")


def _bau(tmp: Path, **kw):  # noqa: ANN001, ANN202
    return build_snapshot(
        now=NOW, paths=_paths(tmp),
        inference=InferenceSettings(enabled=True, mode_ceiling="primary",
                                    route_modes={"research": "advisory", "standard": "off"}),
        transport=kw.pop("transport", None), thresholds=ControlThresholds(_env_file=None),
        models=LiteLLMModels(_env_file=None, standard_model="deepseek/deepseek-v4-flash",
                             research_model="moonshot/kimi-k2.6"),
        providers_configured={"openai": True, "anthropic": False, "gemini": False, "xai": True}, **kw,
    )


def _nulls_ohne_grund(wert, pfad: str, gruende: dict) -> list[str]:  # noqa: ANN001
    if isinstance(wert, dict):
        return [x for k, v in wert.items() for x in _nulls_ohne_grund(v, f"{pfad}.{k}" if pfad else k, gruende)]
    return [pfad] if wert is None and pfad not in gruende else []


def test_snapshot_ohne_telemetrie_hat_gruende(tmp_path: Path) -> None:
    snap = _bau(tmp_path)
    assert snap["schema"] == "ai-control/v1"
    assert _nulls_ohne_grund(snap["summary"], "summary", snap["null_reasons"]) == []
    analyse = next(w for w in snap["workloads"] if w["purpose"] == "analysis")
    assert analyse["state"] == "bereit" and analyse["calls_today"] == 0


def test_route_aus_ist_deaktiviert_und_proxy_unbekannt(tmp_path: Path) -> None:
    snap = _bau(tmp_path)
    alias = {a["alias"]: a for a in snap["connections"]["aliases"]}
    assert alias["kai-standard"]["state"] == "deaktiviert"
    assert snap["connections"]["proxy"]["state"] is None
    assert "connections.proxy.state" in snap["null_reasons"]


def test_veraltete_konten_erzeugen_hinweis(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps({"schema": "ai-accounts/v1",
        "written_at": (NOW - timedelta(hours=4)).isoformat(),
        "accounts": [{"provider": "moonshot", "status": "ok", "balance": 3.0, "currency": "USD",
                      "detail": {}, "error": None, "fetched_at": (NOW - timedelta(hours=4)).isoformat(),
                      "topup_url": "https://x"}]}))
    keys = {h["key"] for h in _bau(tmp_path)["attention"]}
    assert {"konten_veraltet", "guthaben:moonshot"} <= keys


def test_proxy_tot_ist_gestoert_mit_mindestalter(tmp_path: Path) -> None:
    transport = {"transport": {"proxy_alive": False, "proxy_status_code": None, "version": "1.102.1",
                               "lock_matches": True, "verified_at": NOW.isoformat()}, "routes": []}
    snap = _bau(tmp_path, transport=transport)
    assert snap["connections"]["proxy"]["state"] == "gestoert"
    hinweis = next(h for h in snap["attention"] if h["key"] == "gestoert:proxy:litellm")
    assert hinweis["min_age_min"] == 5
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_snapshot.py -q`

- [ ] **Step 3: Implement**

```python
# app/ai/control/snapshot.py
"""Vertrag ``ai-control/v1`` -- liest nur Dateien, bewertet mit app.ai.control.states (Spec §4-§6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.ai.circuit_export import read_circuits
from app.ai.config import InferenceSettings
from app.ai.control import history, protocol
from app.ai.control.accounts import read_accounts
from app.ai.control.config import ControlPaths, ControlThresholds, LiteLLMModels
from app.ai.control.conflicts import find_conflicts
from app.ai.control.states import Signals, State, Verdict, classify
from app.ai.control.workloads import aggregate, provider_activity
from app.ai.modes import resolve_mode
from app.ai.routes import ROUTES

SCHEMA: Final = "ai-control/v1"
ACCOUNTS_STALE: Final = timedelta(hours=3)
TASKS: Final = (("analysis", "standard", "Analyse"), ("chat", "standard", "Chat"),
                ("intent", "critical", "Freitext"), ("stt", "stt", "Sprache"),
                ("consensus", "reasoning", "Konsens"), ("research", "research", "Research"))
LITELLM_UPSTREAMS: Final = {"deepseek": "deepseek/", "moonshot": "moonshot/", "gemini": "gemini/"}


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).isoformat() if ts else None


def _verdict(v: Verdict) -> dict[str, Any]:
    return {"state": v.state.value, "reason": v.reason, "since": _iso(v.since)}


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
    from app.ai.spend import current_budget_status, load_rows, month_projection

    gruende: dict[str, str] = {}
    rows = load_rows(paths.telemetry)
    mitternacht = now.replace(hour=0, minute=0, second=0, microsecond=0)
    heute = aggregate(rows, since=mitternacht, until=now)
    tag24 = aggregate(rows, since=now - timedelta(hours=24), until=now)
    stunde = aggregate(rows, since=now - timedelta(hours=1), until=now)
    aktiv = provider_activity(rows, now=now)
    status, today, month = budget or current_budget_status(path=paths.telemetry, now=now)
    projektion = month_projection(month, monthly_limit_usd=status.policy.monthly_limit_usd, now=now)
    ende = history.budget_exhausted_at(rows, day=now.date().isoformat())
    ceiling = inference.mode_ceiling if inference.enabled else "off"

    def modus(route: str) -> str:
        return str(resolve_mode(route, per_route=inference.route_modes, ceiling=ceiling))  # type: ignore[arg-type]

    # --- Kopfzeile -------------------------------------------------------------
    rate = today.known_cost_usd / max((now - mitternacht).total_seconds() / 3600, 0.25)
    limit = status.policy.daily_limit_usd
    schaetzung: str | None = None
    if ende is None and limit is not None and rate > 0:
        rest_h = max(limit - today.known_cost_usd, 0.0) / rate
        reicht = now + timedelta(hours=rest_h)
        schaetzung = _iso(reicht) if reicht.date() == now.date() else "tagesende"
    summary: dict[str, Any] = {
        "today_usd": round(today.known_cost_usd, 4), "today_limit_usd": limit,
        "month_usd": round(month.known_cost_usd, 4), "month_limit_usd": status.policy.monthly_limit_usd,
        "projected_month_usd": projektion.projected_month_usd, "budget_state": status.state,
        "budget_exhausted_at": _iso(ende), "budget_end_estimate": schaetzung,
        "calls_today": today.calls, "tokens_in_today": today.input_tokens, "tokens_out_today": today.output_tokens,
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
        gruende["summary.budget_end_estimate"] = ("bereits erschoepft" if ende else
                                                  "ohne Tageslimit oder ohne Verbrauch keine Schaetzung")

    budget_leer = status.state == "LIMIT_REACHED" or ende is not None
    morgen = (mitternacht + timedelta(days=1))

    # --- Verbindungen ----------------------------------------------------------
    t = (transport or {}).get("transport") or {}
    proxy: dict[str, Any]
    if not transport:
        proxy = {"state": None, "reason": None, "since": None, "version": None, "lock_matches": None}
        gruende["connections.proxy.state"] = "KI-Transport-Status nicht lesbar"
    else:
        lebt = bool(t.get("proxy_alive"))
        v = classify(Signals(now=now, configured=True, proxy_down=not lebt, last_ok=now if lebt else None))
        proxy = {**_verdict(v), "version": t.get("version"), "lock_matches": t.get("lock_matches"),
                 "verified_at": t.get("verified_at"), "status_code": t.get("proxy_status_code")}
    kreise = read_circuits(now=now, directory=paths.runtime_dir)
    aliase: list[dict[str, Any]] = []
    for route in ROUTES:
        modell = getattr(models, f"{route}_model", "")
        m = modus(route)
        alias = inference.route_aliases.get(route, route)
        offen = any(k.get("alias") == alias and k.get("state") == "open" for c in kreise if not c.stale for k in c.keys)
        halb = any(k.get("alias") == alias and k.get("state") == "half_open" for c in kreise if not c.stale for k in c.keys)
        lite = [st for key, st in heute.items() if key.route == route and key.transport == "litellm"]
        v = classify(Signals(
            now=now, configured=m != "off" and bool(modell),
            disabled_reason="Route aus" if m == "off" else "kein Modell konfiguriert",
            proxy_down=bool(transport) and not t.get("proxy_alive"), circuit_open=offen,
            paused_reason="Circuit prueft Wiederanlauf" if halb else "",
            last_ok=max((s.last_ok for s in lite if s.last_ok), default=None),
        ))
        aliase.append({"alias": alias, "route": route, "mode": m, "upstream_model": modell or None, **_verdict(v)})
    anbieter: list[dict[str, Any]] = []
    genutzt = {p for p, praefix in LITELLM_UPSTREAMS.items()
               if any(getattr(models, f"{r}_model", "").startswith(praefix) and modus(r) != "off" for r in ROUTES)}
    konten, konten_stand = read_accounts(paths.accounts)
    konto_je = {k.get("provider"): k for k in konten}
    for name in ("openai", "deepseek", "moonshot", "xai", "anthropic", "gemini"):
        a = aktiv.get(name)
        if name == "openai":
            an, grund = providers_configured.get("openai", False), "kein OPENAI_API_KEY"
        elif name in LITELLM_UPSTREAMS:
            an, grund = name in genutzt, "keine aktive Route nutzt diesen Anbieter"
        else:
            an = providers_configured.get(name, False) and a is not None and a.calls_24h > 0
            grund = "Schluessel gesetzt, von KAI nicht genutzt" if providers_configured.get(name) else "kein Schluessel"
        konto = konto_je.get(name) or {}
        v = classify(Signals(
            now=now, configured=an, disabled_reason=grund,
            consecutive_failures=a.consecutive_failures if a else 0,
            calls_1h=a.calls_1h if a else 0, failures_1h=a.failures_1h if a else 0,
            balance_exhausted=(konto.get("balance") is not None and float(konto["balance"]) <= 0)
            or bool(a and a.quota_errors_24h),
            paused_reason="Tagesbudget leer" if (budget_leer and name == "openai") else "",
            paused_until=morgen if (budget_leer and name == "openai") else None,
            last_ok=a.last_ok if a else None,
        ))
        anbieter.append({"name": name, "kind": "direct" if name in ("openai", "xai", "anthropic") else "litellm",
                         **_verdict(v), "calls_24h": a.calls_24h if a else 0,
                         "failures_24h": a.failures_24h if a else 0,
                         "circuits": [{"service": c.service, "stale": c.stale, "keys": c.keys} for c in kreise]})

    # --- Wer macht was ---------------------------------------------------------
    from app.analysis.llm_sparfenster import resolve_sparfenster

    fenster = resolve_sparfenster()
    workloads: list[dict[str, Any]] = []
    for zweck, route, titel in TASKS:
        m = modus(route)
        teile = [(k, s) for k, s in heute.items() if k.purpose == zweck]
        teile24 = [s for k, s in tag24.items() if k.purpose == zweck]
        aufrufe24 = sum(s.calls for s in teile24)
        fehler24 = sum(s.failures for s in teile24)
        spar = fenster.verdict(source=None, at=now) if zweck == "analysis" else None
        pausiert = ("Tagesbudget leer" if budget_leer and route != "critical" else
                    "Sparfenster aktiv" if spar else "")
        v = classify(Signals(
            now=now, configured=True, paused_reason=pausiert,
            paused_until=morgen if pausiert == "Tagesbudget leer" else None,
            calls_1h=sum(s.calls for k, s in stunde.items() if k.purpose == zweck),
            failures_1h=sum(s.failures for k, s in stunde.items() if k.purpose == zweck),
            last_ok=max((s.last_ok for _, s in teile if s.last_ok), default=None),
        ))
        workloads.append({
            "purpose": zweck, "title": titel, "route": route, "mode": m, **_verdict(v),
            "calls_today": sum(s.calls for _, s in teile),
            "tokens_in_today": sum(s.input_tokens for _, s in teile),
            "tokens_out_today": sum(s.output_tokens for _, s in teile),
            "approx_kb_today": round(sum(s.approx_kb for _, s in teile), 1),
            "cost_today_usd": round(sum(s.known_cost_usd for _, s in teile), 4),
            "unknown_cost_calls_today": sum(s.unknown_cost_calls for _, s in teile),
            "failure_rate_24h": round(fehler24 / aufrufe24, 4) if aufrufe24 else None,
            "fallbacks_today": sum(s.fallbacks for _, s in teile),
            "sparfenster": fenster.mode if zweck == "analysis" else None,
            "parts": [{"service": k.service, "transport": k.transport, "model": k.model, "calls": s.calls,
                       "cost_usd": round(s.known_cost_usd, 4), "last_call": _iso(s.last_call),
                       "top_sources": sorted(s.sources.items(), key=lambda x: -x[1])[:5]} for k, s in teile],
        })

    # --- Konten ----------------------------------------------------------------
    veraltet = konten_stand is None or now - konten_stand > ACCOUNTS_STALE
    konten_aus: list[dict[str, Any]] = []
    for k in konten:
        kosten7 = (aktiv.get(str(k.get("provider"))) or None)
        tagesrate = (kosten7.cost_7d_usd / 7) if kosten7 and kosten7.cost_7d_usd > 0 else None
        reichweite = (float(k["balance"]) / tagesrate) if (k.get("balance") is not None and tagesrate) else None
        konten_aus.append({**k, "runway_days": round(reichweite, 1) if reichweite is not None else None,
                           "stale": veraltet})

    # --- Handlungsbedarf -------------------------------------------------------
    hinweise: list[dict[str, Any]] = []

    def hinweis(key: str, title: str, detail: str, *, severity: str = "warn", min_age_min: int = 0,
                action: dict[str, Any] | None = None) -> None:
        hinweise.append({"key": key, "severity": severity, "title": title, "detail": detail,
                         "since": _iso(now), "min_age_min": min_age_min, "action": action or {"kind": "details"}})

    if veraltet:
        hinweis("konten_veraltet", "Kontoabfrage veraltet",
                "Keine frische Guthaben-Abfrage seit ueber 3 h -- laeuft kai-ai-control.timer?")
    for k in konten_aus:
        p = str(k.get("provider"))
        if k.get("status") == "fehler":
            hinweis(f"konto_abfrage:{p}", f"Kontoabfrage {p} fehlgeschlagen", str(k.get("error")))
        bal, runway = k.get("balance"), k.get("runway_days")
        if bal is not None and (float(bal) < thresholds.balance_min_usd
                                or (runway is not None and runway < thresholds.runway_min_days)):
            hinweis(f"guthaben:{p}", f"Guthaben {p} knapp",
                    f"{float(bal):.2f} $" + (f" · reicht ~{runway:.0f} Tage" if runway is not None else ""),
                    action={"kind": "topup", "url": k.get("topup_url")})
    if ende is not None and ende.hour < thresholds.early_budget_hour_utc:
        hinweis(f"budget_frueh:{now.date().isoformat()}", "Tagesbudget frueh aufgebraucht",
                f"seit {ende:%H:%M} UTC nur noch Regelanalyse")
    if (projektion.projected_month_usd is not None and status.policy.monthly_limit_usd is not None
            and projektion.projected_month_usd > status.policy.monthly_limit_usd):
        hinweis(f"monat:{now:%Y-%m}", "Monatsprognose ueber Limit",
                f"{projektion.projected_month_usd:.2f} $ > {status.policy.monthly_limit_usd:.2f} $")
    if proxy.get("state") == State.GESTOERT.value:
        primary = any(modus(r) == "primary" for r in ROUTES)
        hinweis("gestoert:proxy:litellm", "LiteLLM-Proxy gestoert", str(proxy.get("reason")),
                severity="crit" if primary else "warn", min_age_min=thresholds.proxy_down_minutes)
    for a in aliase:
        if a["state"] == State.GESTOERT.value:
            hinweis(f"gestoert:alias:{a['alias']}", f"Route {a['route']} gestoert", a["reason"],
                    severity="crit" if a["mode"] == "primary" else "warn")
    for p in anbieter:
        if p["state"] == State.GESTOERT.value:
            hinweis(f"gestoert:anbieter:{p['name']}", f"Anbieter {p['name']} gestoert", p["reason"])
    for w in workloads:
        if w["state"] == State.GESTOERT.value:
            hinweis(f"gestoert:aufgabe:{w['purpose']}", f"{w['title']} gestoert", w["reason"],
                    severity="crit" if w["mode"] == "primary" else "warn")
    for c in find_conflicts(route_modes={k: str(v) for k, v in inference.route_modes.items()}, models=models,
                            lock_matches=t.get("lock_matches") if transport else None,
                            workloads_24h=tag24, activity=aktiv):
        hinweis(c.key, c.title, c.detail)

    zaehler: dict[str, int] = {}
    for obj in [proxy, *aliase, *anbieter, *workloads]:
        if obj.get("state"):
            zaehler[obj["state"]] = zaehler.get(obj["state"], 0) + 1
    summary["state_counts"] = zaehler
    return {
        "schema": SCHEMA, "generated_at": _iso(now), "summary": summary, "attention": hinweise,
        "connections": {"proxy": proxy, "aliases": aliase, "providers": anbieter},
        "workloads": workloads, "accounts": konten_aus, "accounts_written_at": _iso(konten_stand),
        "protocol": protocol.read(paths.protocol)[:30], "null_reasons": gruende,
    }


__all__ = ["SCHEMA", "TASKS", "build_snapshot"]
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/ -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(ai-control): Vertrag ai-control/v1 mit Zustaenden, Wer-macht-was, Konten, Handlungsbedarf"`

---

### Task 8: API-Router

**Files:**
- Create: `app/api/routers/ai_control.py`
- Modify: `app/api/main.py` — nach `app.include_router(health.router)`: `app.include_router(ai_control.router)` + Import in der Router-Importzeile
- Test: `tests/unit/ai_control/test_router.py`

**Interfaces:**
- Consumes: `build_snapshot`, `history.daily`, `ai_transport_snapshot`, `default_transport_paths`, `inference_settings`, `app.core.settings.get_settings`
- Produces: `GET /dashboard/api/ai/control` → `ai-control/v1`; `GET /dashboard/api/ai/control/history?days=14` → `{"schema": "ai-control-history/v1", "days": [DayRow…]}`

- [ ] **Step 1: Failing test**

```python
# tests/unit/ai_control/test_router.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ai_control


def test_endpunkte_liefern_vertraege(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    async def ohne_transport(**_kw):  # noqa: ANN003, ANN202
        return None

    monkeypatch.setattr(ai_control, "_transport", ohne_transport)
    monkeypatch.chdir(tmp_path)
    app = FastAPI()
    app.include_router(ai_control.router)
    client = TestClient(app)
    r = client.get("/dashboard/api/ai/control")
    assert r.status_code == 200 and r.json()["schema"] == "ai-control/v1"
    assert r.headers["cache-control"].startswith("no-store")
    h = client.get("/dashboard/api/ai/control/history?days=3")
    assert h.status_code == 200 and len(h.json()["days"]) == 3
    assert client.get("/dashboard/api/ai/control/history?days=99").status_code == 422
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_router.py -q`

- [ ] **Step 3: Implement**

```python
# app/api/routers/ai_control.py
"""KI-Kontrollstation -- nur lesend (Stufe 1). Auth: globale Middleware wie /health/ai."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Response

router = APIRouter(tags=["ai-control"])


async def _transport(**_kw: Any) -> dict[str, Any] | None:
    from app.ai.runtime import inference_settings
    from app.ai.transport_status import ai_transport_snapshot, default_transport_paths

    try:
        configured = inference_settings(None)
        return await ai_transport_snapshot(settings=configured, paths=default_transport_paths(configured))
    except Exception:  # noqa: BLE001 -- ohne Transport-Status zeigt die Seite einen Grund
        return None


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"


@router.get("/dashboard/api/ai/control")
async def ai_control(response: Response) -> dict[str, Any]:
    from app.ai.control.config import ControlPaths, ControlThresholds, LiteLLMModels
    from app.ai.control.snapshot import build_snapshot
    from app.ai.runtime import inference_settings
    from app.core.settings import get_settings

    _no_store(response)
    p = get_settings().providers
    return build_snapshot(
        now=datetime.now(UTC), paths=ControlPaths(), inference=inference_settings(None),
        transport=await _transport(), thresholds=ControlThresholds(), models=LiteLLMModels(),
        providers_configured={"openai": bool(p.openai_api_key), "anthropic": bool(p.anthropic_api_key),
                              "gemini": bool(p.gemini_api_key), "xai": bool(p.xai_api_key)},
    )


@router.get("/dashboard/api/ai/control/history")
async def ai_control_history(response: Response, days: int = Query(14, ge=1, le=31)) -> dict[str, Any]:
    from app.ai.control import history
    from app.ai.control.config import ControlPaths
    from app.ai.spend import load_rows

    _no_store(response)
    tage = history.daily(load_rows(ControlPaths().telemetry), now=datetime.now(UTC), days=days)
    return {"schema": "ai-control-history/v1", "days": [asdict(t) for t in tage]}
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/test_router.py tests/unit/test_operator_api.py -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(api): /dashboard/api/ai/control und /history (nur lesend)"`

---

### Task 9: Telegram-Hinweise mit Entprellung und Ruhezeit

**Files:**
- Create: `app/ai/control/alerts.py`
- Test: `tests/unit/ai_control/test_alerts.py`

**Interfaces:**
- Consumes: `snapshot["attention"]` (Task 7), `ControlThresholds`
- Produces: `plan(attention: list[dict], state: dict, *, now, thresholds) -> tuple[str | None, dict]` (eine Nachricht oder `None`, neuer Zustand); `load_state(path) -> dict`, `save_state(path, state)`; `is_quiet(now, thresholds) -> bool`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/ai_control/test_alerts.py
from datetime import UTC, datetime, timedelta

from app.ai.control.alerts import is_quiet, plan
from app.ai.control.config import ControlThresholds

TH = ControlThresholds(_env_file=None)
TAG = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)   # 12:00 MESZ
NACHT = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)  # 02:00 MESZ


def h(key: str, severity: str = "warn", min_age_min: int = 0) -> dict:
    return {"key": key, "severity": severity, "title": f"T {key}", "detail": "d", "min_age_min": min_age_min}


def test_neu_einmal_dann_ruhe_dann_erinnerung_dann_behoben() -> None:
    text, st = plan([h("a")], {}, now=TAG, thresholds=TH)
    assert text and "T a" in text
    assert plan([h("a")], st, now=TAG + timedelta(hours=1), thresholds=TH)[0] is None
    text2, st2 = plan([h("a")], st, now=TAG + timedelta(hours=25), thresholds=TH)
    assert text2 and "weiterhin" in text2
    text3, st3 = plan([], st2, now=TAG + timedelta(hours=26), thresholds=TH)
    assert text3 and "behoben" in text3 and "a" not in st3["open"]


def test_mindestalter() -> None:
    text, st = plan([h("p", min_age_min=5)], {}, now=TAG, thresholds=TH)
    assert text is None
    text2, _ = plan([h("p", min_age_min=5)], st, now=TAG + timedelta(minutes=6), thresholds=TH)
    assert text2 and "T p" in text2


def test_ruhezeit_sammelt_und_liefert_morgens() -> None:
    assert is_quiet(NACHT, TH) and not is_quiet(TAG, TH)
    text, st = plan([h("n")], {}, now=NACHT, thresholds=TH)
    assert text is None
    text, st = plan([], st, now=NACHT + timedelta(hours=2), thresholds=TH)
    assert text is None
    morgens = datetime(2026, 10, 2, 5, 5, tzinfo=UTC)  # 07:05 MESZ
    text, st = plan([], st, now=morgens, thresholds=TH)
    assert text and "Ruhezeit" in text and "T n" in text and "behoben" in text
    assert st["pending"] == []


def test_kritisch_bricht_die_ruhezeit() -> None:
    text, _ = plan([h("k", severity="crit")], {}, now=NACHT, thresholds=TH)
    assert text and "T k" in text
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_alerts.py -q`

- [ ] **Step 3: Implement**

```python
# app/ai/control/alerts.py
"""Telegram-Hinweise der KI-Kontrollstation -- einmal, Erinnerung nach 24 h, „behoben“, Ruhezeit (Spec §6.1)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.ai.control.config import ControlThresholds


def is_quiet(now: datetime, thresholds: ControlThresholds) -> bool:
    stunde = now.astimezone(ZoneInfo(thresholds.quiet_tz)).hour
    a, b = thresholds.quiet_start_hour, thresholds.quiet_end_hour
    return (stunde >= a or stunde < b) if a > b else (a <= stunde < b)


def plan(
    attention: list[dict[str, Any]], state: dict[str, Any], *, now: datetime, thresholds: ControlThresholds
) -> tuple[str | None, dict[str, Any]]:
    offen: dict[str, Any] = dict(state.get("open") or {})
    pending: list[str] = list(state.get("pending") or [])
    ruhe = is_quiet(now, thresholds)
    zeilen: list[str] = []
    stempel = now.isoformat()

    def ausgeben(text: str, kritisch: bool) -> bool:
        if ruhe and not kritisch:
            pending.append(text)
            return False
        zeilen.append(text)
        return True

    aktuelle = {a["key"]: a for a in attention}
    for key, a in aktuelle.items():
        eintrag = offen.get(key) or {"first_seen": stempel, "last_sent": None, "title": a["title"]}
        offen[key] = eintrag
        alter = now - datetime.fromisoformat(eintrag["first_seen"])
        if alter < timedelta(minutes=int(a.get("min_age_min") or 0)):
            continue
        kritisch = a.get("severity") == "crit"
        symbol = "⛔" if kritisch else "⚠"
        if eintrag["last_sent"] is None:
            if ausgeben(f"{symbol} {a['title']}: {a['detail']}", kritisch) or ruhe:
                eintrag["last_sent"] = stempel
        elif now - datetime.fromisoformat(eintrag["last_sent"]) >= timedelta(hours=thresholds.remind_after_hours):
            if ausgeben(f"↻ weiterhin: {a['title']}: {a['detail']}", kritisch):
                eintrag["last_sent"] = stempel
    for key in [k for k in offen if k not in aktuelle]:
        eintrag = offen.pop(key)
        if eintrag.get("last_sent"):
            ausgeben(f"✓ behoben: {eintrag['title']}", False)
    if not ruhe and pending:
        zeilen = ["Nachtrag aus der Ruhezeit:", *pending, *zeilen]
        pending = []
    text = ("KI-Kontrolle\n" + "\n".join(zeilen)) if zeilen else None
    return text, {"open": offen, "pending": pending}


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-alert-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, sort_keys=True)
    os.replace(tmp, path)


__all__ = ["is_quiet", "load_state", "plan", "save_state"]
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/test_alerts.py -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(ai-control): Telegram-Hinweise mit Entprellung, Erinnerung, behoben, Ruhezeit"`

---

### Task 10: Timer (Tick) + Units

**Files:**
- Create: `app/ai/control/tick.py`, `deploy/systemd/kai-ai-control.service`, `deploy/systemd/kai-ai-control.timer`
- Modify: `deploy/systemd/enabled_set.txt` (Zeile `kai-ai-control.timer` alphabetisch einsortieren)
- Test: `tests/unit/ai_control/test_tick.py`

**Interfaces:**
- Consumes: alles aus Task 5–9; `app.alerts.notify.send_operator_notification(text) -> bool`
- Produces: `run_tick(*, now, paths, send, fetch_client=None, transport=None) -> dict` (Bericht: `accounts_fetched: bool`, `protocol_changes: int`, `sent: bool`); `main() -> int` (CLI für die Unit)

- [ ] **Step 1: Failing test**

```python
# tests/unit/ai_control/test_tick.py
import asyncio
from datetime import UTC, datetime

import httpx

from app.ai.control.config import ControlPaths
from app.ai.control.tick import run_tick

NOW = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)


def test_tick_holt_konten_und_meldet_einmal(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-m")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    paths = ControlPaths(telemetry=tmp_path / "t.jsonl", accounts=tmp_path / "a.json", runtime_dir=tmp_path / "rt",
                         protocol=tmp_path / "rt" / "p.json", alert_state=tmp_path / "rt" / "s.json",
                         env_file=tmp_path / ".env")
    paths.env_file.write_text("SOURCE_LLM_SPARFENSTER_MODE=enforce\n")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"code": 0, "data": {"available_balance": 2.0, "voucher_balance": 0, "cash_balance": 2.0}})))
    gesendet: list[str] = []

    async def send(text: str) -> bool:
        gesendet.append(text)
        return True

    bericht = asyncio.run(run_tick(now=NOW, paths=paths, send=send, fetch_client=client, transport=None))
    assert bericht["accounts_fetched"] is True and paths.accounts.exists()
    assert len(gesendet) == 1 and "Guthaben moonshot knapp" in gesendet[0]
    bericht2 = asyncio.run(run_tick(now=NOW, paths=paths, send=send, fetch_client=client, transport=None))
    assert bericht2["accounts_fetched"] is False and len(gesendet) == 1
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_tick.py -q`

- [ ] **Step 3: Implement**

```python
# app/ai/control/tick.py
"""Timer der KI-Kontrollstation (alle 10 min): Konten (stuendlich), Protokoll, Telegram-Hinweise.

    python -m app.ai.control.tick            # wie die Unit
    python -m app.ai.control.tick --dry-run  # druckt die Nachricht, sendet nichts
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.ai.control import accounts, alerts, protocol
from app.ai.control.config import AccountKeys, ControlPaths, ControlThresholds, LiteLLMModels
from app.ai.control.snapshot import build_snapshot


async def run_tick(
    *,
    now: datetime,
    paths: ControlPaths,
    send: Callable[[str], Awaitable[bool]],
    fetch_client: httpx.Client | None = None,
    transport: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.ai.runtime import inference_settings
    from app.core.settings import get_settings

    th = ControlThresholds()
    bericht: dict[str, Any] = {"accounts_fetched": False, "protocol_changes": 0, "sent": False}
    vorher, stand = accounts.read_accounts(paths.accounts)
    if stand is None or now - stand >= timedelta(minutes=th.accounts_every_minutes):
        client = fetch_client or httpx.Client()
        try:
            neu = accounts.fetch_accounts(AccountKeys(), client=client, now=now)
        finally:
            if fetch_client is None:
                client.close()
        accounts.write_accounts(paths.accounts, accounts.merge_with_previous(neu, vorher), now)
        bericht["accounts_fetched"] = True
    release = Path("/home/kai/current")
    extra = {"RELEASE": release.resolve().name[:8]} if release.exists() else None
    bericht["protocol_changes"] = len(protocol.record(paths, now=now, extra=extra))
    p = get_settings().providers
    snap = build_snapshot(
        now=now, paths=paths, inference=inference_settings(None), transport=transport, thresholds=th,
        models=LiteLLMModels(), providers_configured={"openai": bool(p.openai_api_key),
        "anthropic": bool(p.anthropic_api_key), "gemini": bool(p.gemini_api_key), "xai": bool(p.xai_api_key)},
    )
    text, zustand = alerts.plan(snap["attention"], alerts.load_state(paths.alert_state), now=now, thresholds=th)
    if text:
        try:
            bericht["sent"] = await send(text)
        except Exception:  # noqa: BLE001 -- der naechste Lauf versucht es erneut
            bericht["sent"] = False
        if not bericht["sent"]:
            return bericht  # Zustand NICHT speichern: sonst gaelte die Meldung als zugestellt
    alerts.save_state(paths.alert_state, zustand)
    return bericht


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    async def senden(text: str) -> bool:
        if args.dry_run:
            print(text)
            return True
        from app.alerts.notify import send_operator_notification

        return await send_operator_notification(text)

    async def ablauf() -> dict[str, Any]:
        from app.api.routers.ai_control import _transport

        return await run_tick(now=datetime.now(UTC), paths=ControlPaths(), send=senden, transport=await _transport())

    print(asyncio.run(ablauf()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```ini
# deploy/systemd/kai-ai-control.service
[Unit]
Description=KAI KI-Kontrollstation -- Guthaben, Schalter-Protokoll, Telegram-Hinweise (nur lesend gegenueber KAI)
After=kai-server.service
OnFailure=kai-unit-failure-notify@%n.service

[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/kai/ai_analyst_trading_bot
Environment=PYTHONIOENCODING=utf-8
# Liest Telemetrie/Artefakte, fragt stuendlich die Guthaben-APIs (DeepSeek, Kimi, optional OpenAI-Admin)
# und schreibt artifacts/ai_accounts.json + artifacts/runtime/ai_control_*.json. Kein Modellaufruf.
ExecStart=/home/kai/ai_analyst_trading_bot/.venv/bin/python -m app.ai.control.tick
TimeoutStartSec=120
Nice=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```ini
# deploy/systemd/kai-ai-control.timer
[Unit]
Description=Trigger KAI KI-Kontrollstation alle 10 Minuten

[Timer]
# Alle 10 min (Minute 2, 12, 22 ...): Proxy-Ausfall > 5 min faellt so binnen 15 min auf; die
# Guthaben fragt der Tick selbst nur alle 55 min ab (KAI_AI_CONTROL_ACCOUNTS_EVERY_MINUTES).
OnCalendar=*-*-* *:02/10:00
Persistent=true
AccuracySec=30s
Unit=kai-ai-control.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/ tests/unit/test_systemd_install_coverage_ratchet.py tests/unit/test_systemd_failure_visibility_ratchet.py tests/unit/test_oneshot_units_do_not_bind_to_server.py tests/unit/test_health_check_host.py -q`. Der Installer leitet seine Liste aus `deploy/systemd/` ab, neue Units sind damit abgedeckt. `OnFailure=` ist gesetzt, `After=` ist keine Bindung. `enabled_set.txt` enthält den Timer.
- [ ] **Step 5: Commit** — `git commit -m "feat(ai-control): Timer kai-ai-control (Konten, Protokoll, Telegram) + Units"`

---

### Task 11: KI-Zeilen im Operator-Digest

**Files:**
- Create: `app/ai/control/digest.py`
- Modify: `scripts/digest_ops_block.py` — `collect_ops_status`: Teil `"ai_control": lambda: collect_ai_control(jetzt)`; `format_ops_lines`: nach `_cost_line(...)` die KI-Zeilen einfügen
- Test: `tests/unit/ai_control/test_digest.py`; bestehend `tests/unit/test_digest_ops_block.py` muss unverändert grün bleiben (`backup_line, cost_line, *_`)

**Interfaces:**
- Consumes: `build_snapshot` (Task 7), `history.daily` (Task 4)
- Produces: `collect(now, paths=ControlPaths()) -> dict`; `format_lines(block: dict) -> list[str]` (zwei Zeilen: „🧠 *KI gestern:*“, „🏦 *KI-Konten:*“)

- [ ] **Step 1: Failing test**

```python
# tests/unit/ai_control/test_digest.py
from app.ai.control.digest import format_lines


def test_zwei_zeilen() -> None:
    block = {
        "yesterday": {"day": "2026-10-01", "cost_by_provider": {"openai": 1.24}, "calls": 145,
                      "input_tokens": 312000, "output_tokens": 46000,
                      "budget_exhausted_at": "2026-10-01T13:55:00+00:00"},
        "accounts": [{"provider": "deepseek", "balance": 21.61, "runway_days": None, "status": "ok"},
                     {"provider": "moonshot", "balance": 19.3, "runway_days": 9.0, "status": "ok"},
                     {"provider": "openai", "balance": None, "runway_days": None, "status": "kein_api"}],
        "open_hints": 2,
    }
    gestern, konten = format_lines(block)
    assert gestern == ("🧠 *KI gestern:* 1.24 $ (openai 1.24) · 145 Aufrufe · Token 312k/46k · "
                       "Budgetende 13:55 UTC")
    assert konten == "🏦 *KI-Konten:* deepseek 21.61 $ · moonshot 19.30 $ (~9 T) · offen: 2 Hinweise"


def test_fehler_wird_eine_zeile() -> None:
    assert format_lines({"error": "OSError"}) == ["🧠 *KI-Kontrolle:* nicht lesbar (OSError)"]
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/ai_control/test_digest.py -q`

- [ ] **Step 3: Implement**

```python
# app/ai/control/digest.py
"""Zwei KI-Zeilen fuer den Operator-Digest -- eine Nachricht statt Silos (Spec §6.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai.control.config import ControlPaths


def collect(now: datetime, paths: ControlPaths | None = None) -> dict[str, Any]:
    from app.ai.control import history
    from app.ai.control.accounts import read_accounts
    from app.ai.spend import load_rows

    pfade = paths or ControlPaths()
    tage = history.daily(load_rows(pfade.telemetry), now=now, days=2)
    gestern = (now.astimezone(UTC).date() - timedelta(days=1)).isoformat()
    konten, _ = read_accounts(pfade.accounts)
    from app.ai.control.alerts import load_state

    offen = len((load_state(pfade.alert_state).get("open") or {}))
    return {"yesterday": next((t.__dict__ for t in tage if t.day == gestern), None),
            "accounts": konten, "open_hints": offen}


def _k(n: int) -> str:
    return f"{round(n / 1000)}k" if n >= 1000 else str(n)


def format_lines(block: dict[str, Any]) -> list[str]:
    if "error" in block:
        return [f"🧠 *KI-Kontrolle:* nicht lesbar ({block['error']})"]
    g = block.get("yesterday")
    if g:
        kosten = g["cost_by_provider"]
        summe = sum(kosten.values())
        teile = " · ".join(f"{p} {v:.2f}" for p, v in sorted(kosten.items(), key=lambda x: -x[1]))
        ende = g.get("budget_exhausted_at")
        ende_text = f"Budgetende {datetime.fromisoformat(ende):%H:%M} UTC" if ende else "Budget reichte"
        erste = (f"🧠 *KI gestern:* {summe:.2f} $ ({teile or '–'}) · {g['calls']} Aufrufe · "
                 f"Token {_k(g['input_tokens'])}/{_k(g['output_tokens'])} · {ende_text}")
    else:
        erste = "🧠 *KI gestern:* keine Daten"
    konten = []
    for k in block.get("accounts") or []:
        if k.get("balance") is None:
            continue
        reichweite = f" (~{k['runway_days']:.0f} T)" if k.get("runway_days") is not None else ""
        konten.append(f"{k['provider']} {float(k['balance']):.2f} ${reichweite}")
    zweite = ("🏦 *KI-Konten:* " + (" · ".join(konten) or "keine Abfrage")
              + f" · offen: {block.get('open_hints', 0)} Hinweise")
    return [erste, zweite]


__all__ = ["collect", "format_lines"]
```

Note: Die Reichweite in `read_accounts` gibt es nur im Snapshot. Im Digest fehlt sie, solange `collect` sie nicht berechnet. Deshalb in `collect` vor dem Rückgeben ergänzen:

```python
    from app.ai.control.workloads import provider_activity

    aktiv = provider_activity(load_rows(pfade.telemetry), now=now)
    for k in konten:
        a = aktiv.get(str(k.get("provider")))
        rate = a.cost_7d_usd / 7 if a and a.cost_7d_usd > 0 else None
        k["runway_days"] = round(float(k["balance"]) / rate, 1) if (rate and k.get("balance") is not None) else None
```

In `scripts/digest_ops_block.py`:

```python
def collect_ai_control(now: datetime) -> dict[str, Any]:
    from app.ai.control.digest import collect

    return collect(now)
```

`collect_ops_status`: Eintrag `"ai_control": lambda: collect_ai_control(jetzt),` nach `"ai_cost"`.

`format_ops_lines`: die Rückgabe wird zu

```python
    from app.ai.control.digest import format_lines

    return [
        f"🛟 *Backup:* {backup}",
        _cost_line(status.get("ai_cost", {"error": "fehlt"})),
        *format_lines(status.get("ai_control", {"error": "fehlt"})),
        _ln_line(status.get("ln", {"error": "fehlt"})),
    ]
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/ai_control/test_digest.py tests/unit/test_digest_ops_block.py -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(digest): KI-Zeilen (gestern, Konten, offene Hinweise) im Operator-Digest"`

---

### Task 12: Frontend-Bibliothek `aiControl.ts`

**Files:**
- Create: `web/src/lib/aiControl.ts`, `web/src/lib/aiControl.test.ts`

**Interfaces:**
- Consumes: `apiGet<T>(path, init)` aus `web/src/lib/api.ts`
- Produces: Typen `AiControlState`, `AiControlResponse`, `AiControlHistory`; `fetchAiControl(signal?)`, `fetchAiControlHistory(signal?)`; `STATE_META: Record<AiControlState, {label, symbol, tone}>`; `formatUsd(n)`, `formatTokens(n)`, `runwayLabel(days)`

- [ ] **Step 1: Failing test**

```ts
// web/src/lib/aiControl.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { _resetInflightForTests } from "./api";
import { STATE_META, fetchAiControl, formatTokens, formatUsd, runwayLabel } from "./aiControl";

afterEach(() => {
  vi.restoreAllMocks();
  _resetInflightForTests();
});

describe("aiControl", () => {
  it("kennt jeden Zustand mit Symbol", () => {
    for (const s of ["aktiv", "bereit", "pausiert", "gestoert", "ausser_kraft", "deaktiviert"] as const) {
      expect(STATE_META[s].symbol.length).toBeGreaterThan(0);
    }
    expect(STATE_META.gestoert.tone).toBe("neg");
  });

  it("formatiert ehrlich", () => {
    expect(formatUsd(null)).toBe("–");
    expect(formatUsd(1.2345)).toBe("1,23 $");
    expect(formatTokens(312000)).toBe("312k");
    expect(runwayLabel(null)).toBe("∞");
    expect(runwayLabel(8.6)).toBe("~9 Tage");
  });

  it("holt den Vertrag", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ schema: "ai-control/v1", attention: [] }), { status: 200 }),
    );
    const data = await fetchAiControl();
    expect(data.schema).toBe("ai-control/v1");
  });
});
```

- [ ] **Step 2: Run, expect FAIL** — `cd web && npx vitest run src/lib/aiControl.test.ts`

- [ ] **Step 3: Implement**

```ts
// web/src/lib/aiControl.ts
// @data-source: /dashboard/api/ai/control (ai-control/v1), /dashboard/api/ai/control/history
//
// KI-Kontrollstation (Spec docs/superpowers/specs/2026-10-02-ki-kontrollstation-design.md).
// Typen + Anzeige-Helfer. Bewertet wird im Backend; hier wird nur dargestellt.

import { apiGet } from "./api";

export type AiControlState = "aktiv" | "bereit" | "pausiert" | "gestoert" | "ausser_kraft" | "deaktiviert";
type Tone = "pos" | "info" | "warn" | "neg" | "ai" | "muted";

export const STATE_META: Record<AiControlState, { label: string; symbol: string; tone: Tone }> = {
  aktiv: { label: "AKTIV", symbol: "●", tone: "pos" },
  bereit: { label: "BEREIT", symbol: "○", tone: "info" },
  pausiert: { label: "PAUSIERT", symbol: "◐", tone: "warn" },
  gestoert: { label: "GESTÖRT", symbol: "✖", tone: "neg" },
  ausser_kraft: { label: "AUSSER KRAFT", symbol: "⊘", tone: "ai" },
  deaktiviert: { label: "DEAKTIVIERT", symbol: "─", tone: "muted" },
};

export type Verdict = { state: AiControlState | null; reason: string | null; since: string | null };

export type Attention = {
  key: string;
  severity: "warn" | "crit";
  title: string;
  detail: string;
  since: string;
  min_age_min: number;
  action: { kind: "details" | "topup"; url?: string | null };
};

export type WorkloadPart = {
  service: string;
  transport: string;
  model: string;
  calls: number;
  cost_usd: number;
  last_call: string | null;
  top_sources: [string, number][];
};

export type Workload = Verdict & {
  purpose: string;
  title: string;
  route: string;
  mode: string;
  calls_today: number;
  tokens_in_today: number;
  tokens_out_today: number;
  approx_kb_today: number;
  cost_today_usd: number;
  unknown_cost_calls_today: number;
  failure_rate_24h: number | null;
  fallbacks_today: number;
  sparfenster: string | null;
  parts: WorkloadPart[];
};

export type Account = {
  provider: string;
  status: "ok" | "fehler" | "kein_schluessel" | "kein_api";
  balance: number | null;
  currency: string | null;
  error: string | null;
  fetched_at: string;
  topup_url: string;
  runway_days: number | null;
  stale: boolean;
  detail: Record<string, unknown>;
};

export type AiControlResponse = {
  schema: "ai-control/v1";
  generated_at: string;
  summary: {
    today_usd: number;
    today_limit_usd: number | null;
    month_usd: number;
    month_limit_usd: number | null;
    projected_month_usd: number | null;
    budget_state: string;
    budget_exhausted_at: string | null;
    budget_end_estimate: string | null;
    calls_today: number;
    tokens_in_today: number;
    tokens_out_today: number;
    state_counts: Partial<Record<AiControlState, number>>;
  };
  attention: Attention[];
  connections: {
    proxy: Verdict & { version: string | null; lock_matches: boolean | null; status_code?: number | null };
    aliases: (Verdict & { alias: string; route: string; mode: string; upstream_model: string | null })[];
    providers: (Verdict & { name: string; kind: string; calls_24h: number; failures_24h: number })[];
  };
  workloads: Workload[];
  accounts: Account[];
  accounts_written_at: string | null;
  protocol: { ts: string; actor: string; kind: string; key: string; old: string | null; new: string | null }[];
  null_reasons: Record<string, string>;
};

export type AiControlHistory = {
  schema: "ai-control-history/v1";
  days: {
    day: string;
    cost_by_provider: Record<string, number>;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    budget_exhausted_at: string | null;
  }[];
};

export function fetchAiControl(signal?: AbortSignal): Promise<AiControlResponse> {
  return apiGet<AiControlResponse>("/dashboard/api/ai/control", { signal });
}

export function fetchAiControlHistory(signal?: AbortSignal): Promise<AiControlHistory> {
  return apiGet<AiControlHistory>("/dashboard/api/ai/control/history?days=14", { signal });
}

const USD = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatUsd(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : `${USD.format(n)} $`;
}

export function formatTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

export function runwayLabel(days: number | null): string {
  return days === null ? "∞" : `~${Math.round(days)} Tage`;
}
```

- [ ] **Step 4: Run, expect PASS** — `cd web && npx vitest run src/lib/aiControl.test.ts && npx tsc -b`
- [ ] **Step 5: Commit** — `git commit -m "feat(web): aiControl-Bibliothek (Vertrag, Zustands-Metadaten, Formatierung)"`

---

### Task 13: Seite „KI-Kontrolle“ + Menüpunkt

**Files:**
- Create: `web/src/pages/AIControl.tsx`, `web/src/components/aicontrol/StateChip.tsx`, `web/src/components/aicontrol/Sections.tsx`
- Modify: `web/src/state/Router.tsx` (`"ki"` in `ROUTES` nach `"ai"`), `web/src/layout/AppShell.tsx` (lazy import + `case "ki"`), `web/src/layout/Sidebar.tsx` (Eintrag in `CONTROL`: `{ id: "ki", labelKey: "nav.ki", icon: <Cpu size={16} /> }`, `Cpu` aus `lucide-react`), `web/src/i18n/strings.ts` (im Objekt `de.nav`: `ki: "KI-Kontrolle",` nach `ai:`; im Objekt `en.nav`: `ki: "AI Control",`)
- Test: `web/src/components/aicontrol/StateChip.test.tsx`

**Interfaces:**
- Consumes: Task 12; `useApi(fetcher, intervalMs)` aus `@/lib/useApi`; `PageHeader` (`tone="ai"`), `Card`, `CardHeader`, `Badge`, `SectionLabel` aus `@/components/ui/Primitives`; `PanelLoading`, `PanelError` aus `@/components/ui/PanelState`; `PanelErrorBoundary` wie in `System.tsx`
- Produces: Seite unter `#ki`

- [ ] **Step 1: Failing test**

```tsx
// web/src/components/aicontrol/StateChip.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StateChip } from "./StateChip";

describe("StateChip", () => {
  it("zeigt Symbol, Label und Grund", () => {
    render(<StateChip state="gestoert" reason="Proxy nicht erreichbar" />);
    expect(screen.getByText("GESTÖRT")).toBeTruthy();
    expect(screen.getByTitle("Proxy nicht erreichbar")).toBeTruthy();
  });

  it("unbekannt statt leer", () => {
    render(<StateChip state={null} reason="KI-Transport-Status nicht lesbar" />);
    expect(screen.getByText("UNBEKANNT")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run, expect FAIL** — `cd web && npx vitest run src/components/aicontrol/StateChip.test.tsx`

- [ ] **Step 3: Implement**

```tsx
// web/src/components/aicontrol/StateChip.tsx
import { STATE_META, type AiControlState } from "@/lib/aiControl";
import { cn } from "@/lib/utils";

const TONE = {
  pos: "text-pos border-pos/40 glow-pos",
  info: "text-info border-info/40",
  warn: "text-warn border-warn/50",
  neg: "text-neg border-neg/60 glow-neg",
  ai: "text-ai border-ai/50",
  muted: "text-fg-subtle border-line",
} as const;

export function StateChip({ state, reason }: { state: AiControlState | null; reason: string | null }) {
  const meta = state ? STATE_META[state] : { label: "UNBEKANNT", symbol: "?", tone: "muted" as const };
  return (
    <span
      title={reason ?? meta.label}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 font-mono text-2xs tracking-wider",
        TONE[meta.tone],
      )}
    >
      <span aria-hidden>{meta.symbol}</span>
      <span>{meta.label}</span>
      {reason ? <span className="sr-only"> – {reason}</span> : null}
    </span>
  );
}
```

```tsx
// web/src/components/aicontrol/Sections.tsx
import { AlertTriangle, ExternalLink } from "lucide-react";

import { Card, CardHeader } from "@/components/ui/Primitives";
import {
  formatTokens,
  formatUsd,
  runwayLabel,
  type AiControlHistory,
  type AiControlResponse,
} from "@/lib/aiControl";
import { cn } from "@/lib/utils";

import { StateChip } from "./StateChip";

type R = AiControlResponse;

export function HeaderStrip({ s }: { s: R["summary"] }) {
  const quote = s.today_limit_usd ? Math.min(1, s.today_usd / s.today_limit_usd) : null;
  const ende = s.budget_exhausted_at
    ? `leer seit ${s.budget_exhausted_at.slice(11, 16)} UTC`
    : s.budget_end_estimate === "tagesende"
      ? "reicht bis Tagesende"
      : s.budget_end_estimate
        ? `reicht bis ~${s.budget_end_estimate.slice(11, 16)} UTC`
        : "–";
  return (
    <Card padded className="glow-border-ai">
      <div className="grid grid-cols-2 gap-3 font-mono text-xs md:grid-cols-4">
        <div>
          <div className="text-2xs uppercase tracking-wider text-fg-muted">Heute</div>
          <div className="text-lg text-fg">
            {formatUsd(s.today_usd)} <span className="text-fg-muted">/ {formatUsd(s.today_limit_usd)}</span>
          </div>
          {quote !== null ? (
            <div className="mt-1 h-1.5 w-full rounded-sm bg-bg-3">
              <div
                className={cn("h-1.5 rounded-sm", quote >= 1 ? "bg-neg" : quote >= 0.8 ? "bg-warn" : "bg-pos")}
                style={{ width: `${quote * 100}%` }}
              />
            </div>
          ) : null}
        </div>
        <div>
          <div className="text-2xs uppercase tracking-wider text-fg-muted">Monat</div>
          <div className="text-lg text-fg">
            {formatUsd(s.month_usd)} <span className="text-fg-muted">/ {formatUsd(s.month_limit_usd)}</span>
          </div>
        </div>
        <div>
          <div className="text-2xs uppercase tracking-wider text-fg-muted">Prognose</div>
          <div className="text-lg text-fg">{formatUsd(s.projected_month_usd)}</div>
        </div>
        <div>
          <div className="text-2xs uppercase tracking-wider text-fg-muted">Budget</div>
          <div className={cn("text-lg", s.budget_exhausted_at ? "text-warn" : "text-fg")}>{ende}</div>
        </div>
      </div>
    </Card>
  );
}

export function AttentionList({ items }: { items: R["attention"] }) {
  return (
    <Card padded>
      <CardHeader title="Handlungsbedarf" />
      {items.length === 0 ? (
        <div className="font-mono text-xs text-pos">● nichts offen</div>
      ) : (
        <ul className="space-y-1.5">
          {items.map((h) => (
            <li key={h.key} className="flex items-start gap-2 font-mono text-xs">
              <AlertTriangle size={13} className={h.severity === "crit" ? "text-neg" : "text-warn"} aria-hidden />
              <span className="text-fg">{h.title}</span>
              <span className="text-fg-muted">{h.detail}</span>
              {h.action.kind === "topup" && h.action.url ? (
                <a href={h.action.url} target="_blank" rel="noreferrer" className="ml-auto text-info hover:underline">
                  Aufladen <ExternalLink size={11} className="inline" />
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function Connections({ c }: { c: R["connections"] }) {
  return (
    <Card padded>
      <CardHeader title="Verbindungen" />
      <div className="space-y-2 font-mono text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-36 text-fg-muted">LiteLLM-Proxy</span>
          <StateChip state={c.proxy.state} reason={c.proxy.reason} />
          <span className="text-fg-muted">v{c.proxy.version ?? "?"}</span>
          <span className={c.proxy.lock_matches ? "text-pos" : "text-warn"}>
            {c.proxy.lock_matches === null ? "Lock ?" : c.proxy.lock_matches ? "Baum = Lock ✓" : "Baum ≠ Lock"}
          </span>
        </div>
        {c.aliases.map((a) => (
          <div key={a.alias} className="flex flex-wrap items-center gap-2 pl-4">
            <span className="w-32 text-fg">{a.alias}</span>
            <span className="text-fg-muted">→ {a.upstream_model ?? "kein Modell"}</span>
            <StateChip state={a.state} reason={a.reason} />
            <span className="text-fg-subtle">{a.mode}</span>
          </div>
        ))}
        <div className="flex flex-wrap gap-3 pt-1">
          {c.providers.map((p) => (
            <span key={p.name} className="inline-flex items-center gap-1.5">
              <span className="text-fg">{p.name}</span>
              <StateChip state={p.state} reason={p.reason} />
            </span>
          ))}
        </div>
      </div>
    </Card>
  );
}

export function Workloads({ items }: { items: R["workloads"] }) {
  return (
    <Card padded>
      <CardHeader title="Wer macht was (heute)" />
      <div className="overflow-x-auto">
        <table className="w-full font-mono text-xs">
          <thead className="text-2xs uppercase tracking-wider text-fg-muted">
            <tr>
              <th className="py-1 text-left">Aufgabe</th>
              <th className="text-left">Zustand</th>
              <th className="text-left">Weg / Modell</th>
              <th className="text-right">Aufr.</th>
              <th className="text-right">Token ein/aus</th>
              <th className="text-right">≈ KB</th>
              <th className="text-right">Kosten</th>
              <th className="text-right">Fehler 24 h</th>
            </tr>
          </thead>
          <tbody>
            {items.map((w) => (
              <tr key={w.purpose} className="border-t border-line/50 align-top">
                <td className="py-1.5 text-fg">
                  {w.title}
                  <div className="text-2xs text-fg-subtle">
                    {w.route} · {w.mode}
                    {w.sparfenster && w.sparfenster !== "off" ? ` · Sparfenster ${w.sparfenster}` : ""}
                  </div>
                </td>
                <td>
                  <StateChip state={w.state} reason={w.reason} />
                </td>
                <td className="text-fg-muted">
                  {w.parts.length === 0
                    ? "–"
                    : w.parts.map((p) => (
                        <div key={`${p.service}-${p.transport}-${p.model}`}>
                          {p.transport} · {p.model} <span className="text-fg-subtle">({p.service})</span>
                        </div>
                      ))}
                </td>
                <td className="text-right">{w.calls_today}</td>
                <td className="text-right">
                  {formatTokens(w.tokens_in_today)}/{formatTokens(w.tokens_out_today)}
                </td>
                <td className="text-right">{w.approx_kb_today.toFixed(0)}</td>
                <td className="text-right">
                  {formatUsd(w.cost_today_usd)}
                  {w.unknown_cost_calls_today ? (
                    <div className="text-2xs text-warn">+{w.unknown_cost_calls_today} ohne Preis</div>
                  ) : null}
                </td>
                <td className="text-right">
                  {w.failure_rate_24h === null ? "–" : `${Math.round(w.failure_rate_24h * 100)} %`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function Accounts({ items, writtenAt }: { items: R["accounts"]; writtenAt: string | null }) {
  return (
    <Card padded>
      <CardHeader title="Konten & Guthaben" subtitle={writtenAt ? `Abfrage ${writtenAt.slice(11, 16)} UTC` : "keine Abfrage"} />
      <div className="grid grid-cols-1 gap-2 font-mono text-xs md:grid-cols-3">
        {items.map((k) => (
          <div key={k.provider} className={cn("rounded-sm border border-line p-2", k.stale && "opacity-70")}>
            <div className="flex items-center justify-between">
              <span className="text-fg">{k.provider}</span>
              <a href={k.topup_url} target="_blank" rel="noreferrer" className="text-info hover:underline">
                Konsole <ExternalLink size={11} className="inline" />
              </a>
            </div>
            <div className="text-lg text-fg">
              {k.status === "kein_api" ? "nur KAI-Messung" : formatUsd(k.balance)}
            </div>
            <div className="text-2xs text-fg-muted">
              {k.status === "fehler" ? `Abfrage fehlgeschlagen (${k.error})` : `Reichweite ${runwayLabel(k.runway_days)}`}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function Protocol({ items }: { items: R["protocol"] }) {
  return (
    <Card padded>
      <CardHeader title="Protokoll" />
      {items.length === 0 ? (
        <div className="font-mono text-xs text-fg-muted">noch keine Schalteränderung erfasst</div>
      ) : (
        <ul className="space-y-1 font-mono text-xs">
          {items.map((p) => (
            <li key={`${p.ts}-${p.key}`}>
              <span className="text-fg-muted">{p.ts.slice(0, 16).replace("T", " ")}Z</span>{" "}
              <span className="text-fg">{p.key}</span>{" "}
              <span className="text-fg-subtle">{p.old ?? "–"}</span> → <span className="text-ai">{p.new ?? "–"}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function History({ h }: { h: AiControlHistory | null }) {
  if (!h) return null;
  const max = Math.max(0.01, ...h.days.map((d) => Object.values(d.cost_by_provider).reduce((a, b) => a + b, 0)));
  return (
    <Card padded>
      <CardHeader title="Verlauf 14 Tage" subtitle="Kosten je Tag · darunter Budgetende (UTC)" />
      <div className="flex h-28 items-end gap-1">
        {h.days.map((d) => {
          const summe = Object.values(d.cost_by_provider).reduce((a, b) => a + b, 0);
          return (
            <div key={d.day} className="flex flex-1 flex-col items-center gap-1" title={`${d.day}: ${formatUsd(summe)}`}>
              <div className="w-full rounded-sm bg-ai/70" style={{ height: `${(summe / max) * 100}%` }} />
              <div className="font-mono text-[9px] text-fg-subtle">{d.day.slice(8)}</div>
              <div className="font-mono text-[9px] text-warn">{d.budget_exhausted_at?.slice(11, 16) ?? ""}</div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
```

```tsx
// web/src/pages/AIControl.tsx
// @data-source: /dashboard/api/ai/control, /dashboard/api/ai/control/history
//
// KI-Kontrollstation, Stufe 1 (nur lesend): Laeuft alles? Wer macht was, was verbraucht es?
// Wo muss ich eingreifen? Eingriffe folgen in Stufe 2.

import { PageHeader } from "@/layout/PageHeader";
import { PanelError, PanelLoading } from "@/components/ui/PanelState";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { STATE_META, fetchAiControl, fetchAiControlHistory, type AiControlState } from "@/lib/aiControl";
import { useApi } from "@/lib/useApi";

import {
  Accounts,
  AttentionList,
  Connections,
  HeaderStrip,
  History,
  Protocol,
  Workloads,
} from "@/components/aicontrol/Sections";

export function AIControlPage() {
  const ctl = useApi(fetchAiControl, 30_000);
  const hist = useApi(fetchAiControlHistory, 300_000);
  const d = ctl.state === "ready" ? ctl.data : null;
  const zaehler = d
    ? (Object.entries(d.summary.state_counts) as [AiControlState, number][])
        .map(([s, n]) => `${STATE_META[s].symbol} ${n} ${STATE_META[s].label.toLowerCase()}`)
        .join(" · ")
    : "";

  return (
    <div className="mx-auto max-w-[1680px] space-y-4 p-4 xl:p-5">
      <PageHeader
        title="KI-Kontrolle"
        sub="Läuft alles? Wer macht was, und was verbraucht es? Wo muss ich eingreifen?"
        tone="ai"
        right={<span className="font-mono text-xs text-fg-muted">{zaehler}</span>}
      />
      {ctl.state === "loading" ? <PanelLoading /> : null}
      {ctl.state === "error" ? (
        <PanelError
          title="KI-Kontrolle nicht verfügbar"
          error={{ kind: ctl.error.kind, message: ctl.error.message, code: ctl.error.status ? `HTTP ${ctl.error.status}` : null }}
          onRetry={ctl.reload}
        />
      ) : null}
      {d ? (
        <PanelErrorBoundary name="KI-Kontrolle">
          <HeaderStrip s={d.summary} />
          <AttentionList items={d.attention} />
          <Connections c={d.connections} />
          <Workloads items={d.workloads} />
          <Accounts items={d.accounts} writtenAt={d.accounts_written_at} />
          <Protocol items={d.protocol} />
          <History h={hist.state === "ready" ? hist.data : null} />
        </PanelErrorBoundary>
      ) : null}
    </div>
  );
}
```

Geprüfte Schnittstellen (02.10., Mainline 9a4cc5fd): `useApi(fetcher, refreshMs)` liefert `{state, data, error: {kind, message, status}, reload}`; `PanelError` erwartet `PanelErrorInfo {kind, message, code?}`; `CardHeader` hat `title, subtitle, right`; `PanelErrorBoundary` liegt in `@/components/PanelErrorBoundary`.

Menüpunkt:
- `Router.tsx`: `"ki",` nach `"ai",`.
- `AppShell.tsx`: `const AIControlPage = lazy(() => import("@/pages/AIControl").then((m) => ({ default: m.AIControlPage })));` and `case "ki": return <AIControlPage />;`.
- `Sidebar.tsx`: put the `CONTROL` entry first.

- [ ] **Step 4: Run, expect PASS** — `cd web && npx vitest run && npx tsc -b && npm run build`
- [ ] **Step 5: Commit** — `git commit -m "feat(web): Seite KI-Kontrolle (Zustaende, Wer-macht-was, Konten, Protokoll, Verlauf)"`

---

### Task 14: Gate, PR, Release, Unit-Aktivierung, Abnahme

**Files:**
- Create (außerhalb Repo): `C:\Users\sasch\KAI-mirror\reminders\ki_kontrolle_timer_aktivieren.ps1`, `C:\Users\sasch\KAI-mirror\scripts\release\kai_release_<sha>.ps1` (Vorlage `kai_release_01169947.ps1`)

- [ ] **Step 1: Volles Gate** — `KAI_PY=python bash ~/KAI-mirror/scripts/kai_preflight.sh`, dazu die volle Suite wie CI: `python -m pytest tests/ -n auto --dist loadfile -q`, `cd web && npx vitest run && npm run build`. Alles grün, sonst kein Push.
- [ ] **Step 2: PR** — `kai_ship.sh` mit Body aus den 5 Pflichtsektionen; Auto-Merge armieren (`gh pr merge <n> --squash --delete-branch --auto`); CI im Hintergrund beobachten.
- [ ] **Step 3: Release-Skript** — nach dem Merge aus `kai_release_01169947.ps1` erzeugen: `$Target` = Merge-SHA, `$Expect` = laufendes Release laut `readlink /home/ubuntu/current`. Schritt 8 ersetzen durch:

```powershell
Step '8/8 KI-Kontrollstation im Release (nur lesen)'
$kc = ssh $Pi 'cd /home/ubuntu/ai_analyst_trading_bot && k=$(grep -E ^APP_API_KEY= .env | cut -d= -f2- | tr -d \"\x27); curl -s -m 20 -o /tmp/kai_kc.json -w %{http_code} -H "Authorization: Bearer $k" http://127.0.0.1:8000/dashboard/api/ai/control; echo; python3 -c "import json;d=json.load(open(\"/tmp/kai_kc.json\"));print(d[\"schema\"], len(d[\"workloads\"]), len(d[\"attention\"]))"; rm -f /tmp/kai_kc.json'
$kc | ForEach-Object { Write-Host "  $_" }
if (-not (($kc -join "`n") -match '200')) { Fail 'KI-Kontrolle-Endpunkt nicht 200 -- Ausgabe an Claude' }
```

(Bash-Quoting in einem geprüften `.ps1` mit `[System.Management.Automation.Language.Parser]::ParseFile` gegenprüfen, bevor es zum Operator geht.)
- [ ] **Step 4: Unit-Aktivierung (Operator, sudo)** — `ki_kontrolle_timer_aktivieren.ps1` nach dem Muster `route_report_timer_aktivieren.ps1`:

```powershell
ssh -t ubuntu@192.168.178.23 'cd /home/ubuntu/ai_analyst_trading_bot && sudo bash scripts/pi_apply_systemd_units.sh && sudo systemctl enable --now kai-ai-control.timer && systemctl list-timers kai-ai-control.timer --no-pager && sudo systemctl start kai-ai-control.service && journalctl -u kai-ai-control.service -n 5 --no-pager'
```

- [ ] **Step 5: Abnahme (lesend, Spec §9):**
  1. `GET /dashboard/api/ai/control` liefert alle 6 Aufgaben, 6 Routen-Aliase und 6 Anbieter mit Zustand + Grund.
  2. `calls_today` je Aufgabe summiert = `spend.current_spend()[0].calls` (±0).
  3. DeepSeek- und Kimi-Guthaben in `artifacts/ai_accounts.json` = Konsole des Anbieters (Operator vergleicht).
  4. Der nächste Operator-Digest enthält „🧠 *KI gestern:*“ und „🏦 *KI-Konten:*“.
  5. Testhinweis: einmalig `KAI_AI_CONTROL_BALANCE_MIN_USD=1000` per Operator-Skript setzen. Genau eine Telegram-Nachricht „Guthaben … knapp“ muss ankommen; danach die Zeile zurücksetzen und die „behoben“-Nachricht beim nächsten Tick prüfen.
  6. Im Dashboard sind `#ki` und der Menüpunkt „KI-Kontrolle“ sichtbar. Die Bundle-Bytes enthalten `KI-Kontrolle` (`grep` im ausgelieferten `index-*.js` bzw. dem lazy Chunk).
- [ ] **Step 6: Memory** — Live-Stand, SHA, Abnahme-Ergebnisse in `project_ki_kontrollstation_20261002.md`.
