# Live-Trading Security — Operator-Decision-Log 2026-05-09

**Datum:** 2026-05-09 abend · **Format:** Decision Record nach drei AskUserQuestion-Pfaden.

## Kontext

Operator (Sascha) hat 2026-05-09 explicit "LIVE KEY SECURITY ABSOLUT KRITISCH" als P0-Topic getriggert mit folgender wörtlicher Vorgabe:

> Hardware Root of Trust: TPM, HSM, YubiKey EMPFOHLEN.
> Live-Trading nur wenn: physischer Unlock, Zeitfenster aktiv, Signatur validiert, Risk State OK, Watchdog OK.
> Live API Keys: niemals plaintext auf Disk, nur encrypted at rest, nur temporär decrypted im RAM.
> Live-Trading benötigt: Operator Unlock, Hardware Verification, Session Timeout, Risk State Validation, Watchdog Approval.
> Bei: abnormalen Verlusten, anomalem Verhalten, Datenfehlern, hoher Latenz, Exchange-Ausfällen → sofortiger Kill Switch.

## Architektur-Subagent-Runs

1. **SATOSHI** lieferte Voll-Stack-Spec (Vault + 5-Layer-Approval + 8-Trigger-Kill-Switch + HMAC-Co-Sign) mit 7 Vetos gegen Operator-Brief — siehe `live_trading_circuit_breaker_v1.md`.
2. **architecture-red-team** identifizierte 4 Showstopper (S-001 bis S-004) + B-001/B-002 + Anti-Hypothese §6 "KAI-Light-Live" — siehe `red_team_response_v1.md`.

## Operator-Decisions

### D1 — Architektur-Wahl

**Frage:** Welche Architektur baust du?

**Optionen vorgelegt:**
- A) SATOSHI-Voll-Stack (3-4 Wochen, YubiKey + Vault + 5-Layer + 8-Trigger)
- B) **KAI-Light-Live (3-5 Tage, Hard-Cap + HOTP + Exchange-Perms + Server-SL)** ← gewählt
- C) Hybrid (Light-Live als Phase 0, Voll-Stack ab Capital >$5k)
- D) Paper-only, Live-Track auf Eis

**Operator-Wahl:** **Option B (KAI-Light-Live)**.

**Begründung (von mir abgeleitet):** Pragmatischer Phase-0-Pfad mit gedeckeltem Worst-Case-Risiko, schnell baubar, adressiert die 4 Showstopper teilweise (S-001 durch HOTP per-command, S-002 durch hardcoded Cap, S-003 durch Server-Side-SL, S-004 durch HOTP-Seed-Recovery + Exchange-Web-UI 2FA-Fallback).

### D2 — Capital-Plan

**Frage:** Capital-Plan?

**Operator-Wahl:** **>$10k ernsthaftes Setup**.

**Implikation:** Capital-Skala ist nicht "Lerneffekt". Hard-Cap muss konfigurierbar sein, Default $200/position ist initial konservativ; nach Phase-0-Stabilität soll auf $500-1000/position eskalieren (Worst-Case bei Total-Compromise = $1-2k von $10k Capital = 10-20% — Schmerz-Schwelle, aber tolerabel als Phase-0).

**Folge:** Light-Live als **Phase 0** mit Capital-Cap-Konfigurierbarkeit; **Voll-Stack als Phase 1 nach 3-6 Monaten Stable-Run** und mit Drift-Daten aus Phase-0. De facto Hybrid (Option C), aber mit Light-Live-Start.

### D3 — Sofort-Aktion

**Frage:** Was JETZT konkret tun?

**Operator-Wahl:** **SATOSHIs Spec + Red-Team-Report in `docs/security/` persistieren**.

**Erfüllt 2026-05-09:**
- `live_trading_circuit_breaker_v1.md` — vollständige SATOSHI-Spec (vorher 13-Zeilen-Marker, B-001 gefixt)
- `red_team_response_v1.md` — vollständige Red-Team-Antwort
- `decision_log_20260509.md` — dieses Dokument
- `kai_light_live_phase0_spec.md` — Implementation-Spec für die gewählte Architektur (folgt)

## Aktive Tasks (post-Decision)

| ID | Task | Status |
|---|---|---|
| 32 | P0: Specs persistieren | **in_progress** (heute) |
| 37 | KAI-Light-Live: Implementation-Spec dokumentieren | pending |
| 38 | KAI-Light-Live: Hard-Cap-Enforcement (S-002-Fix) | pending |
| 39 | KAI-Light-Live: HOTP-Auth via Telegram-Command | pending |
| 40 | KAI-Light-Live: Exchange-Permission-Verifier | pending |
| 41 | KAI-Light-Live: Server-Side-SL Pflicht (S-003) | pending |

## Verworfene/parkierte Items

- SATOSHI-Voll-Stack Tasks (33 Vault, 34 Live-Gate, 35 Kill-Switch, 36 CLI) → **deleted**, weil Light-Live-Architektur gewählt. Spec bleibt als Anker für Phase 1 in `live_trading_circuit_breaker_v1.md`.
- YubiKey-Hardware-Beschaffung → "**später entscheiden**". Phase 0 nutzt zunächst Authenticator-App (TOTP) als Fallback; YubiKey OATH-HOTP wird später als Drop-in-Replacement integriert.
- Vault (Argon2id + AES-256-GCM + mlock) → **nicht in Phase 0**, kommt mit Phase 1.

## Red-Team-Showstopper-Adressierung in Phase 0

| ID | In Light-Live adressiert? |
|---|---|
| **S-001** (Session-Timer Theater) | Ja — kein Session-Timer; jede Order braucht HOTP-Code (per-command Auth). |
| **S-002** (RiskEngine in-process patchbar) | Teilweise — Hard-Cap ist hardcoded Konstante im Order-Send-Path, doppelt verifiziert. RiskEngine bleibt in-process, aber Cap-Bypass durch Patch ist schwerer (zwei Punkte zu treffen). Voll-IPC-Härtung kommt mit Phase 1. |
| **S-003** (Server-Side-SL) | Ja — Pflicht beim Order-Open, kein KAI-internes SL-Watching. |
| **S-004** (Recovery-Pfad) | Ja — HOTP-Seed via Authenticator-App ist auf mehreren Geräten install-bar; Exchange-Web-UI mit 2FA als Emergency-Close. Voll-Recovery-Spec als Anhang in `kai_light_live_phase0_spec.md`. |

## Nicht-Operator-Decisions (von mir gesetzt)

- **Phase 0 = Light-Live, Phase 1 = Voll-Stack** als de-facto-Hybrid, weil Capital >$10k geplant ist.
- **Hard-Cap MAX_POSITION_USD initial $200**, konfigurierbar, Eskalation auf $500-1000 nach 3 Monaten Stabilität.
- **HOTP-Provider Phase 0 = Authenticator-App (TOTP)**, Phase 1 = YubiKey OATH-HOTP wenn Operator Hardware-Token kauft.
- **Live-Switch bleibt zu** bis Phase-0-Implementation grün + Operator-Sign-off.
