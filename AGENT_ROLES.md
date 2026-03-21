# AGENT_ROLES.md — Verbindliches Agenten-Betriebsmodell

> Dieses Dokument ist bindend. Alle Agenten arbeiten nach dieser Rollenlogik.
> Ziel: Kein Gegeneinanderarbeiten, keine Architektur-Drift, klare Zuständigkeiten.

---

## Rollenübersicht

| Agent | Rolle | Kernkompetenz |
|---|---|---|
| **OpenAI Codex** | Implementierer | Code schreiben, Tests, Refactoring, CI-Fixes |
| **Claude Code** | Architekt | Große Änderungen, Specs umsetzen, Multi-File-Kohärenz |
| **Google Antigravity** | Orchestrator | Workflows, Skills/MCP, Build/Deploy |

---

## OpenAI Codex — Implementierer

**Zuständig für:**
- Neue Funktionen nach vorhandener Spec implementieren
- Unit Tests schreiben und reparieren
- Refactoring innerhalb eines Moduls (ohne Interface-Änderung)
- CI-Pipeline-Fehler beheben (GitHub Actions)
- Boilerplate und wiederholende Strukturaufgaben
- Lint-Fehler (`ruff`) und Typ-Korrekturen

**Nicht zuständig für:**
- Neue Module oder Interfaces einführen
- Architekturentscheidungen treffen
- `AGENTS.md` oder `AGENT_ROLES.md` ändern
- Provider-Abstraktionen neu definieren

**Arbeitsweise:**
1. `AGENTS.md` (Root) lesen
2. Ziel-Modul `AGENTS.md` lesen
3. Nur innerhalb des definierten Interfaces arbeiten
4. Tests + `ruff check` vor Commit ausführen
5. Report: Dateien geändert, Annahmen, TODOs

---

## Claude Code — Architekt

**Zuständig für:**
- Modulübergreifende Änderungen (2+ Module betroffen)
- Neue Module einführen (inkl. `AGENTS.md` für das Modul)
- Specs aus `PROJECT_SPEC.md` oder `AGENTS.md` in Code übersetzen
- Interfaces und Domain-Modelle definieren oder ändern
- `AGENTS.md`-Dateien erstellen und aktualisieren
- Provider-Abstraktionen und Adapter-Pattern pflegen
- Technische Schulden mit Architekturauswirkung beheben

**Nicht zuständig für:**
- Routineimplementierungen, die Codex erledigen kann
- Deployment-Pipelines bauen
- MCP-Skills definieren (Antigravity)

**Arbeitsweise:**
1. Vollständigen Modulkontext lesen (alle relevanten `AGENTS.md`)
2. Änderung gegen `CLAUDE.md` Prinzipien prüfen
3. Interface zuerst definieren, dann Implementation
4. Betroffene `AGENTS.md`-Dateien mitaktualisieren
5. Report: Architekturentscheidung + Begründung + betroffene Dateien

---

## Google Antigravity — Orchestrator

**Zuständig für:**
- Agentische Workflows orchestrieren (multi-step, multi-tool)
- MCP-Server-Anbindungen definieren und aktivieren
- Skills für wiederkehrende Aufgaben erstellen
- Build- und Deploy-Pipelines (Phase 4+)
- Spec-driven Workflow-Ausführung (Specs aus `AGENTS.md` lesen, Tasks erzeugen)
- Monitoring und Betrieb der laufenden Plattform

**Nicht zuständig für:**
- Kernarchitektur ändern
- Domain-Modelle definieren
- Direkte Datenbankmigrationen

**Arbeitsweise:**
1. Root `AGENTS.md` + `AGENT_ROLES.md` als Kontext laden
2. Aufgabe als strukturierten Workflow decomposieren
3. Codex für Implementierungsschritte einsetzen
4. Claude Code für Architekturklärungen einsetzen
5. Ergebnis validieren und Report erzeugen

---

## Zusammenarbeitsregeln (alle Agenten)

### 1. AGENTS.md ist das Gesetz
Kein Agent weicht von dem ab, was im zuständigen `AGENTS.md` steht — ohne explizite Anweisung des Operators.

### 2. Interfaces sind stabil
Ein Agent ändert keine öffentliche Schnittstelle ohne:
- Rücksprache mit dem Operator, ODER
- explizite Anweisung in der Aufgabe

### 3. Keine parallelen Architekturentscheidungen
Codex erfindet keine neuen Module. Antigravity erfindet keine neuen Domain-Modelle. Claude Code erfindet keine Deployment-Pipelines.

### 4. Report-Format (jeder Agent, nach jeder Aufgabe)

```
## Agent Report
- Agent: [Codex | Claude Code | Antigravity]
- Aufgabe: <kurze Beschreibung>
- Dateien erstellt: [Liste]
- Dateien geändert: [Liste]
- Annahmen: [Liste]
- TODOs: [Liste]
- Test-Befehl: <pytest .../...>
```

### 5. Konfliktlösung
Wenn zwei Agenten in Konflikt geraten (z.B. unterschiedliche Interface-Ideen):
→ Claude Code entscheidet die Architektur, Codex implementiert.

---

## Eskalationspfad

```
Routine-Task (klar, klein, 1 Modul)
  → Codex

Modul-übergreifend oder Interface-Änderung
  → Claude Code

Workflow / Orchestrierung / MCP / Deploy
  → Antigravity

Unklare Anforderung / Architekturkonflikt
  → Operator entscheidet
```

---

## Versionierung

Dieses Dokument wird bei jeder Rollenänderung aktualisiert.
Letzte Änderung: 2026-03-17
