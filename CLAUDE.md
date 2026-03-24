# CLAUDE.md

## Project Identity

**Project Name:** `ai_analyst_trading_bot`  
**Mission:** Build a production-oriented, modular AI-powered monitoring, analysis, alerting, research, and signal-preparation platform for crypto and traditional financial markets.  
**Engineering Motto:** **Simple but Powerful**

This repository is designed to support:
- multi-source market intelligence ingestion,
- structured AI analysis,
- historical and contextual research,
- alerting and prioritization,
- trading-oriented signal preparation,
- future extensibility for execution systems and agent-driven workflows.

The system must remain understandable, testable, modular, and safe.

---
# KAI – Canonical Rules

## Identity
- KAI ist eine Produktplattform, kein Lernprojekt oder Demo.
- Ziel: Analyse-, Signal- und perspektivisch kontrolliertes Realtime-Trading-System.
- Fokus: Sicherheit, Nutzbarkeit, Nachvollziehbarkeit, kontrollierte Erweiterbarkeit.

## Prioritäten (immer in dieser Reihenfolge)
1. Security & Auditierbarkeit
2. CI/CD & Testbarkeit
3. E2E Paper-Workflow (real nachvollziehbar)
4. Operator-Nutzen (Sascha zuerst)
5. Lesbare Outputs (kein JSON-Spam)
6. Minimal funktionierende UI
7. Erst danach Architektur-Optimierung

## Core Rules
- Arbeite immer vom Produktziel rückwärts.
- Bevorzuge den kleinsten sinnvollen, testbaren Schritt.
- Kein Overengineering ohne echten Nutzen.
- Kein Feature ohne klaren Operator-Mehrwert.
- Keine kritischen Aktionen ohne Guardrails, Logs und Audit-Trail.
- Keine stillen Annahmen – immer explizit markieren.
- Keine Lösungen nur für KI-Agenten – immer für Menschen mitdenken.
- Kein Provider-Lock-in.

## Working Mode
- Erst einordnen → dann entscheiden → dann umsetzen.
- Wenn unklar: konservative Annahme treffen und kennzeichnen.
- Änderungen klein, testbar, dokumentierbar und reversibel halten.
- Nur das ändern, was wirklich betroffen ist.

## Output Rules
- Antworte strukturiert, klar und priorisiert.
- Liefere immer:
  - nächsten kleinsten Schritt
  - Risiken / Annahmen
  - konkrete Umsetzung oder Arbeitspaket
- Vermeide theoretische Diskussion ohne direkten Umsetzungswert.

## Red Flags (vermeiden)
- Overengineering
- Scope-Drift
- UI/UX ignorieren
- ungeprüfter Modelloutput → Aktion
- große Umbauten ohne klaren Nutzen
- reine Architektur-Arbeit ohne operativen Effekt

## Definition of Done
Ein Schritt ist nur fertig, wenn:
- er das Produktziel stärkt
- er testbar ist
- er dokumentierbar ist
- er nachvollziehbar ist
- er echten Nutzen bringt

## Hinweis
Nutze für strategische Planung und strukturierte Arbeitspakete den Skill:
kai-master-coding-regeln

## Execution Behavior

Bei jeder Aufgabe gilt:

1. Prüfe zuerst:
   - Ist das relevant für das Produktziel?
   - Ist es der kleinste sinnvolle Schritt?
   - Ist es jetzt dran oder später?

2. Wenn strategisch / unklar:
   → Nutze automatisch: kai-master-coding-regeln

3. Wenn Implementierung:
   - Arbeite in kleinen, testbaren Änderungen
   - Keine Seiteneffekte außerhalb des Scopes
   - Tests + Validierung berücksichtigen

4. Wenn Entscheidung:
   - Begründen
   - Risiken nennen
   - Auswirkungen auf nächsten Schritt klar machen

5. Wenn mehrere Optionen:
   - Wähle die mit:
     - weniger Komplexität
     - höherem Nutzen
     - besserer Nachvollziehbarkeit

6. Wenn Unsicherheit:
   - konservative Annahme treffen
   - explizit markieren

7. Immer liefern:
   - nächsten konkreten Schritt
   - keine offenen Enden

## Core Principles

1. **Keep it simple**
   - Prefer a small number of strong abstractions.
   - Avoid unnecessary framework complexity.
   - Avoid premature microservices.

2. **Keep it powerful**
   - Design for extension, not reinvention.
   - Build generic interfaces with domain-specific adapters.
   - Support crypto, equities, ETFs, and macro-relevant signals.

3. **Keep it reliable**
   - Use typed models.
   - Use deterministic pipelines where possible.
   - Validate all structured outputs.
   - Log clearly and consistently.
   - Fail gracefully.

4. **Keep it modular**
   - Separate ingestion, normalization, enrichment, analysis, storage, alerts, research, and trading preparation.
   - Keep provider-specific logic out of the core domain.

5. **Keep it safe**
   - No hardcoded secrets.
   - No uncontrolled live trading.
   - No unstable scraping-first architecture.
   - Respect source differences: feed, page, channel, API, unresolved source.


## Deploy-Regeln (Kurzfassung)

Deployments sind kontrollierte Eingriffe – niemals Routine.

Vor jedem Deploy zwingend klären:
- Was wird geändert und warum?
- Welche Komponenten/Umgebungen sind betroffen?
- Welche Risiken bestehen?
- Sind Tests ausreichend?
- Sind Konfigurationen & Secrets korrekt?
- Gibt es Migrationen oder Seiteneffekte?
- Ist ein Rollback klar definiert?
- Gibt es Monitoring & Post-Deploy-Checks?

Keine Freigabe bei:
- unklarem Scope
- fehlenden Tests
- unsicheren Konfigurationen
- unklaren Migrationen
- fehlendem Rollback
- Zeitdruck statt Sorgfalt

Pflicht:
- reproduzierbarer Build
- klare Versionierung
- saubere Reihenfolge beim Rollout
- kleine, kontrollierte Schritte
- Post-Deploy-Verifikation

Grundsatz:
Stabilität > Geschwindigkeit  
Sicherheit > Bequemlichkeit  
Klarheit > Annahmen

## Testing-Regeln (Skill)

Nutze den Skill `.claude/skill/Testing-Regeln` IMMER, wenn es um Tests, Qualitätssicherung oder Validierung von Änderungen geht.

Grundprinzip:
Tests dienen nicht der Formalität, sondern der echten Absicherung von Verhalten und Risiken.

Pflichtregeln:

- Teste Verhalten, nicht Implementierungsdetails
- Keine falsche Sicherheit → Lücken klar benennen
- Jeder Test braucht einen klaren Zweck
- Qualität > Coverage
- Stabilität > Komplexität
- So viel wie nötig, nicht so viel wie möglich

Bei jeder Änderung prüfen:

1. Was wurde geändert?
2. Welche Risiken entstehen?
3. Welche Testebene ist sinnvoll? (Unit / Integration / E2E)
4. Welche Fälle müssen getestet werden?
   - Normalfall
   - Randfälle
   - Fehlerfälle
5. Was bleibt ungetestet und warum?

Testqualität muss sein:

- strukturiert
- korrekt
- systematisch
- ehrlich
- sinnvoll

Vermeiden:

- Test-Theater (Tests ohne Aussagekraft)
- fragile Tests
- unnötiges Mocking
- reine Coverage-Jagd
- nur Happy-Path-Tests

Wenn Tests unzureichend sind:
→ klar benennen, keine Beschönigung

Wenn Tests gut sind:
→ begründen warum--

## Skill-Aktivierung: Testing-Regeln

Aktiviere `.claude/skill/Testing-Regeln`, wenn:

- neuer Code geschrieben wird
- bestehender Code verändert wird
- Refactoring durchgeführt wird
- Bugs gefixt werden
- neue Features entstehen
- Tests geschrieben oder angepasst werden
- Code überprüft oder bewertet wird
- Qualität oder Stabilität bewertet wird

---

## Pflichtverhalten bei Aktivierung

Claude muss:

1. Eine Teststrategie ableiten
2. Relevante Testfälle identifizieren
3. Testebene festlegen (Unit / Integration / E2E)
4. Bestehende Tests prüfen
5. Lücken klar benennen
6. Nur sinnvolle Tests erstellen oder vorschlagen

---

## Explizite Commands (optional nutzbar)

- `/test-plan`
  → erstellt strukturierte Teststrategie

- `/test-cases`
  → listet konkrete Testfälle (inkl. Edge Cases)

- `/test-review`
  → bewertet bestehende Tests ehrlich und kritisch

- `/test-gaps`
  → zeigt Schwachstellen und Risiken auf

- `/test-level`
  → entscheidet passende Testebene

- `/test-improve`
  → verbessert bestehende Tests gezielt

---

## Verbotenes Verhalten

Claude darf NICHT:

- Tests nur für Coverage schreiben
- Tests schönreden
- fehlende Tests ignorieren
- Risiken verschweigen
- unnötig komplexe Tests erzeugen

## Non-Negotiable Rules

### Architecture
- Use **Python 3.12+**
- Prefer a **monorepo** with clean module boundaries
- Use **FastAPI** for service endpoints
- Use **Typer** for CLI commands
- Use **Pydantic / pydantic-settings** for configuration
- Use **SQLAlchemy 2.x** and Alembic for DB foundation
- Prefer **PostgreSQL**
- Use **pytest** and **ruff**
- Use **mypy** where it improves stability without heavy friction

### Code Quality
- Write readable, production-oriented code
- Prefer explicitness over magic
- Keep functions focused
- Keep interfaces small and stable
- Add tests for all non-trivial logic
- Do not introduce large dependencies without strong reason

### Data & Source Handling
- Never assume a URL is an RSS feed unless validated
- Never treat a podcast landing page as a feed automatically
- Never treat a YouTube channel URL as a transcript source automatically
- Classify first, resolve second, ingest third
- Maintain explicit unresolved/disabled/requires_api states

### LLM Integration
- Use provider abstraction
- Use structured outputs with schema validation
- No direct business logic inside transport/provider clients
- Version prompts
- Log model/provider metadata when useful
- Keep OpenAI/ChatGPT integration replaceable

### Safety & Scope
- No direct live-trading execution in early phases
- No broad fragile scraping systems as foundation
- No hidden assumptions about API access
- No credentials committed to the repository
- No monolithic "god service"

---

## Working Style Expectations

When working in this repository:

1. Think like a production-oriented lead engineer.
2. Be conservative with assumptions.
3. Prefer robust foundations over flashy features.
4. Implement incrementally.
5. Preserve existing architecture when extending.
6. Add or update tests with meaningful changes.
7. Document assumptions when needed.
8. If a source cannot be resolved cleanly, classify it correctly and move on.

---

## Repository Goals

The repository should evolve into a platform with the following capability layers:

1. **Source Registry & Classification**
2. **Ingestion & Resolution**
3. **Canonicalization & Deduplication**
4. **Rule-Based Analysis**
5. **LLM-Augmented Analysis**
6. **Scoring & Ranking**
7. **Alerting**
8. **Research Outputs**
9. **Signal Preparation**
10. **Advanced Connectors / Narrative Intelligence**
11. **Optional Execution Integration (future, gated)**

---

## System Scope

### Included
- News monitoring
- Website monitoring
- RSS ingestion
- Podcast source classification and resolution
- YouTube channel registry and resolution
- Search/filter/query DSL
- Sentiment, relevance, novelty, impact scoring
- Historical analog structures
- Alerts via Telegram and Email
- Watchlists and signal candidate generation
- Research briefs and prioritized outputs

### Excluded in early phases
- Full live order execution
- High-frequency trading infrastructure
- Broad scraping of arbitrary sources without stability/compliance plan
- Hard vendor lock-in
- Unreviewed autonomous production deployment changes

---

## Preferred Architecture Shape

- **Core domain** remains provider-agnostic
- **Integrations** remain isolated
- **Adapters** remain thin
- **Analysis outputs** remain structured
- **Source classification** is explicit and persistent
- **Research and signal prep** sit on top of normalized and analyzed documents

---

## Expected Module Separation

- `app/core/` → settings, logging, domain types, enums, errors, utilities
- `app/ingestion/` → source adapters, resolvers, registries, schedulers
- `app/normalization/` → canonical schemas, content cleanup, metadata alignment
- `app/enrichment/` → entities, tags, language, dedup helpers
- `app/analysis/` → keyword logic, DSL, sentiment, scoring, historical comparison
- `app/integrations/` → provider-specific clients and adapters
- `app/alerts/` → Telegram, email, alert rules, formatters
- `app/research/` → briefs, summaries, event clusters, watchlists
- `app/trading/` → signal candidates, asset mapping, risk notes
- `app/api/` → FastAPI endpoints
- `app/cli/` → Typer commands
- `app/storage/` → DB models, repositories, migrations
- `monitor/` → user-editable source lists and watchlists

---

## Required Source Taxonomy

Every source must be classifiable into one of these or a compatible extension:

- `rss_feed`
- `website`
- `news_api`
- `youtube_channel`
- `podcast_feed`
- `podcast_page`
- `reference_page`
- `social_api`
- `manual_source`
- `unresolved_source`

Every source should also have a lifecycle/status field such as:
- `active`
- `planned`
- `disabled`
- `requires_api`
- `manual_resolution`
- `unresolved`

---

## Prompting / Agent Collaboration Rules

This repository is expected to be worked on by multiple coding agents and assistants (e.g. Claude Code, Codex, ChatGPT-based workflows).  
To keep outputs compatible:

1. Do not invent new architectural directions without necessity.
2. Reuse existing domain models where possible.
3. Keep naming stable and descriptive.
4. Do not duplicate provider logic across modules.
5. Prefer extension over replacement.
6. Report:
   - files created,
   - files changed,
   - assumptions,
   - TODOs,
   - local test commands.

---

## Quality Bar

Every meaningful implementation should aim to satisfy:

- `pytest` passes
- `ruff check` passes
- config is documented
- new source types are documented
- structured outputs are validated
- unresolved external dependencies are explicitly marked
- no silent architectural drift

---

## Implementation Priorities

1. Foundation
2. Source classification and ingestion
3. Analysis core
4. Alerting
5. Research and signal preparation
6. Advanced connectors and narratives
7. Future execution integration

---

## Explicit Warnings

Do not:
- fake RSS feeds,
- silently scrape unstable sources as if they were APIs,
- hardwire business logic into LLM provider clients,
- entangle analysis logic with transport code,
- skip tests for parser/resolver/scoring logic,
- break existing modules for cosmetic refactors.

---

## What “Done” Means

A feature is only considered meaningfully complete when:
- code exists,
- config exists,
- tests exist,
- docs are updated,
- assumptions are recorded,
- failure states are handled,
- the design still fits the project architecture.

---

## Local Development Expectations

Typical commands should work and remain documented:

- install dependencies
- run API
- run CLI
- run tests
- run lint
- classify sources
- resolve podcasts
- resolve YouTube channels
- validate query syntax
- analyze pending documents
- send test alerts

---

## Final Behavioral Instruction

When unsure:
- classify conservatively,
- extend minimally,
- document clearly,
- avoid fragile shortcuts,
- preserve the long-term integrity of the repository.