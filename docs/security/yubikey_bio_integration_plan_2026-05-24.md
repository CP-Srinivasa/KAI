# YubiKey Hardware-Stack — Integration-Planung (2026-05-24, v2)

**Stichtag:** 2026-05-24
**Trigger:** Operator hat 3 YubiKeys verfügbar.
**Operator-Hardware-Inventar (bestätigt 2026-05-24):**
- **2× YubiKey Bio FIDO Edition (USB-A)** — FIDO2/WebAuthn-only, biometric UV via Fingerprint
- **1× YubiKey 5C N FIPS (USB-C, FIPS 140-2 zertifiziert)** — Full-Feature: FIDO2/WebAuthn + OATH-HOTP/TOTP + PIV + OpenPGP + HMAC-SHA1

→ **Beide Pfade ohne Neukauf möglich.** Bisheriger Plan war auf "Bio-only"-Annahme aufgebaut und ist obsolet.

**Existing Stand:** Phase-0 Light-Live-Spec ([[kai-live-trading-security-phase0]]) erwartet HOTP-Codes. `app/security/hotp_auth.py` (RFC 4226, pyotp) ist implementiert + getestet. YubiKey war ursprünglich als "Phase-1-optional" geplant ([[kai-live-trading-security-phase0]] §"D2/D3"-Decisions, SATOSHI Voll-Stack-Spec).

---

## 1. Fakten-Check YubiKey Bio Edition

| Feature | YubiKey Bio FIDO Edition | YubiKey 5er Standard |
|---|---|---|
| FIDO2 / WebAuthn | ✅ (mit biometric UV) | ✅ (mit PIN-UV) |
| U2F (CTAP1) | ✅ | ✅ |
| **OATH-HOTP / TOTP** | **❌** | ✅ (CCID-App) |
| PIV (PKI Smart Card) | ❌ | ✅ |
| OpenPGP | ❌ | ✅ |
| Static Password | ❌ | ✅ |
| HMAC-SHA1 Challenge-Response | ❌ | ✅ |
| NFC | nur Bio NFC-Variante | 5C NFC, 5 NFC |

**Konsequenz für KAI:** Die Bio-Edition ist eine **reine WebAuthn/FIDO2-Authenticator** mit Fingerprint-User-Verification. Sie kann **nicht** als drop-in-Ersatz für die Authenticator-App im existing HOTP-Pfad dienen.

---

## 2. Empfohlener Stack: A + B parallel (kein Tausch nötig)

Mit der bestätigten Hardware ist **A+B parallel** der optimale Pfad — alle Vorteile beider Architekturen, ohne Trade-off.

### Track B (sofort, 1d): 5C N FIPS als HOTP-Hardware-Container für Phase-0-Light-Live

**Konzept:** Der 5C N FIPS hat einen **CCID-OATH-Slot**. Der existing `hotp_seed.b32` wird via `ykman oath accounts add` programmiert. Operator-Workflow: USB-C-YubiKey berühren → `ykman oath code KAI-Live:operator` zeigt 6-stelligen Code → in Telegram `/trade ...` eintippen.

**Kein Code-Change.** Das `app/security/hotp_auth.py` bleibt unverändert. Effekt: Software-Authenticator-App wird durch Hardware-bound-Container ersetzt → Phone-Compromise kann Live-Trading-Code nicht mehr extrahieren.

**FIPS 140-2 Bonus:** Der N FIPS gehört zu den nach US-NIST zertifizierten Hardware-Modulen — wenn KAI später ein Compliance-Audit braucht (rechtlich relevant bei größeren Capital-Stufen), ist der Cert-Stempel bereits da.

### Track A (5-10d, post-PRE-D): 2× Bio als WebAuthn-Stack für Phase-1

**Konzept:** KAI bekommt einen WebAuthn-Server (FastAPI + `python-fido2` + Credential-DB). `/live unlock` und `/trade ...` werden auf WebAuthn-Assertion-Pfad gehoben, biometric-UV ist Pflicht (UV=1-Flag). HOTP via 5C N FIPS bleibt als **Fallback-Mode** für Notfall-Pfad.

**Beide Bios registrieren:** Primary (täglich) + Backup (Tresor). Recovery-Pfad ohne Hardware-Verlust eingebaut. Plus 5C N FIPS kann zusätzlich als 3. WebAuthn-Credential registriert werden (Triple-Backup).

**Pros (A+B kombiniert):**
- **Phase-0-Go ab morgen** (Track B 1d) ohne auf WebAuthn-Sprint zu warten
- **Phase-1-Upgrade post-PRE-D** ohne Hardware-Tausch (Track A 5-10d)
- **Defense-in-Depth:** Wenn ein YubiKey-Bio verloren → Backup Bio + 5C N FIPS bleiben verfügbar
- **Compliance-Pfad** offen via 5C N FIPS FIPS 140-2 cert
- **Architectural-Konsistenz:** Kein "Bio liegt ungenutzt rum"-Asymmetrie

**Cons:**
- WebAuthn-Implementation (5-10d) ist trotzdem nicht trivial — aber das war in jeder Variante so

### Verworfene Alternativen (zur Dokumentation)

- ~~Option B-only (HOTP allein)~~: würde die 2× Bio ungenutzt lassen, Security-Stufe bleibt bei HOTP
- ~~Option C (Bio nur für Dashboard-Login)~~: Live-Trading-Pfad bliebe schwach, Security-Asymmetrie
- ~~Bio-Tausch~~: nicht nötig, 5C N FIPS deckt HOTP-Bedarf
- ~~5er-Standard-Zukauf~~: nicht nötig, FIPS-Variante ist überlegen

---

## 3. Architecture-Red-Team-Take

**A+B parallel ist die korrekte Antwort.** Track B liefert sofortigen Phase-0-Security-Sprung (App→Hardware) ohne Engineering-Aufwand; Track A schließt die `S-001` Session-Timer-Theater-Lücke aus dem 2026-05-09 Red-Team-Review systematisch. Die Hardware-Investition ist gerechtfertigt durch:

1. **Multi-key-Recovery** (kein Single-YubiKey-Lockout) — 2× Bio + 1× FIPS = 3 unabhängige Credentials
2. **Architektur-Inkrement statt -Sprung** — Phase-0 (HOTP) ist als Backup-Mode verfügbar wenn Phase-1 (WebAuthn) Bug oder Server-Down hat
3. **FIPS-Compliance-Optionalität** — relevant bei Capital-Stufen-Erhöhung [[kai-live-trading-security-phase0]] §D2

Die einzige offene Frage ist Track-A-Operator-UI-Form (Browser-Page vs Telegram-Webview).

---

## 4. Sprint-Plan A+B parallel

### Track B — 5C N FIPS HOTP-Container (Phase-0, 1d total)

| Stage | Aufwand | Wann | Inhalt |
|---|---|---|---|
| B0 Hardware-Inventar | 0d | done | 1× 5C N FIPS + 2× Bio bestätigt |
| B1 OATH-Setup | 0.5d | 2026-05-25 (Mo) | `ykman oath accounts add KAI-Live operator --oath-type HOTP --secret <existing-base32>`; Secret bleibt identisch zu existing seed-file, kein Re-Issue |
| B2 Backup | 0.25d | 2026-05-25 | QR-Code-Print für Tresor (Papier-Backup), 2. Authenticator-App auf isoliertem Backup-Phone als Soft-Fallback |
| B3 Operator-Runbook-Update | 0.25d | 2026-05-25 | `docs/security/operator_runbook_phase0.md` §"HOTP-Setup" erweitern: "Variante A: Authenticator-App", "Variante B: YubiKey 5C N FIPS (empfohlen)" |
| B4 Tabletop | 0.25d | 2026-05-26 | End-to-End: Touch FIPS → `ykman oath code` → Code in TG `/trade` → Live-Order-Path-Simulation → Audit-Trail verifiziert |

**Track-B-Ready für Phase-0-Light-Live-Aktivierung:** 2026-05-26 abend.

### Track A — WebAuthn-Pfad mit 2× Bio + 1× FIPS als 3. Credential (Phase-1, 5-10d)

| Stage | Aufwand | Wann | Inhalt |
|---|---|---|---|
| A0 Spec-Refinement | 0.5d | nach PR #65/PRE-D-Merge (~27.05.) | WebAuthn-API-Design: Routes, Credential-Schema (CredentialID, PublicKey, Counter, AAGUID, UV-Flag-Pflicht), Challenge-Store-TTL 60s |
| A1 Backend | 2-3d | 28.-30.05. | `app/security/webauthn.py` — Registration + Assertion via `python-fido2`; FastAPI-Routes `/auth/webauthn/register/{begin,complete}`, `.../assert/{begin,complete}`; Brute-Force-Guard-Integration |
| A2 Credential-DB | 1d | 30.05. | SQLAlchemy-Model `webauthn_credentials` (operator_id, credential_id, public_key, sign_count, aaguid, registered_at, last_used_at) + Migration + Repository |
| A3 Operator-UI | 1-2d | 31.05.-02.06. | Minimal Browser-Page (`/auth/webauthn.html`) mit `navigator.credentials.get()`-Wrapper; serviert via FastAPI-static |
| A4 Live-Trading-Wiring | 1d | 03.06. | `/live unlock` und `/trade` accepting WebAuthn-Assertion OR HOTP (graceful Fallback); HOTP-Pfad bleibt als Backup für Browser-Outage |
| A5 Multi-Key-Registrierung + Recovery + Tests | 1-2d | 04.-05.06. | Beide Bios + FIPS als 3 Credentials registrieren; Tabletop "verloren primary Bio"; PIN-Reset-Workflow für Bio |
| A6 Pi-Deploy + Smoke | 0.5d | 06.06. | `python-fido2>=1.0` ARM64-Wheel-Check (Pi 5 Bookworm), Migration apply, /auth-Routes-Smoke |

**Track-A-Ready:** ~2026-06-07.

---

## 5. Operator-Decisions (Sign-off 2026-05-24)

**D-YK-1:** ✅ **A+B parallel** angenommen
**D-YK-2:** ✅ **Browser-Page primary** (Track A4-Default), TG-WebView als Phase-2-Iteration nach Browser-Ready
**D-YK-3:** ✅ **Sofort nach PRE-D-Merge** — Track A startet 2026-05-27 (oder Tag-X nach PR PRE-D Merge falls später)

---

## 6. Terminierung Master

| Milestone | Datum | Voraussetzung |
|---|---|---|
| Track B Ready | 2026-05-26 | Operator-Sign-off + 1h Operator-Zeit für ykman-Setup |
| Phase-0-Light-Live-Go möglich | 2026-05-26+ | Plus PR #65 + #68 merged + PRE-D done |
| Track A Start | 2026-05-27 | PRE-D merged |
| Track A Ready | 2026-06-07 | Track-A-Sprint durchgezogen |
| Phase-1-Upgrade Live-Trading auf WebAuthn | 2026-06-08+ | Track A + Tabletop grün |

**Spätester Operator-Sign-off für Track B:** 2026-05-26 morgens (sonst rutscht Phase-0-Go).

---

## 7. Risiken

- **Track-A ARM64-Wheel:** `python-fido2>=1.0` hat Wheels für `linux_aarch64` (pypi-verified Jan 2026), aber Pi-5-Bookworm-Variante prüfen vor Sprint-Start. Falls Build-from-source nötig: +1d Tool-Chain.
- **Track-A Browser-Workflow:** Operator muss Browser auf Workstation öffnen statt Telegram-only. Akzeptabel wenn Workstation eh läuft (Light-Live-Use-Case meistens daytime).
- **Track-B FIPS-Reset:** 5C N FIPS-Variante hat strikteres PIN-Reset-Verhalten als Standard-5er (FIPS-Compliance verlangt key-wipe bei zu vielen PIN-Failures). Operator-Runbook muss PIN-Backup explizit fordern.
- **Track-B USB-C-Port am Pi:** Pi 5 hat USB-C nur als Power-Port. Für ykman-Setup auf Pi: USB-A-Adapter ODER Setup auf Workstation und Seed-File via scp transportieren (sicherer Pfad: Workstation-Setup, Pi nutzt nur seed-file). **Empfehlung:** Setup auf Workstation, Pi nutzt verifiziertes seed-file.
- **Multi-Key-Lockout-Recovery:** 2× Bio + 1× FIPS = 3 unabhängige Credentials. Wenn 2 davon gleichzeitig verloren → Recovery via Papier-Backup-HOTP-Seed im Tresor. Single-Point-of-Failure ist der Tresor selbst.

---

## 8. Cross-Links

- [[kai-live-trading-security-phase0]] — Phase-0-Architektur D1 (Light-Live)
- `docs/security/kai_light_live_phase0_spec.md` — Active-Spec mit HOTP §2
- `docs/security/live_trading_circuit_breaker_v1.md` — SATOSHI Voll-Stack mit YubiKey-HMAC-SHA1-Anker (Phase-1)
- `docs/security/red_team_response_v1.md` §"YubiKey-Library-Bindings ARM64"
- `app/security/hotp_auth.py` (RFC 4226, pyotp, max_advance_window=3) — bleibt als Backup-Pfad bestehen
- [[kai-phase0-n2-done-20260511]] — HOTP-Verifier Task 39 done in PR #6
- [[kai-phase0-pre-sprints-20260510]] — PRE-A/B/C/D-Track; YubiKey ist orthogonal zu PRE-Sprints
- [[kai-dispatch-filter-root-befund-20260524]] — laufender F1/F2/F3-Sprint, sollte parallel weiterlaufen

---

**Status:** Spec liegt vor. Operator-Entscheidung D-YK-1 + D-YK-2/3 pending. Spätester Sign-off **2026-05-28**.
