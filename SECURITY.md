# SECURITY.md

## Sicherheitsbaseline

- `mode=paper`
- `dry_run=true`
- `live_enabled=false`
- `approval_required=true`
- `require_stop_loss=true`
- `kill_switch_enabled=true`
- `allow_averaging_down=false`
- `allow_martingale=false`

## Aktive Schutzmechanismen

| Mechanismus | Repo-Pfad |
|---|---|
| Settings-Validierung | `app/core/settings.py` |
| API-Auth | `app/security/auth.py` |
| Telegram-Admin-Gating | `app/messaging/telegram_bot.py` |
| Guarded MCP Writes | `app/agents/mcp_server.py` |
| Risk Gates / Kill Switch | `app/risk/engine.py` |
| Paper-only Execution Core | `app/execution/paper_engine.py` |
| Audit Trails | `artifacts/*.jsonl`, `app/alerts/audit.py` |

## Verbindliche Regeln

- keine Secrets im Code
- keine Secrets in Logs
- keine unvalidierten Webhook- oder Bot-Inputs
- keine Live-Aktivierung ohne explizite, vollständige Settings-Freigabe
- keine Auto-Routing-, Auto-Promote- oder Auto-Execution-Pfade
- keine stillen Fehler im kritischen Pfad

## Incident-Hinweise

- bei Unsicherheit: stoppen, alarmieren, Zustand einfrieren
- Telegram `/kill` bleibt confirm-gated
- Operator-Approve/Reject via Telegram ist aktuell audit-only und hat keine Live-Seiteneffekte

## Befund E-1: Klartext-Secrets-Bereinigung (2026-03-21)

**Status**: Technisch bereinigt — externer Rotationsnachweis offen

Das lokale `APIs/`-Verzeichnis enthielt API-Keys als Klartext-Dateien und wurde vor dem
Sprint-9–36-Catch-up-Commit aus dem Projektverzeichnis entfernt. Das Verzeichnis wurde
**niemals** in das Git-Repository committed (`.gitignore`-Eintrag seit Projektbeginn vorhanden;
explizit dokumentiert in `.gitignore` ab 2026-03-21).

| Maßnahme | Status |
|---|---|
| Klartext-Dateien nicht mehr im Projektverzeichnis | ✅ |
| `APIs/` in `.gitignore` eingetragen | ✅ |
| Git-History enthält keine API-Key-Dateien | ✅ (verifiziert via `git log --all`) |
| Externer Rotations-/Invalidierungsnachweis | ⚠️ NICHT BELEGBAR |

**Betroffene Secret-Klassen** (soweit aus Projektstruktur ableitbar):
- Telegram Bot Token
- CoinGecko API Key
- ggf. LLM-Provider-Keys (OpenAI / Anthropic / Google)

**Offener Punkt**: Für Vollabnahme ist manuelles Rotieren/Invalidieren der betroffenen Keys
beim jeweiligen Provider erforderlich. Rotationsdatum und betroffene Services hier eintragen,
sobald abgeschlossen.

**Warum keine Rotation dokumentierbar**: Keine Rotationsdokumentation in SECURITY.md,
AGENTS.md, CHANGELOG.md oder Commit-Messages gefunden. Rotation kann nicht rückwirkend
belegbar gemacht werden — nur vorwärts.
