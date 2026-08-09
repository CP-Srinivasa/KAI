---
name: daily-strategy-review
description: Täglich Pflicht — ehrliches Lagebild + Verbesserungen + neue Quellen + Aufgabenverteilung + P0..P3-Priorisierung + Aufwandsschätzung. Operationalisiert KAI Master Execution Directive §7.
trigger: User sagt "Daily Review", "/daily-strategy-review", "Lagebild", "was ist heute dran", "Status-Analyse", oder zu Session-Start wenn seit letztem Review > 20h vergangen.
---

# Daily Strategy Review (KAI)

Pflicht-Skill pro Kalendertag. Operationalisiert §7 der KAI Master Execution Directive. Liefert **ein** strukturiertes Lagebild mit konkreten, priorisierten Maßnahmen — keine Gefälligkeits-Reports, keine wiederholten Boilerplate-Aussagen.

## Zweck

Zwingt zu täglicher ehrlicher Selbstprüfung: Wo steht KAI? Was ist Leerlauf? Was ist P0? Was wurde verschenkt? Welche Quellen fehlen? Output ist umsetzbar, nicht dekorativ.

## Trigger

- User-Befehl: "Daily Review", "Lagebild", "Status-Analyse"
- Session-Start wenn `artifacts/agents/daily_review/last_run.json` älter als 20h ODER fehlt
- Nach Abschluss einer P0-Aufgabe als Re-Priorisierungs-Check

## Input-Quellen (alle parallel lesen)

**A. Agent-Artifacts (aktueller Zustand):**
- `artifacts/agents/architect/runs.jsonl` — letzter Architect-Review, Findings-Severity
- `artifacts/agents/watchdog/runs.jsonl` + `findings.jsonl` — Drift/Health
- `artifacts/agents/sentr/findings.jsonl` — Security-Issues

**B. Projekt-State:**
- `git status --short` (uncommitted scope)
- `git log --oneline -20` (recent velocity)
- `DECISION_LOG.md` (letzte 5 Decisions)
- `artifacts/ph5_hold_metrics_report.json` (falls vorhanden, echte Metriken)
- `artifacts/alert_outcomes.jsonl` (letzte 50 Zeilen, Precision-Trend)
- `artifacts/paper_execution_audit.jsonl` (Paper-Fills, PnL)

**C. Memory/Kontext:**
- `C:\Users\sasch\.claude\projects\C--Users-sasch--local-bin\memory\MEMORY.md` — Projekt-Memory
- `project_sprint_plan.md`, `project_tv_pivot.md`, `project_roadmap.md` (falls gemerkt)

**D. Externe Signale (wenn greifbar, nicht zwingend):**
- Offene TODOs / ungelöste Operator-Fragen in Konversation
- Letzte User-Frustration/Korrektur (aus Memory-Feedback-Einträgen)

## Output-Format (verbindlich, 6 Sektionen)

### § 1 Lagebild
- **Funktioniert gut:** 1–3 konkrete Punkte mit Evidenz (Run-Zeit, Metrik-Wert, Log-Auszug)
- **Funktioniert teilweise:** Was läuft, aber mit Einschränkung (Grund + Blocker)
- **Fehlt / blockiert:** Was ist nicht da, was hängt
- **Leerlauf/Doppelarbeit/Fehlfokus:** Ehrlich benennen. Wenn nichts erkannt → schreibe "nichts erkannt", nicht "alles perfekt"

### § 2 Konkrete Verbesserungen (3–10)
Liste im Pflichtformat §11 (Vorschlag/Warum jetzt/Nutzen/Quellen/Weg/Parallel/Aufwand/Risiken/Priorität). Jede Verbesserung ist ein eigener Block.

**Unterscheide:** Quick Win (≤2h) · Strategisch (verändert Richtung) · Datenqualität · Signalqualität · Speed · Robustheit

### § 3 Neue Quellen / Wege
- **Kategorien durchprüfen:** A News/Web, B Social/Community, C Markt/Struktur, D Kontrolle (siehe §4 Directive)
- Pro vorgeschlagene Quelle: Name · Kategorie · Integrationskosten (low/med/high) · erwartete Relevanz (low/med/high) · Integrationsweg (API/RSS/Crawl/MCP/Cross-Signal)
- Explizit: Was ist **experimentell aber vielversprechend**? Was ist **stabil verwertbar**?

### § 4 Aufgabenverteilung
- **Sofort erledigbar:** (inkl. Hinweis, ob parallel mit anderem P0)
- **Parallel laufen lassen:** background tasks, monitor-jobs, worker-polls
- **Automatisierbar:** Hook / Cron / Skript — mit Pfad-Vorschlag
- **Manuelle Prüfung erforderlich:** was braucht Operator-Sign-off
- **Subagent-Kandidaten:** Source-Scout / Data-Quality-Inspector / Architecture-Red-Team / anderes

### § 5 Priorisierung
Tabelle aller Maßnahmen aus §2+§3:

| ID | Titel | Prio | Aufwand | Parallel | Blocker |
|----|-------|------|---------|----------|---------|
| V1 | ... | P0 | 2h | ja | — |

### § 6 Ehrliche Aufwandsschätzung (kritische P0/P1)
Pro P0/P1-Maßnahme:
- **Minimal:** Wenn alles glatt läuft
- **Realistisch:** Erfahrungswert inkl. Debugging/Edge-Cases
- **Blocker:** Konkret was könnte aufhalten
- **Abhängigkeiten:** Andere Tasks/Entscheidungen/Freigaben
- **Erwarteter Nutzen:** Messbarer Effekt (Alert-Count↑, Precision↑, Latency↓, etc.)

**Keine Dramatik.** „Dauert Tage" nur wenn wirklich Tage. Wenn 2h realistisch → schreibe 2h.

## Post-Run Pflicht

1. Zusammenfassung als **ein** Operator-lesbares Digest-Message (max. 20 Zeilen, keine Emojis) ausgeben.
2. Artifact schreiben: `artifacts/agents/daily_review/YYYY-MM-DD.md` (voller Report)
3. `artifacts/agents/daily_review/last_run.json` updaten: `{ts, p0_count, p1_count, source_proposals, verbesserungen}`
4. Top-3 P0-Maßnahmen als **TaskCreate** anlegen (wenn nicht existent)
5. Wenn neue Source-Proposals ≥ P1 → Hinweis: `source-expansion` Skill starten (wenn implementiert)

## Anti-Pattern (nicht erlaubt)

- Gefällige Formulierungen: „alles läuft gut", „System stabil", „keine Auffälligkeiten" — ohne Evidenz
- Duplizierte Verbesserungen aus vorigen Reviews, ohne neue Erkenntnis
- P0-Inflation: wenn alles P0, ist nichts P0. Max. 3 P0 pro Review
- Vage Zeitangaben („irgendwann", „bald", „demnächst")
- Source-Vorschläge ohne Integrationsweg
- Reine Symptom-Beschreibung ohne Lösung
- Dramatisierte Aufwände („dauert Tage" bei Stunden-Arbeit)

## Beispiel-Aufruf

User: "Daily Review"

Agent führt aus:
1. Parallel Reads: architect runs, watchdog runs, git status, alert_outcomes, decision log
2. Synthese in 6-Sektionen-Report
3. Digest + Artifact + Task-Anlage

## Referenz

- KAI Master Execution Directive §7 (Pflicht) + §11 (Format)
- Verwandte Skills: `source-expansion` (für §3 vertieft), `research-crosscheck` (für Confidence-Abgleich)
