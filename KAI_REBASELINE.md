# KAI_REBASELINE.md
# Kanonische Rebaseline — KAI (Robotron)
# Datum: 2026-03-21 | Sprint-Stand: 36 | Tests: 1315 | Ruff: clean

---

## Was ist KAI?

KAI (Codename: Robotron) ist ein **modulares, sicheres, agentisches LLM-System** für:

- **Marktanalyse**: Multi-Source-Ingestion, Keyword-Engine, LLM-Pipeline,
  Sentiment/Relevanz/Impact-Scoring für Crypto und traditionelle Märkte
- **Signal-Vorbereitung**: SignalCandidate-Extraktion, Confluence-Scoring,
  Watchlist-Integration
- **Kontrolliertes Paper-Trading**: RiskEngine (8 Gates, Kill Switch),
  PaperExecutionEngine (Slippage + Fee), TradingLoop (7-Schritt-Pipeline)
- **Entscheidungsprotokollierung**: DecisionJournal, append-only, immutable,
  nicht-exekutiv
- **Operator-Observability**: MCP-Surface (46 Tools), CLI (47+ Research-Commands),
  Telegram-First-Channel
- **Kontrolliertes Lernen**: Shadow-Run, Distillation, Companion-Evaluation,
  Promotion-Gates mit Rollback-Fähigkeit
- **Audit-Trail**: Vollständige append-only JSONL-Logs für alle kritischen Operationen

---

## Was ist KAI nicht?

KAI ist **nicht**:

- Kein unkontrollierter Auto-Trader
- Kein System, das live-handelt ohne explizite Gate-Freigabe
- Kein System, das ungeprueften LLM-Output ausführt
- Kein Blackbox-System ohne Audit-Trail
- Kein System, das Gewinnversprechen macht oder impliziert
- Kein monolithischer Script-Haufen
- Kein System, das sich ohne Validierung und Rollback selbst modifiziert
- Kein System, das Risiken verschleiert oder Fehler still schluckt
- Kein High-Frequency-Trading-System
- Kein System mit Hard-Vendor-Lock-in

---

## Letzter sicherer kanonischer Baseline-Stand

**Sprint 36b | 2026-03-21 | 1315 Tests | ruff clean**

### Vollständig implementierte und getestete Schichten

| Schicht | Status | Kanonische Dateien |
|---|---|---|
| Core Domain | ✅ Kanonisch | `app/core/` |
| Ingestion & Resolution | ✅ Kanonisch | `app/ingestion/` |
| LLM Analysis Pipeline | ✅ Kanonisch | `app/analysis/`, `app/integrations/` |
| Alerting (Telegram + Email) | ✅ Kanonisch | `app/alerts/`, `app/messaging/` |
| Research & Signals | ✅ Kanonisch | `app/research/` |
| Risk Engine | ✅ Kanonisch | `app/risk/` |
| Paper Execution Engine | ✅ Kanonisch | `app/execution/paper_engine.py` |
| Backtest Engine | ✅ Kanonisch | `app/execution/backtest_engine.py` |
| Signal Generator + Orchestrator | ✅ Kanonisch | `app/signals/`, `app/orchestrator/` |
| Decision Journal | ✅ Kanonisch | `app/decisions/journal.py` |
| MCP Server (46 Tools) | ✅ Kanonisch | `app/agents/mcp_server.py` |
| CLI (47+ Research Commands) | ✅ Kanonisch | `app/cli/main.py` |
| Companion/Shadow/Distillation | ✅ Kanonisch | `app/analysis/providers/` |
| DB Storage Layer | ✅ Kanonisch | `app/storage/` |
| Telegram Operator Bot | ✅ Kanonisch | `app/messaging/telegram_bot.py` |

### Kanonische Dokumentation

| Dokument | Zweck | Stand |
|---|---|---|
| `CLAUDE.md` | Non-Negotiable Architecture Rules | ✅ Aktuell |
| `AGENTS.md` | Sprint-Protokoll P1–P42 | ✅ Aktuell |
| `AGENT_ROLES.md` | Rollenmodell (Codex/Claude/Antigravity) | ✅ Aktuell |
| `docs/contracts.md` | Datenmodelle §1–§47 | ✅ Aktuell |
| `docs/intelligence_architecture.md` | I-1 bis I-250 | ✅ Aktuell |
| `ASSUMPTIONS.md` | A-001 bis A-020 | ✅ Aktuell |
| `RISK_POLICY.md` | Risikoparameter | ✅ Aktuell |
| `SECURITY.md` | Sicherheitsbaseline | ✅ Aktuell |
| `TELEGRAM_INTERFACE.md` | Operator-Befehle | ✅ Aktuell |
| `docs/kai_identity.md` | KAI-Identität | ⚠️ Zähler veraltet (1158 statt 1315) |
| `KAI_SYSTEM_PROMPT.md` | System-Prompt | ✅ NEU v1 |
| `KAI_DEVELOPER_PROMPT.md` | Developer-Prompt | ✅ NEU v1 |
| `KAI_EXECUTION_PROMPT.md` | Execution-Prompt | ✅ NEU v1 |
| `CLAUDE_CODE_ADAPTER.md` | Agent-Adapter Claude | ✅ NEU v1 |
| `CODEX_ADAPTER.md` | Agent-Adapter Codex | ✅ NEU v1 |
| `ANTIGRAVITY_ADAPTER.md` | Agent-Adapter Antigravity | ✅ NEU v1 |

---

## Kanonische Sicherheitsinvarianten (verifiziert, Stand Sprint 36)

| Invariante | Nachweis | Status |
|---|---|---|
| `execution_enabled=False` hardcoded | 50+ Stellen, grep-verifiziert | ✅ |
| `write_back_allowed=False` hardcoded | 50+ Stellen, grep-verifiziert | ✅ |
| `live_enabled=False` als Default | Settings + PaperEngine + Tests | ✅ |
| Risk Engine non-bypassable | A-002 + Tests | ✅ |
| Kill Switch manueller Reset | A-006 + Tests | ✅ |
| MCP Write Guard (artifacts/ only) | I-95 + Tests | ✅ |
| MCP Write Audit (JSONL per Write) | I-94 + Tests | ✅ |
| Alle Dataclasses frozen=True | Architekturstandard + Tests | ✅ |
| Append-only JSONL für alle Audits | Alle Persistence-Funktionen | ✅ |
| Settings nur via Pydantic AppSettings | D-001 | ✅ |
| Telegram-Commands nur für admin_chat_ids | A-004 | ✅ |
| Recording != Executing (Decision Journal) | A-019 + I-248 + Tests | ✅ |

---

## Prompt-Einsatzreihenfolge (kanonisch)

```
STUFE 1 — System Prompt
└── KAI_SYSTEM_PROMPT.md
    Wer ist KAI, was darf er, was darf er nie, welche absoluten Grenzen gelten?

STUFE 2 — Developer Prompt
└── KAI_DEVELOPER_PROMPT.md
    Wie arbeitet man in diesem Repository? Standards, Struktur, Pflichtlektüre.

STUFE 3 — Execution Prompt (nur wenn Execution-Kontext aktiv)
└── KAI_EXECUTION_PROMPT.md
    Welcher Pfad gilt für Signal→Order→Fill? Alle Gates explizit definiert.

STUFE 4 — Agent-Adapter (rollenabhängig)
├── CLAUDE_CODE_ADAPTER.md    → für Architekt-Arbeiten
├── CODEX_ADAPTER.md          → für Implementierungs-Arbeiten
└── ANTIGRAVITY_ADAPTER.md    → für Workflow-Orchestrierung
```

**Reihenfolge ist verbindlich. Kein Adapter ersetzt System- oder Developer-Prompt.**

---

## Was ist "vorgemerkte zukünftige Arbeit" (unreviewed future work)

Diese Bereiche sind architektonisch vorbereitet, aber **noch nicht kanonisch**
(d.h. noch nicht produktionsfreigegeben, noch nicht vollständig getestet oder
noch nicht durch einen offiziellen Sprint-Review gegangen):

| Bereich | Status | Bedingung für Kanonisierung |
|---|---|---|
| Live-Trading-Execution | Vorbereitete Settings, kein Pfad aktiv | Explizite Gate-Chain + Operator-Freigabe + Live-Adapter |
| Telegram `/approve` und `/reject` | audit-only, kein Execution-Effekt | Approval Queue + Reconciliation Layer (A-017) |
| Voice/STT/TTS-Interface | No-op Stub vorbereitet | Approved Backend + Security Review (A-018) |
| Persona/Avatar/Visual | Architektonisch vorbereitet | Stabiler Core + Security Review (A-016) |
| Multichannel-Erweiterung | Vorbereitet | Stabiler Core |
| Real-Exchange-Adapter (Binance etc.) | Nicht implementiert | Live-Gate-Freigabe |
| Echtzeitdaten-Streaming | Nicht implementiert | Exchange-Adapter |
| Multi-Asset-Portfolio-Optimierung | Nicht implementiert | Grundlage |
| Verteiltes Deployment | Nicht implementiert | Single-Process Phase 1 |
| Automatische Telegram-Freigabe | Explizit deaktiviert (A-017) | Approval Queue |

---

## Erkannte Inkonsistenzen (zu beheben)

| Nr. | Inkonsistenz | Datei | Handlung |
|---|---|---|---|
| IC-001 | `docs/kai_identity.md` zeigt "1158 Tests", "Sprint 33", "41 MCP Tools" — veraltet | `docs/kai_identity.md` | Zähler aktualisieren: 1315 Tests, Sprint 36, 46 MCP Tools |
| IC-002 | `AGENT_ROLES.md` referenziert `CLAUDE.litcoffee` statt `CLAUDE.md` | `AGENT_ROLES.md:62` | Korrigieren auf `CLAUDE.md` |
| IC-003 | `TASKLIST.md` Kopfzeile sagt "Last Updated: 2026-03-20" | `TASKLIST.md` | Auf 2026-03-21 aktualisieren |

---

## Offene Risiken

| Nr. | Risiko | Schwere | Maßnahme |
|---|---|---|---|
| R-001 | Kein formaler Review-Prozess für Sprint-Deliverables durch separate Person | Mittel | Review-Checkliste in TASKLIST.md |
| R-002 | `loop-cycle-summary` CLI zeigt rohe JSONL-Records ohne Schema-Validierung | Niedrig | Akzeptiert (read-only, advisory) |
| R-003 | Telegram `/approve` hat noch keinen Execution-Effekt — Operator könnte Fehlverhalten annehmen | Mittel | A-017 dokumentiert, Telegram-Interface aktualisiert |
| R-004 | `docs/kai_identity.md` veraltete Test/Sprint/Tool-Zähler könnte Agenten verwirren | Mittel | IC-001 beheben |
| R-005 | Kein automatisches Audit-Log-Rotation implementiert (A-005 Hinweis) | Niedrig | Manuelle Rotation, dokumentiert |

---

## Prüf-Befehle (sofort ausführbar)

```bash
# Vollständige Test-Suite
python -m pytest -q
# Erwartet: 1315 passed

# Lint
python -m ruff check .
# Erwartet: All checks passed!

# Sicherheitsinvarianten prüfen
grep -r "execution_enabled=True" app/     # Erwartet: 0 Treffer
grep -r "live_enabled=True" app/          # Erwartet: 0 Treffer (nur in Tests ok)
grep -r "write_back_allowed=True" app/    # Erwartet: 0 Treffer

# MCP-Inventory-Konsistenz
python -m pytest tests/unit/test_mcp_server.py -k "inventory_matches" -q
# Erwartet: 1 passed

# Sprint-36-Surfaces
python -m pytest tests/unit/test_mcp_sprint36.py tests/unit/test_cli_decision_journal.py -q
# Erwartet: 34 passed
```
