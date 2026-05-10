# Phase-0 Live-Trading — Operator-Setup-Runbook

**Status:** binding für Phase 0 · **Stand:** 2026-05-10 · **Verantwortlich:** Operator (manuell, einmalig)

Dieses Runbook beschreibt die einmaligen Operator-Setup-Schritte, die **ausserhalb** des Code-Stacks passieren müssen, bevor Live-Trading aktiviert werden kann. Code-seitige Schutzmechanismen (Hard-Caps, HOTP, Exchange-Perm-Verifier, Server-SL) sind **wirkungslos**, wenn dieser Setup-Pfad nicht sauber durchlaufen ist.

Cross-Ref: `kai_light_live_phase0_spec.md` · `decision_log_20260509.md` (D1=B Light-Live).

---

## 1 Sub-Account-Erstellung (Pflicht — Hauptkonto bleibt unberührt)

**Warum:** Schaden-Containment. Phase-0 Worst-Case = ≤$400 — der Sub-Account hält nur die Phase-0-Allokation, nie das gesamte Trading-Kapital. Ein kompromittierter API-Key kann nichts vom Hauptkonto abziehen.

### 1.1 Binance Sub-Account

1. Binance-Hauptkonto-Login → **Sub-Accounts** (rechts oben unter Profil)
2. **Create Sub-Account** → Typ: `Standard Sub-Account` (kein Managed)
3. E-Mail-Pattern: `kai-live-phase0+<datum>@<dein-domain>` (z.B. `kai-live-phase0+20260510@example.com`). Plus-Notation funktioniert mit Gmail/iCloud/eigener Mail.
4. Passwort: **separat** vom Hauptkonto, ≥20 Zeichen, im Password-Manager
5. 2FA aktivieren auf Sub-Account: Authenticator-App **plus** SMS-Backup-Number
6. **Internal Transfer** vom Hauptkonto: nur Phase-0-Allokation (siehe §1.3)

### 1.2 Bybit Sub-Account

1. Bybit-Hauptkonto-Login → **Account** → **Subaccounts**
2. **Create Subaccount** → Typ: `Standard` (nicht `Custodial`)
3. E-Mail + Passwort wie bei Binance §1.1
4. 2FA aktivieren
5. **Internal Transfer** vom Hauptkonto: nur Phase-0-Allokation

### 1.3 Phase-0-Allokation (empfohlen)

| Strategie | Sub-Account-Saldo | Begründung |
|---|---|---|
| **Konservativ** (empfohlen) | $1.000 | 5x den Worst-Case-Schaden ($200 × 2 Positionen). Reicht für 5-10 Phase-0-Trades. |
| **Mittel** | $2.000 | Mehr Trade-Frequenz möglich, ~10x Worst-Case-Cover. |
| **Maximum** | $5.000 | Nicht empfohlen für Phase 0 — der Cap-Schutz dimensioniert sich nicht mit. |

**Nach 30 Tagen Stable-Run:** Erhöhung möglich, aber **immer** als bewusster Operator-Schritt, nie automatisch.

---

## 2 API-Key-Setup (am Sub-Account)

### 2.1 Binance API-Key

1. **Sub-Account-Login** (nicht Hauptkonto!) → **API Management** → **Create API**
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

| Schritt | Dauer | Blocker |
|---|---|---|
| §1 Sub-Account-Erstellung (Binance + Bybit) | 1-2 Tage | KYC-Re-Verification dauert oft 24h |
| §2 API-Key-Setup | 30 Min | — |
| §3 Pi-WAN-IP + Allowlist | 15 Min | — |
| §4 HOTP-Seed + Backup-Print + Test-Restore | 1-2 Tage (Test-Restore Pflicht) | Drucker, Tresor-Zugriff |
| §5 Pre-Flight-Check | 5 Min | Code für `exchange_perms.py` muss live sein |

**Total realistisch:** 3-4 Tage über mehrere Sessions, hauptsächlich wegen KYC + Test-Restore-Wartezeit.

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
