# AGENTS.md — KAI Platform

> **Verbindliches Betriebsdokument für alle Coding-Agenten.**
> Claude Code · OpenAI Codex · Google Antigravity
> Dieses Dokument lesen, bevor eine einzige Zeile Code angefasst wird.

---

## 1. Platform Identity

**Name**: KAI (AI Analyst Trading Bot)
**Repo**: https://github.com/CP-Srinivasa/KAI
**Mission**: Agent-capable development & operations platform for AI-driven market intelligence.
**Motto**: Simple but Powerful

Dies ist **keine normale App** — es ist eine spec-driven, modulare, maschinenlesbare Plattform,
die von Menschen und AI-Agenten gemeinsam entwickelt und betrieben wird.

---

## 2. Provider-Entscheidungen (fest)

| # | Bereich | Entscheidung |
|---|---|---|
| A | Repo | GitHub |
| B | Sprache | Python 3.12+ |
| C | API Framework | FastAPI |
| D | Datenbank | PostgreSQL (TimescaleDB optional) |
| E | AI-Provider | OpenAI/ChatGPT (primär), Anthropic (optional), Google Antigravity (optional) |

---

## 3. Rollen-Zuordnung

| Agent | Rolle | Zuständig für |
|---|---|---|
| **OpenAI Codex** | Implementierer | Neue Features nach Spec, Unit Tests, Refactoring (1 Modul), CI-Fixes, Lint |
| **Claude Code** | Architekt | Neue Module, Interface-Änderungen, Multi-File, Spec → Code, AGENTS.md pflegen |
| **Google Antigravity** | Orchestrator | Workflows, MCP/Skills, Build/Deploy, multi-Agent-Koordination |

**Vollständiges Rollenmodell → [AGENT_ROLES.md](./AGENT_ROLES.md)**

---

## 4. Task-Typen und Zuständigkeit

| Task-Typ | Zuständiger Agent |
|---|---|
| Feature implementieren (Spec vorhanden, 1 Modul) | Codex |
| Unit Test schreiben oder reparieren | Codex |
| Refactoring innerhalb eines Moduls (kein Interface-Bruch) | Codex |
| CI-Pipeline-Fehler beheben | Codex |
| Lint / Typ-Korrekturen | Codex |
| Neues Modul einführen | Claude Code |
| Interface oder Domain-Modell ändern | Claude Code |
| Multi-File-Änderung (2+ Module) | Claude Code |
| Spec schreiben oder AGENTS.md aktualisieren | Claude Code |
| Agentischen Workflow definieren | Antigravity |
| MCP-Tool oder Skill definieren | Antigravity |
| Build / Deploy / CI-Pipeline aufbauen | Antigravity |
| Unklare Anforderung | → Operator entscheidet |

---

## 5. Pflicht: Kein Agent ändert Architektur ohne Spec

> **RULE-ZERO**: Kein Agent darf ein neues Modul, Interface, Domain-Modell oder
> eine Provider-Abstraktion einführen oder ändern, ohne dass eine Spec existiert.

Eine **Spec** ist eine der folgenden:
- Ein Abschnitt in `PROJECT_SPEC.md`
- Ein `AGENTS.md` im betreffenden Modul
- Eine explizite schriftliche Anweisung des Operators in der aktuellen Session

**Was erlaubt ist ohne Spec:**
- Implementierung innerhalb bestehender Interfaces
- Tests schreiben
- Lint/CI-Fixes
- Dokumentation verbessern

**Was NICHT erlaubt ist ohne Spec:**
- Neue `app/<modul>/` Verzeichnisse anlegen
- Bestehende Pydantic-Modelle umbenennen oder Felder entfernen
- Provider-Abstraktionen ändern
- `app/core/` anfassen
- `AGENTS.md` oder `AGENT_ROLES.md` ändern

---

## 6. Pflicht-Task-Format

Jede Aufgabe, die an einen Agenten übergeben wird, muss folgendes enthalten:

```
## Task: <kurzer Titel>

**Agent**: [Codex | Claude Code | Antigravity]
**Phase**: [P1 | P2 | P3 | ...]
**Modul**: app/<modul>/
**Typ**: [feature | test | refactor | fix | spec | deploy]

### Beschreibung
<Was soll gebaut/geändert werden — in 2–5 Sätzen>

### Spec-Referenz
<Abschnitt in PROJECT_SPEC.md oder AGENTS.md, der diese Aufgabe beschreibt>

### Akzeptanzkriterien
- [ ] <messbares Kriterium 1>
- [ ] <messbares Kriterium 2>

### Constraints
- <Was NICHT geändert werden darf>
- <Welche bestehenden Interfaces stabil bleiben müssen>
```

---

## 7. Pflicht-Output-Format (pro Agent)

### OpenAI Codex

```
## Codex Report
- Task: <Titel>
- Dateien erstellt: [Liste oder "keine"]
- Dateien geändert: [Liste]
- Annahmen: [Liste]
- TODOs: [Liste oder "keine"]
- Test-Befehl: pytest tests/unit/<datei>.py
- ruff check: ✅ / ❌ (Fehler angeben)
```

### Claude Code

```
## Claude Code Report
- Task: <Titel>
- Architekturentscheidung: <Was wurde wie entschieden und warum>
- Dateien erstellt: [Liste]
- Dateien geändert: [Liste]
- Interface-Änderungen: [Ja/Nein — wenn Ja: was genau]
- AGENTS.md aktualisiert: [Ja/Nein — welche Dateien]
- Annahmen: [Liste]
- TODOs: [Liste]
- Test-Befehl: pytest tests/unit/<datei>.py
```

### Google Antigravity

```
## Antigravity Report
- Task: <Titel>
- Workflow-Schritte: [geordnete Liste]
- Eingesetzte Agenten: [Codex | Claude Code | beide]
- MCP/Skills aktiviert: [Liste oder "keine"]
- Ergebnis: <Was wurde produziert>
- Validierung: <Wie wurde das Ergebnis geprüft>
- TODOs: [Liste]
```

---

## 8. Quality Gates (Pflicht)

Jede Aufgabe gilt als **nicht abgeschlossen**, solange einer dieser Gates nicht erfüllt ist:

| Gate | Prüfung |
|---|---|
| **Tests** | `pytest` läuft durch ohne Fehler |
| **Lint** | `ruff check .` ohne Fehler |
| **Typisierung** | Keine `Any`, kein nacktes `dict` in öffentlichen Interfaces |
| **Secrets** | Keine Credentials oder API-Keys im Code |
| **Spec-Konformität** | Implementierung entspricht der referenzierten Spec |
| **AGENTS.md** | Aktualisiert, wenn Interface oder Modul geändert |
| **Failure-Handling** | Fehlerszenarien explizit behandelt (kein `pass` in `except`) |

---

## 9. Modul-Map

```
app/
  core/          → settings, logging, domain types, enums, errors  [AGENTS.md ✅]
  ingestion/     → source adapters, resolvers, classifiers, RSS    [AGENTS.md ✅]
  normalization/ → content cleaning, canonical alignment
  enrichment/    → deduplication, entity helpers                   [AGENTS.md ✅]
  analysis/      → base interfaces für LLM + rule-based providers  [AGENTS.md ✅]
  api/           → FastAPI routers                                  [AGENTS.md ✅]
  cli/           → Typer commands                                   [AGENTS.md ✅]
  storage/       → DB models, session (PostgreSQL/SQLAlchemy)
monitor/         → user-editable source lists, keywords, watchlists
tests/unit/      → pytest unit tests
docs/            → architecture and module documentation
```

---

## 10. Key Domain Models

| Model | Datei | Zweck |
|---|---|---|
| `CanonicalDocument` | `app/core/domain/document.py` | Normalisierte Content-Einheit |
| `AnalysisResult` | `app/core/domain/document.py` | LLM-Output-Container |
| `SourceMetadata` | `app/ingestion/base/interfaces.py` | Source-Deskriptor |
| `FetchResult` | `app/ingestion/base/interfaces.py` | Adapter-Fetch-Output |
| `AppSettings` | `app/core/settings.py` | Gesamte Konfiguration |

---

## 11. Branch- und PR-Regeln

**Vollständige Strategie → [.github/BRANCH_STRATEGY.md](./.github/BRANCH_STRATEGY.md)**

| Regel | Wert |
|---|---|
| `master` geschützt | Kein direkter Push — nur via PR |
| Required CI checks | `Lint & Format Check` + `Tests` |
| PR-Template | Pflicht — alle Abschnitte ausfüllen |
| Branch-Schema | `<agent>/<phase>/<name>` z.B. `codex/p2/rss-scheduler` |
| Merge-Strategie | Squash and Merge, Branch danach löschen |

**Branch Protection in GitHub aktivieren:**
> Settings → Branches → Add rule → `master`
> ✅ Require PR · ✅ Require status checks (lint + test) · ✅ No bypass

---

## 12. Aktueller Stand

**Phase 1 — Foundation** ✅ abgeschlossen
**Phase 2 — Ingestion Core** 🔄 nächste Phase

Vollständige Task-Liste → [TASKLIST.md](./TASKLIST.md)

---

## 13. Test & Quality Commands

```bash
pytest                          # alle Tests
ruff check .                    # Lint
mypy app/                       # Typ-Check (optional)
uvicorn app.api.main:app --reload   # API starten
python -m app.cli.main --help   # CLI
```

---

## 14. Pflicht-Referenzdokumente

| Dokument | Zweck |
|---|---|
| `CLAUDE.md` | Vollständige Architekturprinzipien und Non-Negotiables |
| `PROJECT_SPEC.md` | Fachliche Spezifikation aller Features |
| `TASKLIST.md` | Operativer Task-Plan nach Phase |
| `AGENT_ROLES.md` | Vollständiges Rollenmodell mit Eskalationspfad |
| `.github/BRANCH_STRATEGY.md` | Branch-Namenskonvention, PR-Workflow, Commit-Format |
| `.github/pull_request_template.md` | Pflicht-PR-Template |
| `app/<modul>/AGENTS.md` | Modul-spezifischer Kontrakt |
