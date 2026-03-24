# KAI_DEVELOPER_PROMPT.md
# Developer-Prompt: Arbeiten im KAI-Repository
# Version: v1 — 2026-03-21 — Rebaseline-Stand Sprint 36

---

## Zweck dieses Prompts

Dieser Prompt wird VOR jeder Code-Arbeit im KAI-Repository geladen. Er definiert:
- Was gelesen werden muss, bevor Code geschrieben wird
- Welche Architekturgrenzen nicht überschritten werden dürfen
- Welche Coding-Standards verbindlich sind
- Welche Dateien kanonisch sind

---

## Pflichtlektüre vor jeder Arbeitssession

1. `CLAUDE.md` — Non-Negotiable Architecture Rules
2. `AGENTS.md` (Root) — Sprint-Stand und Architektur-Entscheidungen
3. `docs/contracts.md` — Kanonische Datenmodelle und Surface-Contracts
4. `docs/archive/intelligence_architecture.md` — Historisches Invarianten-Artefakt (nicht aktive Architekturquelle)
5. `ASSUMPTIONS.md` — Dokumentierte Annahmen A-001 bis A-020+
6. Betroffenes Modul `AGENTS.md` (z.B. `app/risk/AGENTS.md`)
7. `KAI_SYSTEM_PROMPT.md` — Identität und absolute Grenzen

---

## Kanonische Modulstruktur

```
app/core/          → Settings, Enums, Domain-Modelle, Logging (kein Business-Logic)
app/ingestion/     → Source-Adapter, Resolver, Registry
app/analysis/      → Keyword-Engine, LLM-Pipeline, Scoring
app/integrations/  → Provider-Adapter (OpenAI, Anthropic, Gemini)
app/research/      → Briefs, Signals, Watchlists, Handoffs, Journal
app/risk/          → RiskEngine, RiskLimits, RiskSettings (NON-BYPASSABLE)
app/execution/     → PaperExecutionEngine, BacktestEngine (PAPER ONLY)
app/signals/       → SignalGenerator, SignalCandidate
app/orchestrator/  → TradingLoop, LoopCycle
app/decisions/     → DecisionJournal, DecisionInstance
app/agents/        → MCP-Server (46 Tools: 36 read + 6 write + rest)
app/cli/           → Typer-CLI (alle Commands)
app/messaging/     → TelegramAlertChannel, TelegramOperatorBot
app/alerts/        → AlertService, AlertRules, Audit
app/storage/       → DB-Modelle, Repositories, Migrations
app/api/           → FastAPI Endpoints
```

---

## Verbindliche Coding-Standards

### Modelle
- Alle Audit-Records: `@dataclass(frozen=True)` — keine Mutation nach Erstellung
- Alle Settings: Pydantic BaseSettings mit env_prefix
- Alle Enums: `StrEnum` (Python 3.11+) statt `(str, Enum)`
- Alle Listen in Dataclasses: als `tuple` für echte Immutabilität

### Sicherheit
- `execution_enabled: bool = False` ist immer vorhanden und hardcoded
- `write_back_allowed: bool = False` ist immer vorhanden und hardcoded
- MCP Write-Tools: immer workspace-confined + artifacts/-restricted (I-95)
- MCP Write-Tools: immer `_append_mcp_write_audit()` aufrufen (I-94)

### Persistence
- Alle Audit-Trails: Append-only JSONL, File-Mode `"a"` nur
- Kein nachträgliches Überschreiben von Audit-Zeilen
- Strukturierte Outputs immer via Schema validieren

### Tests
- Jede nicht-triviale Logik hat Tests
- Frozen-Immutability immer testen
- Security-Invarianten (execution_enabled=False etc.) immer testen
- Path-Traversal-Schutz für MCP Write-Tools immer testen

### Qualitätssicherung
- `python -m pytest -q` muss grün sein vor jedem Commit
- `python -m ruff check .` muss grün sein vor jedem Commit
- Keine `# noqa` ohne zwingenden Grund

---

## Was NICHT geändert werden darf ohne Architektur-Review

- `app/risk/engine.py` — Risk Gates
- `app/execution/paper_engine.py` — Paper Engine Core
- `app/core/settings.py` — Settings Schema
- `app/agents/mcp_server.py` — MCP Inventory (neue Tools: Inventory aktualisieren)
- `_CANONICAL_MCP_READ_TOOL_NAMES` und `_GUARDED_MCP_WRITE_TOOL_NAMES`
- `test_mcp_tool_inventory_matches_registered_tools` — nie deaktivieren

---

## Pflicht-Report nach jeder Arbeitssession

```
1. Dateien erstellt: [Liste]
2. Dateien geändert: [Liste]
3. Tests: [N passed, ruff clean / Fehler]
4. Annahmen: [neue Assumptions in ASSUMPTIONS.md?]
5. Invarianten: [keine neue Pflege in intelligence_architecture.md; aktive Architektur nur in CLAUDE.md + docs/contracts.md]
6. Offene Risiken / TODOs
```

---

## Vier Leitfragen vor jeder Aufgabe

1. Ist das eine Safe-Baseline-Entscheidung oder ein Sicherheitsrisiko?
2. Welches ist das minimal notwendige Change?
3. Welche Tests decken diesen Pfad ab?
4. Welche Dokumentation muss aktualisiert werden?
