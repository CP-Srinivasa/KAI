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
  core/              → settings, logging, domain types, enums, errors      [AGENTS.md ✅]
  ingestion/         → source adapters, resolvers, classifiers, RSS        [AGENTS.md ✅]
  normalization/     → content cleaning, text normalization
  enrichment/        → deduplication, entity matching                      [AGENTS.md ✅]
  analysis/          → Analysekern: keyword engine, query DSL, pipeline    [AGENTS.md ✅]
    base/            → LLMAnalysisOutput, BaseAnalysisProvider (ABC)
    keywords/        → KeywordEngine, WatchlistEntry, EntityAlias
    query/           → QueryExecutor (in-memory QuerySpec filter)
    rules/           → RuleAnalyzer, KeywordMatcher, AssetDetector
    scoring.py       → compute_priority(), is_alert_worthy()
    pipeline.py      → AnalysisPipeline (keyword → entity → LLM → AnalysisResult)
  integrations/      → Provider-Implementierungen (HTTP-Schicht)
    openai/          → OpenAIAnalysisProvider (gpt-4o, structured outputs)
    cryptopanic/     → CryptoPanicClient + Adapter (NEWS_API)
  api/               → FastAPI routers + shared deps                       [AGENTS.md ✅]
  cli/               → Typer commands                                      [AGENTS.md ✅]
  storage/           → DB models, repositories, migrations (PostgreSQL)
monitor/             → user-editable source lists, keywords, watchlists
docker/              → Dockerfile (production), docker-compose.yml
tests/unit/          → pytest unit tests (304 passing)
```

---

## 10. Key Domain Models

| Model | Datei | Zweck |
|---|---|---|
| `CanonicalDocument` | `app/core/domain/document.py` | Normalisierte Content-Einheit |
| `AnalysisResult` | `app/core/domain/document.py` | LLM/Regel-Analyse-Ergebnis |
| `EntityMention` | `app/core/domain/document.py` | Strukturierte Entity-Extraktion |
| `LLMAnalysisOutput` | `app/analysis/base/interfaces.py` | Rohausgabe des LLM-Providers |
| `PipelineResult` | `app/analysis/pipeline.py` | Komplettes Analyse-Ergebnis |
| `KeywordHit` | `app/analysis/keywords/engine.py` | Keyword-Match mit Kategorie |
| `QuerySpec` | `app/core/domain/document.py` | Filter-DSL für Dokument-Suche |
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
**Phase 2 — Ingestion Core** ✅ abgeschlossen
**Phase 3 — Analysekern** ✅ abgeschlossen
**Phase 4 — Alerting** 🔄 nächste Phase

| Phase | Status | Geliefert |
|---|---|---|
| P1 Foundation | ✅ | Settings, Enums, FastAPI, CLI, DB-Session, Logging |
| P2 Ingestion | ✅ | Source Registry, Classifier, RSS, CryptoPanic, CanonicalDocument, Dedup |
| P3 Analysekern | ✅ | KeywordEngine, RuleAnalyzer, QueryExecutor, OpenAI Provider, Pipeline |
| P4 Alerting | 🔄 | Telegram, Email, Alert Rules — nächste Phase |

Vollständige Task-Liste → [TASKLIST.md](./TASKLIST.md)

---

## 13. Test & Quality Commands

```bash
# Tests + Lint (lokal)
pytest                                    # 304+ Tests (alle)
ruff check .                              # Lint (muss fehlerfrei sein)
mypy app/                                 # Typ-Check (optional)

# Lokaler Start
cp .env.example .env                      # API-Keys eintragen
uvicorn app.api.main:app --reload         # API auf :8000
python -m app.cli.main --help             # CLI

# Docker
docker compose up --build                 # App + PostgreSQL starten
docker compose --profile dev up adminer  # + DB-UI auf :8080
docker compose down                       # stoppen

# Alembic
alembic upgrade head                      # Migrationen anwenden
alembic revision --autogenerate -m "..."  # neue Migration erstellen
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
