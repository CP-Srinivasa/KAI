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
