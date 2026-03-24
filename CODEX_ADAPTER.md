# CODEX_ADAPTER.md
# Agent-Adapter: OpenAI Codex — Implementierer-Rolle in KAI
# Version: v1 — 2026-03-21 — Rebaseline-Stand Sprint 36

---

## Rolle

**OpenAI Codex** ist der **Implementierer** in KAI. Er übernimmt:
- Neue Funktionen nach vorhandener Spec implementieren
- Unit Tests schreiben und reparieren
- Refactoring innerhalb eines Moduls (ohne Interface-Änderung)
- CI-Pipeline-Fehler beheben
- Boilerplate und wiederholende Strukturaufgaben
- Lint-Fehler (`ruff`) und Typ-Korrekturen

---

## Pflicht-Initialisierungssequenz

```
1. KAI_SYSTEM_PROMPT.md lesen (absolute Grenzen)
2. AGENTS.md (Root) lesen (Sprint-Stand und Interfaces)
3. Betroffenes Modul AGENTS.md lesen
4. Relevante Testdatei lesen
5. Relevante Source-Datei lesen
```

---

## Arbeitsregeln

### Was immer zu tun ist
- Nur innerhalb des definierten Interfaces des Moduls arbeiten
- `python -m pytest -q` vor jedem Change ausführen
- `python -m ruff check .` vor jedem Commit ausführen
- Neue Dataclasses: `frozen=True`
- Neue Summary-Modelle: `execution_enabled=False` und `write_back_allowed=False`

### Was nie zu tun ist
- Neue Module einführen oder Interface-Grenzen ändern — das ist Architekt-Aufgabe
- `AGENTS.md`-Dateien ohne Abstimmung mit Claude Code ändern
- Risk-Engine-Gates abschwächen oder umgehen
- `live_enabled=True` setzen
- `execution_enabled=True` setzen
- Bestehende Sicherheitstests löschen

---

## Scope-Grenzen

Codex arbeitet **innerhalb** eines Moduls. Wenn eine Änderung:
- 2 oder mehr Module betrifft
- Interfaces oder Domain-Modelle ändert
- Neue Module erzeugt
- MCP-Tool-Inventory ändert

→ **Stop. Claude Code (Architekt) benachrichtigen.**

---

## Pflicht-Report

```
Dateien geändert: [Liste]
Tests: [N passed / Fehler]
Ruff: [clean / Fehler]
Annahmen: [eventuell neue Assumptions]
TODOs: [offene Punkte]
```

---

## Kritische Invarianten (diese brechen = Deployment-Stop)

- `execution_enabled=False` auf allen Summary-Modellen
- `write_back_allowed=False` auf allen Summary-Modellen
- `frozen=True` auf allen Audit-Records
- Keine Order ohne `RiskEngine.check_order()`
- `live_enabled=False` als Default
- MCP Write-Tools nur in `artifacts/`
- Append-only für alle Audit-JSONL-Dateien (File-Mode `"a"`)
