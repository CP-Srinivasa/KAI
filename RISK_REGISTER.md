# RISK_REGISTER.md

## Current State (2026-03-23)

- current_phase: `PHASE 4 (active)`
- current_sprint: `PH4E_SCORING_CALIBRATION_AUDIT`
- next_required_step: `PH4E_EXECUTION_START`
- baseline: `1519 passed, ruff clean`

---

## Validation Findings V-1 .. V-9 (Phase-4 Audit, 2026-03-23)

Diese Befunde wurden im Phase-4-Audit systematisch aus dem Repo-Zustand abgeleitet.
Narrative Herleitung und Abschlussnachweis: `KAI_AUDIT_TRAIL.md` → Abschnitt "V-Series".

| ID | Titel | Schweregrad | Status | Empfohlene Maßnahme |
|---|---|---|---|---|
| **V-1** | E-1 Carryover: Externe Key-Rotation offen | hoch | ⚠️ offen | Rotation von Telegram Bot Token, CoinGecko Key, LLM-Keys durchführen; Nachweis in SECURITY.md Befund E-1 dokumentieren |
| **V-2** | Working Tree uncommitted | mittel | ✅ erledigt | Snapshot-Commit `204857c` erstellt |
| **V-3** | CORS hardcoded in `app/api/main.py` | hoch | ✅ erledigt | `APP_CORS_ALLOWED_ORIGINS` Env-Var eingeführt; `.env.example` und README aktualisiert |
| **V-4** | `.env.example` unvollständig | niedrig | ✅ erledigt | `APP_CORS_ALLOWED_ORIGINS`, `COMPANION_MODEL_ENDPOINT`, `TELEGRAM_WEBHOOK_SECRET_TOKEN` ergänzt |
| **V-5** | README auf Phase-3-Stand (veraltet) | mittel | ✅ erledigt | README auf Phase-4 / PH4E-Stand aktualisiert |
| **V-6** | RUNBOOK.md auf Phase-3-Stand (veraltet) | niedrig | ✅ erledigt | RUNBOOK Scope-Zeile auf Phase 4 aktualisiert |
| **V-7** | D-7 Carryover: Cache-Verzeichnisse physisch vorhanden | niedrig | ⚠️ offen (minor) | `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/` korrekt in `.gitignore`; kein Commit-Risiko; manuelle Bereinigung optional |
| **V-8** | `TELEGRAM_WEBHOOK_SECRET_TOKEN` nicht in `AppSettings` exponiert | mittel | ⚠️ offen | Token wird im `TelegramBot`-Konstruktor erwartet, aber kein Env-Var-Pfad in Settings; als `OPERATOR_TELEGRAM_WEBHOOK_SECRET` ergänzen (eigener Sprint) |
| **V-9** | `APP_ENV=production` Semantik nicht dokumentiert | niedrig | ⚠️ offen | Klarstellen in README/RUNBOOK, was `production`-Mode konkret schaltet (Swagger-Docs aus, CORS-Restriktionen etc.) |

---

### V-1 Detail

**Beschreibung**: Externe Rotation der früher lokal vorhandenen Klartext-Secrets steht aus.
Betroffen: Telegram Bot Token, CoinGecko API Key, alle LLM-Provider-Keys.
**Auswirkung**: Bis zur nachgewiesenen Rotation sind die alten Keys potenziell kompromittiert.
**Nachweis-Ort**: `SECURITY.md` → Befund E-1. Dort Datum + Bestätigung eintragen; keine Key-Werte dokumentieren.

### V-3 Detail (erledigt)

**Beschreibung**: `allow_origins` in `app/api/main.py` war hardcoded auf zwei Localhost-URLs.
**Behebung**: `AppSettings.cors_allowed_origins` (Env: `APP_CORS_ALLOWED_ORIGINS`, kommagetrennt).
Default dev: `http://localhost:3000,http://localhost:8000`. Für Produktion leer lassen oder explizit setzen.

### V-8 Detail

**Beschreibung**: `TelegramBot.__init__` nimmt `webhook_secret_token: str | None` als Parameter,
aber `OperatorSettings` hat kein entsprechendes Feld. Initialisierung erfolgt außerhalb Settings-System.
**Empfehlung**: `OPERATOR_TELEGRAM_WEBHOOK_SECRET` in `OperatorSettings` ergänzen und im Konstruktor auflösen.
**Aufwand**: Klein. Eigener Mini-Sprint oder als Teil der nächsten Telegram-Härtung.

---

## Active Phase-4 Risks

| Risk ID | Description | Severity | Likelihood | Mitigation | Status |
|---|---|---|---|---|---|
| R-PH4-010 | PH4E execution could drift from diagnostics into intervention. | high | medium | Enforce frozen non-goals in PH4E contract and keep execution audit-only. | open |
| R-PH4-011 | PH4E acceptance interpretation could re-broaden scope after freeze. | high | medium | Keep acceptance criteria explicit and narrow in §73; reject out-of-scope outputs. | open |
| R-PH4-012 | Divergence analysis could over-trust Tier-3 without contextual review. | medium | medium | Require root-cause classification (defaults/calibration/missing signal) before any follow-up sprint. | open |

---

## Resolved / Superseded

- PH4B operational blocker (quota) - resolved.
- PH4D regression risk - resolved (`0` regressions).
- PH4D/PH4E governance conflict - resolved.
- PH4E pre-freeze governance ambiguity - resolved by contract freeze.
- V-2 working tree snapshot - resolved (commit `204857c`).
- V-3 CORS hardcoded - resolved (commit see below).
- V-4 .env.example gaps - resolved.
- V-5 README Phase-3 content - resolved.
- V-6 RUNBOOK Phase-3 content - resolved.

---

## Confirmed Context

- PH4D metrics: zero-hit `29 -> 26`, low-hit `27 -> 25`, good-hit `13 -> 18`.
- Remaining zero-hit docs: `26` (`5` true gaps, `21` low-value noise).
- Technical baseline unchanged: `1519 passed`, `ruff clean`.
