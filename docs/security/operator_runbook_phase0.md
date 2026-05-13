# Phase-0 Live-Trading — Operator-Setup-Runbook

**Status:** binding für Phase 0 · **Stand:** 2026-05-10 · **Verantwortlich:** Operator (manuell, einmalig)

Dieses Runbook beschreibt die einmaligen Operator-Setup-Schritte, die **ausserhalb** des Code-Stacks passieren müssen, bevor Live-Trading aktiviert werden kann. Code-seitige Schutzmechanismen (Hard-Caps, HOTP, Exchange-Perm-Verifier, Server-SL) sind **wirkungslos**, wenn dieser Setup-Pfad nicht sauber durchlaufen ist.

Cross-Ref: `kai_light_live_phase0_spec.md` · `decision_log_20260509.md` (D1=B Light-Live).

---

## 1 Account-Strategie + Setup

### 1.0 Operator-Decision Account-Strategie

Phase 0 erlaubt drei Account-Strategien. Operator wählt **eine** und folgt nur den passenden Sub-Schritten.

| Option | Was du tust | Vorteil | Nachteil | Schaden-Containment |
|---|---|---|---|---|
| **A — Sub-Account neu** (Spec-Default) | Frischer Sub-Account auf Binance + Bybit, $1.000 Allokation transferred | Hauptkonto-Saldo nicht sichtbar für API-Key | ~30 Min Setup + Internal-Transfer-Schritt | API-Key sieht nur Sub-Account-Saldo |
| **B — API-Key direkt am Hauptkonto** | Neuer API-Key am bestehenden Hauptkonto mit Phase-0-Permissions | Schnellster Setup (~10 Min/Exchange) | API-Key sieht das gesamte Hauptkonto-Saldo (kann aber nichts abziehen) | Hard-Cap + Withdraw-OFF + IP-Allowlist + Server-SL |
| **C — Phase 0 nur auf einem Exchange** | Phase-0 läuft z.B. nur auf Bybit; der zweite Exchange wird später nachgezogen | Reduzierter Setup-Footprint | Single-Point-of-Failure auf Exchange-Seite | wie A oder B je nach Sub-Strategie |

**KYC-Hinweis:** Wenn das Hauptkonto an einem Exchange schon **KYC-verifiziert** ist (typischer Operator-Fall), brauchst du **kein** Re-KYC, weder für A noch B. Sub-Accounts erben den KYC-Status des Masters automatisch — keine zusätzliche ID-Prüfung, keine 1-2-Tage-Wartezeit.

#### Aktive Operator-Decision (2026-05-10): **Option B**

**Begründung Operator:** Setup-Aufwand niedrig halten; Schaden-Containment ist über `MAX_POSITION_USD=200` × 2 + Withdraw-OFF + IP-Allowlist + Server-SL bereits redundant abgesichert. Bei `≤$400` Worst-Case-Schaden ist das "API-Key-sieht-Hauptkonto-Saldo"-Argument vernachlässigbar — Sichtbarkeit ohne Withdraw-Permission ist kein materieller Verlustpfad.

**Konsequenz für die nachfolgenden Abschnitte:**
- §1.1 / §1.2 / §1.3 (Sub-Account-Erstellung) → **überspringen** bei Option B
- §2 (API-Key-Setup) → gilt direkt am Hauptkonto, ohne Sub-Account-Login-Schritt
- §3 / §4 / §5 / §7 / §8 → unverändert anwendbar

---

### 1.1 Binance Sub-Account *(NUR Option A — bei B überspringen)*

**Warum:** Schaden-Containment durch Saldo-Isolation. Phase-0 Worst-Case bei A = ≤$400 begrenzt durch Hard-Cap × 2 Positionen.

1. Binance-Hauptkonto-Login → **Sub-Accounts** (rechts oben unter Profil)
2. **Create Sub-Account** → Typ: `Standard Sub-Account` (kein Managed)
3. E-Mail-Pattern: `kai-live-phase0+<datum>@<dein-domain>` (z.B. `kai-live-phase0+20260510@example.com`). Plus-Notation funktioniert mit Gmail/iCloud/eigener Mail.
4. Passwort: **separat** vom Hauptkonto, ≥20 Zeichen, im Password-Manager
5. 2FA aktivieren auf Sub-Account: Authenticator-App **plus** SMS-Backup-Number
6. **Internal Transfer** vom Hauptkonto: nur Phase-0-Allokation (siehe §1.3)

### 1.2 Bybit Sub-Account *(NUR Option A — bei B überspringen)*

1. Bybit-Hauptkonto-Login → **Account** → **Subaccounts**
2. **Create Subaccount** → Typ: `Standard` (nicht `Custodial`)
3. E-Mail + Passwort wie bei Binance §1.1
4. 2FA aktivieren
5. **Internal Transfer** vom Hauptkonto: nur Phase-0-Allokation

### 1.3 Phase-0-Allokation *(NUR Option A — Sub-Account-Saldo)*

| Strategie | Sub-Account-Saldo | Begründung |
|---|---|---|
| **Konservativ** (empfohlen) | $1.000 | 5x den Worst-Case-Schaden ($200 × 2 Positionen). Reicht für 5-10 Phase-0-Trades. |
| **Mittel** | $2.000 | Mehr Trade-Frequenz möglich, ~10x Worst-Case-Cover. |
| **Maximum** | $5.000 | Nicht empfohlen für Phase 0 — der Cap-Schutz dimensioniert sich nicht mit. |

**Nach 30 Tagen Stable-Run:** Erhöhung möglich, aber **immer** als bewusster Operator-Schritt, nie automatisch.

### 1.4 Option-B-Pfad — API-Key direkt am Hauptkonto

Wenn Operator-Decision = B (siehe §1.0), entfällt §1.1-§1.3 vollständig. Stattdessen:

1. Hauptkonto-Login bei Binance + Bybit (nicht Sub-Account-Login)
2. Direkt zu §2 (API-Key-Setup) — die dortigen Permission-Schritte gelten 1:1 am Hauptkonto
3. **Optional aber empfohlen:** Phase-0-Allokation mental separieren — z.B. ein Notiz im Password-Manager: *"Phase-0-Trading-Budget: $1.000 von Hauptkonto — bei Verlust >$400 Live-Mode hard-locken und Audit-Review"*. Das ist kein Code-Schutz, sondern ein Operator-Schutz gegen Capital-Drift.
4. Hauptkonto-Saldo sollte zum Zeitpunkt der Live-Aktivierung dokumentiert werden (Pre-Flight-Check schreibt das in den Audit-Header), damit ein späterer Drift sichtbar wird.

---

## 2 API-Key-Setup (am Sub-Account oder Hauptkonto, je nach §1.0)

### 2.1 Binance API-Key

1. Login: bei **Option A** Sub-Account-Login (nicht Hauptkonto), bei **Option B** Hauptkonto-Login → **API Management** → **Create API**
2. Label: `kai-phase0-pi5` (eindeutig für Audit)
3. **Restrict access to trusted IPs only:** Pi-WAN-IP eintragen (siehe §3 für IP-Ermittlung)
4. **Permissions:**
   - ✅ `Enable Reading`
   - ✅ `Enable Spot & Margin Trading`
   - ❌ `Enable Withdrawals` — **MUSS aus sein**
   - ❌ `Enable Margin Loan, Repay & Transfer`
   - ❌ `Enable Internal Transfer`
   - ❌ `Enable Universal Transfer`
   - ❌ `Permits Universal Transfer`
   - ❌ `Enable Futures`
   - ❌ `Enable Options`
5. **Save** → Email-Confirm-Link bestätigen
6. API-Key + Secret in Password-Manager (nicht in Plaintext-File!) — Secret wird nur einmal gezeigt

### 2.2 Bybit API-Key

1. Sub-Account-Login → **API** → **Create New Key**
2. Type: `System-generated API Keys`
3. Permissions: `Read-Write` aber nur:
   - ✅ `Spot Trade`
   - ❌ `Withdrawal` — **MUSS aus sein**
   - ❌ `Derivatives` (Futures/Options)
   - ❌ `Earn Products`
   - ❌ `NFT`
   - ❌ `Copy Trading`
4. **IP Restriction:** Pi-WAN-IP eintragen
5. **Save** + 2FA-bestätigung
6. API-Key + Secret in Password-Manager

### 2.3 Verifikation

Nach Speichern: Browser refreshen + Permissions erneut prüfen. Manche Exchanges resetten still einzelne Felder. Was du im Browser siehst, ist Wahrheit. Was du im Setup-Wizard angekreuzt hast, war nur Wunsch.

---

## 3 Pi-WAN-IP-Allowlist

### 3.1 Pi-WAN-IP ermitteln

```bash
ssh ubuntu@192.168.178.23 "curl -s https://api.ipify.org && echo"
```

Output ist die aktuelle WAN-IP (Beispiel: `93.207.183.42`). Diese Adresse trägt der Operator in beide Exchange-API-Allowlists ein.

### 3.2 Dynamic-DNS-Vorsorge

Wenn der Provider keine statische IP hat (typisch bei Vodafone/Telekom-Kabelanschluss):
- IP-Wechsel triggert Live-Mode-Lock (Exchange-Perm-Verifier sieht den Fehler)
- Operator-Notification über Telegram
- Operator muss dann die neue IP in den Allowlists nachziehen
- **Vorbeugend:** Cron-Job auf Pi prüft alle 6h die WAN-IP und alertet bei Wechsel:

```bash
# /etc/cron.d/kai-wan-ip-watch
0 */6 * * * ubuntu /home/ubuntu/ai_analyst_trading_bot/scripts/security/wan_ip_watch.sh
```

(Skript-Skeleton wird in Sprint N+2 geliefert.)

---

## 4 HOTP-Seed-Setup (Authenticator-App)

### 4.1 Seed generieren auf Pi

```bash
ssh ubuntu@192.168.178.23
cd /home/ubuntu/ai_analyst_trading_bot
python -m app.security.hotp_setup
```

Ausgabe (Beispiel):
```
HOTP-Seed (Base32):  JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP
QR-Code (Otpauth):   otpauth://hotp/KAI-Live:operator?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&counter=0&issuer=KAI-Light-Live

Counter-State:       artifacts/security/hotp_counter.jsonl (initialisiert auf 0)
Seed-File:           ~/.config/kai/hotp_seed.b32 (mode 600)
```

(Hinweis: `hotp_setup`-CLI wird in Session N+2 geliefert. Aktuell manuell via `pyotp.random_base32()`.)

### 4.2 Seed in Authenticator-App eintragen

**Empfohlene Apps** (in dieser Reihenfolge):
1. **Aegis** (Android, Open-Source, Encrypted-Backup) — bevorzugt
2. **2FAS** (iOS + Android, Open-Source, iCloud/Drive-Backup)
3. **Authy** (Multi-Device-Sync, vendor-controlled — letzter Fallback)

**Setup-Schritte** (Aegis):
1. App öffnen → **+** → **Scan QR Code**
2. QR-Code vom Pi-Output scannen
3. **Important:** App-Type auf **HOTP** stellen (nicht TOTP-Default!)
4. Counter auf `0` (neuer Seed, kein Pre-Used)
5. Issuer: `KAI-Light-Live` · Account: `operator`
6. Speichern

### 4.3 Backup-QR drucken

**Pflicht — verhindert S-004-Total-Lockout** wenn Smartphone defekt geht.

1. Auf Pi: `qrencode -o /tmp/hotp-backup.png "otpauth://hotp/KAI-Live:operator?secret=<seed>&counter=0&issuer=KAI-Light-Live"`
2. PNG via `scp` auf Operator-Workstation: `scp ubuntu@192.168.178.23:/tmp/hotp-backup.png ~/Desktop/`
3. **Drucken** auf Papier (nicht digital speichern!) — Drucker offline, USB-Kabel-Verbindung
4. Papier in **physischen Tresor** (nicht im selben Raum wie Pi)
5. PNG auf Workstation **shreddern** (`shred -u ~/Desktop/hotp-backup.png` auf Linux/Mac, `Cipher /w` auf Windows)
6. PNG auf Pi löschen (`shred -u /tmp/hotp-backup.png`)

**Test-Restore (Pflicht binnen 7 Tagen):**
1. Aegis löschen / Smartphone-Reset
2. Backup-QR vom Tresor scannen
3. HOTP-Code aus App muss synchron mit Pi-Counter laufen
4. Wenn Test grün: Runbook Schritt 4 abgeschlossen

### 4.4 Zweites Operator-Device (Tablet)

Phase 0 nutzt **2 Geräte** (Smartphone + Tablet) statt YubiKey:

1. Tablet: gleiches Setup wie §4.2, aber **gleicher Seed** (nicht zweiter Seed!)
2. Counter-State ist auf Pi zentral — beide Devices generieren denselben Code
3. Alternative: Backup-Seed-QR (§4.3) auf Tablet einscannen

---

## 5 Pre-Flight-Checks (vor erstem Live-Trade)

Operator-Checkliste, mit Pi-Befehlen verifizierbar:

```bash
ssh ubuntu@192.168.178.23 'cd /home/ubuntu/ai_analyst_trading_bot && python -m app.security.exchange_perms --verify-all'
```

Erwarteter Output:
```
[binance] sub-account: kai-live-phase0+20260510@... ✓
[binance] api-key: kai-phase0-pi5 ✓
[binance] permissions: spot=ON, margin=OFF, withdraw=OFF, futures=OFF ✓
[binance] ip-allowlist: 93.207.183.42 ✓ (matches Pi-WAN-IP)
[binance] last-restriction-check: 2026-05-10T13:42:11Z

[bybit]   sub-account: kai-live-phase0+20260510@... ✓
[bybit]   api-key: kai-phase0-pi5 ✓
[bybit]   permissions: spot=ON, withdraw=OFF, derivatives=OFF ✓
[bybit]   ip-allowlist: 93.207.183.42 ✓
[bybit]   last-restriction-check: 2026-05-10T13:42:13Z

[hotp]    seed-file: ~/.config/kai/hotp_seed.b32 (mode 0600, owner ubuntu) ✓
[hotp]    counter-file: artifacts/security/hotp_counter.jsonl (current counter: 0) ✓
[hotp]    backup-tested: 2026-05-13T18:30:00Z (within 7 days) ✓

[live]    LIVE_TRADING_DEFAULT_ENABLED=False ✓ (boot-default safe)
[live]    MAX_POSITION_USD=200.0 ✓ (hardcoded)
[live]    MAX_OPEN_POSITIONS=2 ✓ (hardcoded)

✅ All Phase-0 pre-flight checks passed.
```

Wenn **eine** Zeile ein ✗ zeigt: Live-Mode bleibt locked, kein Trade möglich, Operator fixt erst.

---

## 6 Setup-Aufwand (realistisch)

### 6.1 Bei Option A (Sub-Account neu)

| Schritt | Dauer | Blocker |
|---|---|---|
| §1.1 + §1.2 Sub-Account-Erstellung Binance + Bybit | ~30 Min | bestehender Master mit KYC-Status erbt automatisch — **kein** Re-KYC |
| §1.3 Internal-Transfer der Phase-0-Allokation | ~15 Min | — |
| §2 API-Key-Setup an beiden Sub-Accounts | ~30 Min | — |
| §3 Pi-WAN-IP + Allowlist eintragen | ~15 Min | — |
| §4 HOTP-Seed-Init + Backup-QR-Print | ~30 Min | Drucker, Tresor-Zugriff |
| §4.3 HOTP-Test-Restore | 7 Tage Lag | bewusste Wartezeit, kein blockender Operator-Aufwand |
| §5 Pre-Flight-Check | ~5 Min | Code für `exchange_perms.py` muss live sein (Sprint N+2) |

**Total Operator-Hands-On:** ~2 Stunden + 7-Tage-Lag bis Test-Restore.

### 6.2 Bei Option B (API-Key am Hauptkonto)

| Schritt | Dauer | Blocker |
|---|---|---|
| §1.4 Account-Strategie-Notiz (mental + Password-Manager) | ~5 Min | — |
| §2 API-Key-Setup an beiden Hauptkonten | ~20 Min | — |
| §3 Pi-WAN-IP + Allowlist eintragen | ~15 Min | — |
| §4 HOTP-Seed-Init + Backup-QR-Print | ~30 Min | Drucker, Tresor-Zugriff |
| §4.3 HOTP-Test-Restore | 7 Tage Lag | bewusste Wartezeit, kein blockender Operator-Aufwand |
| §5 Pre-Flight-Check | ~5 Min | Code für `exchange_perms.py` muss live sein (Sprint N+2) |

**Total Operator-Hands-On:** ~75 Minuten + 7-Tage-Lag bis Test-Restore.

### 6.3 Bei Option C (nur ein Exchange)

Wie 6.1 oder 6.2, halbiert pro Exchange-Schritt. Realistisch ~50 Min Hands-On bei C+B-Kombination.

### 6.4 KYC-Hinweis

Die ursprüngliche Aufwandsschätzung "1-2 Tage wegen KYC-Re-Verification" trifft **nicht** zu, wenn das Hauptkonto bereits KYC-verifiziert ist. Sub-Accounts erben den Status automatisch. Re-KYC wäre nur in seltenen Edge-Cases nötig (Region-Wechsel, Tier-Upgrade, abgelaufene ID-Dokumente — alles nicht der typische Operator-Fall).

---

## 7 Operator-Sicherheitshygiene während Phase 0

1. **Sub-Account-Browser-Session:** nur in einem **Privacy-Browser-Profil** (Firefox-Container, Brave Private). Nie eingeloggt parallel zum Hauptkonto.
2. **API-Secret nie copy-paste in Chat/Notes/Cloud-Notes.** Nur Password-Manager.
3. **HOTP-App auf separatem Gerät** (nicht auf der Workstation, die SSH-Schlüssel zum Pi hat). Smartphone-PIN aktiviert, Biometric on.
4. **Telegram-Bot-Token NICHT auf Operator-Workstation.** Lebt nur auf Pi in `.env`. Wenn Workstation kompromittiert: nur SSH-Key gefährdet, nicht Telegram-Direkt-Steuerung.
5. **Wöchentlicher Audit-Review:** `python -m app.cli.audit verify-live` — prüft `live_execution_audit.jsonl` Hash-Chain + Cap-Compliance.

---

## 8 Notfall-Lock-Sequenz

Wenn Operator Verdacht auf Kompromittierung schöpft:

```
Telegram: /live lock                    # Sofort-Lock, kein HOTP nötig
ssh ubuntu@192.168.178.23 "sudo systemctl stop kai-server"   # Pi-Side-Hard-Stop
```

Auf Exchange-Seite parallel:
1. Binance/Bybit-Sub-Account einloggen
2. **API Management** → **Delete API Key** (sofort)
3. Sub-Account-Passwort ändern
4. 2FA-Reset triggern

Audit nachträglich:
```
python -m app.cli.audit trail --since '24h ago' --type live_execution_audit
```

Zeigt alle Live-Order-Events der letzten 24h, inkl. HOTP-Counter, Cap-Check, IP-Verifikation pro Order. Forensik-tauglich für Versicherungs-/Steuer-Folge.

---

## 9 Cross-Refs

- Spec: `kai_light_live_phase0_spec.md`
- Decision-Log: `decision_log_20260509.md` (D1 = Architektur-Wahl B)
- Red-Team: `red_team_response_v1.md` (S-001..S-004 Mitigationen)
- Code-Skeleton: `app/security/live_caps.py` (commit 76e3de5)
- Verifier (in Arbeit): `app/security/exchange_perms.py` (Sprint N+1)

---

## 10 Sign-off

Phase-0 darf **erst** aktiviert werden, wenn alle 9 Abschnitte oben durchlaufen UND der Pre-Flight-Check (§5) ein vollständiges ✅ liefert.

Operator-Sign-off-Zeile (manuell zu führen):

```
SIGN-OFF Phase-0-Setup:
  Datum:        2026-__-__
  Sub-Account:  binance=___ bybit=___
  WAN-IP:       __.__.__.__
  HOTP-Backup:  getestet am 2026-__-__
  Pre-Flight:   ✅ alle Zeilen grün am 2026-__-__T__:__:__Z
  Initial-Cap:  $___ Sub-Account-Saldo
```
