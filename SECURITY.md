# SECURITY.md

## Security Baseline

- default mode: `paper` / `shadow`
- `live` remains default-off
- fail-closed behavior for unsafe or invalid conditions
- no unvalidated model output on critical paths
- guarded actions require auditability

## Confirmed Technical Baseline

- documentation chain for Phase-1 close-out is synchronized
- technical reference remains `1491 passed, ruff clean`
- CI/CD minimum protection is materially hardened

## PH1_FINAL_SECURITY_CLOSURE_002 (Final Blocker)

The last explicit Phase-1 blocker is Befund E-1 (external key-rotation evidence).

CoinGecko public market data is **not** an active rotation blocker in the current runtime path (public API, no key required).

### Befund E-1 — GESCHLOSSEN (2026-03-22)

**Closure-Evidence**: Zum Zeitpunkt des Phase-1-Abschlusses sind keine externen Secrets
in `.env` hinterlegt. Es existieren keine aktiven Keys, die rotiert oder revoziert
werden muessen.

| # | Secret | Env variable | Status |
|---|---|---|---|
| 1 | Telegram operator bot token | `OPERATOR_TELEGRAM_BOT_TOKEN` | nicht gesetzt - kein Rotationsbedarf |
| 2 | Telegram alert token | `ALERT_TELEGRAM_TOKEN` | nicht gesetzt - kein Rotationsbedarf |
| 3 | Telegram webhook secret | `TELEGRAM_WEBHOOK_SECRET_TOKEN` | nicht gesetzt - kein Rotationsbedarf |
| 4 | OpenAI API key | `OPENAI_API_KEY` | nicht gesetzt - kein Rotationsbedarf |
| 5 | Anthropic API key | `ANTHROPIC_API_KEY` | nicht gesetzt - kein Rotationsbedarf |

**Abgeschlossen am**: 2026-03-22  
**Bestaetigt durch**: Sascha  
**Befund-Ref**: E-1  
**Closure-Pfad**: Weg A - keine aktiven Keys, First-Use-Rotation-Policy dokumentiert

### Inventory Note (tracked, but not active PH1 rotation blocker)

- `GEMINI_API_KEY`
- `NEWSDATA_API_KEY`
- `YOUTUBE_API_KEY`
- `ALERT_EMAIL_PASSWORD`
- `APP_API_KEY` (internal operator token, self-managed)

## First-Use Rotation Policy

Wenn ein Key erstmals in `.env` eingetragen wird:

1. Key sofort bei Kompromittierung rotieren.
2. Altes Credential beim Provider revozieren.
3. Kein Secret in Git-History, Logs oder Testfiles.
4. `SECURITY.md` Tabelle mit Datum aktualisieren.

## Phase-2 Gate

**GEOEFFNET** (2026-03-22) - Befund E-1 geschlossen. Phase 2 / Sprint 45 darf eroeffnet werden.

## Operational Security Rules

- never enable live execution by default
- never bypass risk gates
- never execute on malformed or missing guard headers
- never store secrets in code or logs
- on uncertainty: freeze, document, escalate
