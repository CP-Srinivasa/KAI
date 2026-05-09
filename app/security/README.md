# app/security/ — KAI Phase 0 Live-Trading Security

**Stand:** 2026-05-09 — Skeleton-Verzeichnis, kein aktiver Code-Pfad.

## Was hier rein kommt (Phase 0)

| File | Zweck | Status |
|---|---|---|
| `__init__.py` | Modul-Anker (von früherer Security-Arbeit) | ✅ existing |
| `auth.py`, `rate_limit.py`, `secrets.py`, `ssrf.py` | bestehende Security-Layer (auth-mw, rate-limit, secrets-validation, SSRF-Schutz) | ✅ existing |
| `vault.py` | **Codex-Parallel-Track 2026-05-09**: in-memory `LiveVault`-Singleton ohne Encrypted-at-Rest. Passt NICHT zu Phase-0-Spec (HOTP-basiert), und NICHT zu SATOSHIs Phase-1-Spec (Argon2id+AES-GCM+mlock). **Drift-Item — vor Live-Aktivierung reconcilen.** | ⚠ Codex |
| `live_caps.py` | Hardcoded `MAX_POSITION_USD = 200`, `MAX_OPEN_POSITIONS = 2`, `verify_live_order()`, `caps_summary()` | ✅ angelegt (Skeleton, Task 38) |
| `hotp_auth.py` | RFC 4226 HOTP, Counter-Tracking, Telegram-Command-Auth | ⏳ Task 39 |
| `exchange_perms.py` | Boot+periodic Verifier: spot-trade ON, withdraw OFF, IP-allowlist | ⏳ Task 40 |
| `live_engine.py` | analog `paper_engine.py`, mit allen 5 Gates AND-verknüpft | ⏳ Sprint-Plan-Item 7 |

## ⚠️ Codex-Drift bei `vault.py`

Codex hat heute (2026-05-09 18:19) `vault.py` mit einem `LiveVault`-Singleton angelegt, der nur In-Memory-Storage bietet — **kein Encrypted-at-Rest, kein Argon2id, kein mlock**. Das passt zu keiner der zwei dokumentierten Architekturen:

- **Phase 0 (Light-Live)** nutzt **kein Vault**, sondern HOTP-per-Order. Vault ist überflüssig.
- **Phase 1 (SATOSHIs Voll-Stack)** verlangt Argon2id + AES-256-GCM + mlock. Codex' Singleton erfüllt das nicht.

**Vor Live-Aktivierung reconcilen:** entweder Codex' vault.py löschen (Phase 0) oder durch SATOSHIs Spec ersetzen (Phase 1). Aktuell unkritisch, weil Live-Mode disabled ist (`paper_engine.py:73-78`).

## Was NICHT hier rein kommt (Phase 1, später)

- Vault (Argon2id + AES-256-GCM + mlock)
- 5-Layer-Approval mit YubiKey-HMAC-Co-Sign
- 8-Trigger-Kill-Switch-Daemon
- IPC-gehärtete RiskEngine

→ Kommt nach 3-6 Monaten Phase-0-Real-Daten oder bei Capital >$50k.
Voll-Spec: `docs/security/live_trading_circuit_breaker_v1.md`.

## Cross-Refs

- **Active-Architektur-Spec:** `docs/security/kai_light_live_phase0_spec.md`
- **Voll-Stack-Anker:** `docs/security/live_trading_circuit_breaker_v1.md`
- **Red-Team-Befunde:** `docs/security/red_team_response_v1.md`
- **Operator-Decision:** `docs/security/decision_log_20260509.md`

## Coding-Regeln in diesem Modul

1. **Keine Settings-Felder** für Caps oder Threshold-Werte. Hardcoded-Konstanten in `live_caps.py`. Jede Änderung = Code-Review-pflichtig.
2. **Kein dev-Mode-Bypass-Flag.** Wenn ein Test einen Cap umgehen muss, ist der Test falsch.
3. **Fail-closed by default.** Boot-time-Check fehlt → Live-Mode disabled. Permission-Drift → sofort lock. HOTP-Verify-Fehler → Order rejected, kein Retry.
4. **Audit-vor-Action.** Jeder Live-Trade-Event muss VOR dem Exchange-Call ins Audit-JSONL geschrieben werden.
5. **Keine Logs mit Plaintext-Keys, Plaintext-HOTP-Codes, oder Seeds.** Audit zeigt nur Counter-Werte und Hash-Ketten.
