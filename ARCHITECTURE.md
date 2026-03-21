# ARCHITECTURE.md

## Zielbild

KAI ist ein modularer, sicherheitsorientierter Analyse- und Entscheidungsstack mit
hart getrennten Kernschichten, auditierbaren Operator-Surfaces und standardmäßig
deaktivierter Live-Handlung.

## Kanonischer Kern

| Bereich | Aktueller Repo-Pfad | Status |
|---|---|---|
| Core Orchestrator | `app/pipeline/`, `app/research/active_route.py`, `app/research/operational_readiness.py` | aktiv |
| Model Gateway | `app/analysis/base/`, `app/analysis/factory.py`, `app/integrations/` | aktiv |
| Prompt / Policy Layer | `app/analysis/prompts.py`, schema-validierte Provider-Outputs | aktiv |
| Tool Access Layer | `app/agents/mcp_server.py`, `app/cli/main.py` | aktiv |
| Market Data Ingestion | `app/ingestion/`, `app/integrations/newsdata/`, `app/market_data/` | aktiv |
| Analysis / Signals | `app/analysis/`, `app/research/signals.py`, `app/research/briefs.py` | aktiv |
| Risk Engine | `app/risk/` | aktiv |
| Portfolio / Position Manager | `app/execution/models.py`, `app/execution/paper_engine.py` | aktiv |
| Execution Engine | `app/execution/` | paper-first |
| Memory / Knowledge | `app/research/operational_readiness.py`, review journal, artifacts | partiell aktiv |
| Learning / Evaluation | `app/research/evaluation.py`, `training.py`, `tuning.py`, `upgrade_cycle.py` | aktiv |
| Security Layer | `app/security/`, `app/core/settings.py`, guarded MCP writes | aktiv |
| Observability / Audit | `app/alerts/audit.py`, artifacts JSON/JSONL, readiness/gates/runbook | aktiv |
| Communication Layer | `app/messaging/telegram_bot.py`, `app/alerts/` | aktiv |
| Admin / Control Plane | `app/api/routers/health.py`, CLI, Telegram operator bot | aktiv |

## Sicherheitsprinzipien

- `paper` ist Standardmodus
- `live` ist doppelt gegatet und fail-closed
- kein ungeprüfter Modelloutput im kritischen Pfad
- MCP- und CLI-Surfaces sind standardmäßig read-only oder guarded-write
- jede kritische Aktion braucht Audit-Trail, Logging und Tests

## Erweiterungspunkte

- Persona: `app/messaging/persona_service.py`
- Text-to-Speech: `app/messaging/text_to_speech_interface.py`
- Speech-to-Text: `app/messaging/speech_to_text_interface.py`
- Avatar: `app/messaging/avatar_event_interface.py`

Diese Schnittstellen sind aktuell bewusst deaktivierte Stubs ohne Seiteneffekte.
