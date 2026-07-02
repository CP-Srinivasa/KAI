# Red-Team Response — gegen SATOSHI Live-Trading Circuit Breaker v1

**Stand:** 2026-05-09 · **Author:** architecture-red-team (subagent-run) · **Subjekt:** SATOSHIs Spec v1.

**Vorab-Befund (B-001):** Die SATOSHI-Spec-Datei war beim Red-Team-Run nur ein 13-Zeilen-Marker — der reale Inhalt lebte in einer Chat-Antwort. Ist nach Operator-Decision 2026-05-09 abend in `live_trading_circuit_breaker_v1.md` voll-persistiert.

**Urteil-Header:** **ROT — Re-Design nötig in S-001 und S-002, gefolgt von Diskussion §6.**

---

## 1 Welche der 10 SATOSHI-Positionen ist fragil oder falsch?

- **#1 (YubiKey Pflicht, TPM/HSM Cargo-Cult):** teilweise falsch. Pi 5 hat **kein** TPM-Modul ab Werk und **kein** Secure-Enclave-Äquivalent — TPM-Cargo-Cult-Vorwurf trifft hier niemanden, weil niemand ernsthaft TPM auf Pi vorgeschlagen hat. Der eigentliche Punkt ist: **YubiKey schützt nur den Unlock-Moment, nicht die laufende Session** (siehe §3). Position klingt entscheidungsstark, ist aber unter-spezifiziert.
- **#2 (Nur Live-Exchange-Keys verschlüsseln, 11 andere in `.env`):** fragil. Telegram-Bot-Token ist ein Live-OOB-Channel — wer ihn hat, kann Approvals fälschen (auch wenn Telegram nicht **als** Approval-Channel dient, ist er Read-Channel für den Operator). Anthropic-Key kann unbegrenzte LLM-Kosten erzeugen. Argumentation "Threat-Model unterscheidet Asset-Klassen" stimmt — aber 11 vs 2 ist **ohne dokumentiertes Threat-Model** willkürlich.
- **#3 (Session 1-2h + 30min idle vs Operator 4h):** vertretbar, aber nicht auf "Sicherheits"-Argument reduzierbar. 4h vs 2h ändert das Angreifer-Modell nur marginal, weil das Hauptproblem nicht "wie lange offen" sondern "kein Re-Auth pro Trade" ist (siehe §3-Adversarial). SATOSHI verkauft Operator-Komfort-Verlust als Sicherheits-Gewinn — schlechter Trade.
- **#4 (8-Trigger-Matrix):** wahrscheinlich richtig, aber 8 ist verdächtig rund. Ohne die Trigger-Liste konkret zu sehen kann ich nicht prüfen, ob welche redundant sind oder ob Lücken bleiben (z.B. "Exchange-API meldet Order accepted, aber Fill kommt nie" — Liveness-Lücke).
- **#5 (Telegram NIE Approval):** korrekt und non-negotiable. SIM-Swap, Bot-Hijack, Telegram-Cloud — kein Two-Party-Trust. Stärkste Position der Liste.
- **#6 (USB-Stick außerhalb Sessions):** theatralisch. Wenn Pi 5 24/7 Trading-Service hosten soll, muss der Stick stecken oder das Vault muss anders gelöst sein. "Stick im Schreibtisch" funktioniert nur, wenn Operator vor jedem Trade physisch zum Pi geht — das widerspricht remote-Operation.
- **#7 (P0 nur Doc, kein Code-Touch):** richtig im Prinzip, aber Doc war heute leer (B-001).
- **#8 (Argon2id+AES-GCM+mlock):** kryptografisch solide. `mlock` auf Pi 5 verlangt RLIMIT_MEMLOCK-Anpassung und root oder CAP_IPC_LOCK.
- **#9 (Watchdog vor Live-Aktivierung härten):** korrekt, aber Verschiebung der Komplexität. "Hardening" ohne konkrete Angreifer-Stories ist ein Bucket. Siehe §3.
- **#10 (HMAC-Co-Sign jeder Live-Order via YubiKey):** das ist die **richtige** Idee — und macht #3 (Session-Timer) weitgehend irrelevant. Wenn jeder Order-Send einen YubiKey-Touch braucht, ist die Session-Länge fast egal. Wenn dann Operator-Touch "max_age_s=30" für Orders >$100, kollidiert das aber mit Auto-Stop-Loss (siehe §2).

**Schwächste Position:** #6 (USB-Stick-Theater) und #2 (willkürliche 2-von-13). **Stärkste:** #5 und #10.

---

## 2 Single-Point-of-Failure-Stress-Test

- **YubiKey verloren, Wochenende, Operator unterwegs:** SATOSHI hat keinen Recovery-Pfad spezifiziert. Optionen: (a) zweiter YubiKey im Bankschließfach (typisch, aber Operator-Aufwand), (b) Recovery-Phrase verschlüsselt offsite (Single-Point-of-Failure verschoben), (c) gar kein Recovery → Live-Trading frozen, **alle offenen Positionen müssen entweder vom Exchange-Web-UI manuell geschlossen werden** (Operator hat dort 2FA-App, nicht YubiKey) oder laufen ungehedged. **Pflicht-Forderung: Recovery-Pfad als P0-Spec-Eintrag, sonst Showstopper.**
- **Stick im Schreibtisch, Operator im Urlaub:** Aktive Live-Position kann nicht geschlossen werden vom Bot, weil Vault gelocked ist. Stop-Loss am Exchange selbst (Server-Side-SL) muss daher **Pflicht** sein und **außerhalb** der Vault-Logik liegen — d.h. SL wird beim Order-Open via Exchange-API gesetzt, nicht via KAI-internem Watchdog. Falls SATOSHI client-side SL-Triggering vorsieht: Showstopper.
- **Pi 5 stirbt mid-Session, `vault_session.lock()` läuft nie:** Plaintext-Keys sind im RAM (auch mit `mlock`) bis Stromverlust. Bei Pi-Reboot über `sudo reboot` → Kernel zerstört Pages, OK. Bei **Stromausfall mit USV-Backup** → RAM bleibt teilweise lesbar im Cold-Boot-Attack-Fenster (theoretisch ~Sekunden auf modernen DDR4, aber ARM-DDR4 weniger gut erforscht). Real ist das Risiko klein, **aber das Argument "mlock schützt Plaintext" ist nicht "Plaintext ist nie verwundbar".**
- **YubiKey-Touch max_age_s=30 für Orders >$100, Operator duscht, Stop-Loss feuert:** Genau hier kollidieren #10 und Realität. **Lösung muss sein: Stop-Loss ist Server-Side am Exchange (separate Permission), nicht via KAI-Order-Path.** Wenn SATOSHI das nicht explizit so macht, ist die Co-Sign-Pflicht ein Bug, kein Feature. **Showstopper, falls SL clientseitig via co-signed Order-Send geplant.**

---

## 3 Adversarial-Tests (kritischste Sektion)

- **SSH-Key-Kompromittierung + warten auf Operator-Unlock:** Operator-Diagnose ist korrekt und vernichtend. YubiKey-Touch beim Vault-Unlock + Session-Timer 1-2h heißt: Angreifer hat 1-2h Beliebig-Order-Window. **Antidot ist #10 (Co-Sign pro Order), nicht #3 (Session-Timer).** Wenn SATOSHI #3 als Sicherheits-Layer verkauft, ist das **Showstopper S-001:** Session-Timer ist nicht adversarial-wirksam, nur Komfort-Verlust.
- **`RiskEngine.check_order` im selben Process patchbar:** Korrekt. Im Paper-Modus egal, in Live ein **harter Bypass**. Mitigationen: (a) RiskEngine als separater Process mit Unix-Socket + HMAC-signed-Request-Response (nicht in-process); (b) Risk-Check-Result mit HMAC vom RiskEngine-Process, im Order-Send-Path verifiziert; (c) Audit-Log mit append-only-FD vor Risk-Check, sodass Tamper sichtbar wird. Aktuell `app/risk/engine.py:109` ist eine Methode in einem Object — Monkey-Patch in 1 Zeile. **Showstopper S-002.**
- **Watchdog-Hardening konkret welche Angriffe?** SATOSHI nennt "adversarial-gehärtet" ohne Inhalt. Realistische Angriffe: (a) Heartbeat-Datei wird vom Angreifer-Cron getouched während `position_monitor_scheduler` tot ist → Live denkt "alles fein"; (b) Heartbeat ohne HMAC und Sequenznummer → Replay; (c) Heartbeat-Reader und -Writer im selben Process → wenn Process hängt, bemerkt niemand. **Hardening-Spec gehört in P0, nicht "vor Live-Aktivierung". Sonst wird P1 zu P3.** Wer baut es? Nicht spezifiziert.

---

## 4 Falsche-Sicherheit-Risiko

**Höchste Wahrnehmung, niedrigste Realität:** #1 (YubiKey) und #6 (USB-Stick im Schreibtisch). Beide sind physisch-sichtbar — Operator denkt "ich halte das Token, also bin ich sicher". Aber:
- YubiKey schützt nur den Unlock-Moment, nicht die Session (siehe §3).
- USB-Stick im Schreibtisch schützt gegen Remote-Attacker, der ohnehin 1-2h Session-Fenster hat (siehe §3).

**Höchste Real-Wirkung pro Komplexität:** #5 (Telegram-no-approval) und #10 (per-order Co-Sign), wenn richtig gebaut. #4 (Trigger-Matrix) ist Mittelfeld.

Operator wird nach 5-Layer-Build wahrscheinlich denken "alles geprüft". Realer Restpfad: **In-Process-Patching von RiskEngine (S-002), Server-Side-SL-Lücken, Recovery-Pfad-Lücken.**

---

## 5 Implementation-Komplexität-Realismus

"P1 1-Wochen-Sprint" ist **klassischer Engineer-Optimismus**. Was übersehen wird:
- **RLIMIT_MEMLOCK** auf Pi 5: braucht `/etc/security/limits.conf` + systemd-`LimitMEMLOCK=infinity`. Jeder Cutover oder Pi-Migration vergisst das.
- **YubiKey-Library-Bindings auf ARM64-Bullseye:** `python-yubico`/`fido2` haben native Deps; kein wheel auf ARM, dann Build-Tool-Chain-Hölle.
- **Test-Hardware:** 2 YubiKeys (Primary + Recovery) — vorhanden? Mock-Tests sind nutzlos, weil das Threat-Model **physische Touch-Verifikation** ist.
- **Edge-Cases der 8-Trigger-Matrix:** Wer schreibt die Test-Suite? Jeder Trigger braucht Trigger-Test + Reset-Test + Inter-Trigger-Test (drei aktiv gleichzeitig).
- **Audit-Trail-Schema-Migration:** Live-Order-Log ist nicht Paper-Log; Replay-Logik (`audit_replay.py`) muss erweitert werden, sonst rehydrate-from-audit bricht beim ersten Live-Trade.
- **Exchange-spezifische Idempotenz-Keys** (Binance vs Bybit haben unterschiedliche `clientOrderId`-Formate und TTLs).

**Realistisch: 3-4 Wochen Solo, 2 Wochen mit Pair-Review.** 1 Woche ist es nur, wenn man Tests auf "Smoke" reduziert — was bei Live-Trading verboten ist. **B-002.**

---

## 6 Anti-Hypothese (radikal einfacher) — "KAI-Light-Live"

**Vorschlag:**
1. Live-Trading **nur via signed Telegram-Command vom Operator** mit HMAC über Symbol+Side+Qty+Nonce, Schlüssel auf YubiKey OATH-HOTP. (Telegram ist Read-Channel, **nicht Auth-Channel** — Auth ist HOTP-Code im Command.)
2. **Hard-coded Capital-Cap** in Code: `MAX_POSITION_USD = 200`, `MAX_OPEN_POSITIONS = 2`. Keine Vault, weil Schaden bei Key-Theft auf $400 begrenzt.
3. Exchange-Keys **read-only + spot-trade only + IP-allowlist** (Pi-IP) — Withdrawal-Berechtigung **nie** vergeben. Damit ist Worst-Case "alle Coins werden zu schlechtem Preis verkauft", nicht "alle Coins weg".
4. Server-Side-SL Pflicht beim Order-Open.
5. Audit-JSONL bleibt wie heute.

**80/20-Schutz:** Kein Vault, kein Argon2id, kein mlock, kein 5-Layer-Stack. Build-Aufwand: **3-5 Tage** statt 3-4 Wochen. Restrisiko: $400 + Slippage. **Erst wenn Operator nachweisbar 6 Monate stabil mit $400-Cap fährt, eskalieren auf $2k mit Vault — aber dann mit Datenlage über reale Bedrohungen, nicht aus Theorie.**

→ **Diese Anti-Hypothese wurde vom Operator 2026-05-09 abend gewählt** (mit Capital-Skala-Anpassung auf >$10k). Spec dazu: `kai_light_live_phase0_spec.md`.

---

## 7 Architektur-Risiko (Metapunkt)

**Ja, das wird ein Maintenance-Albtraum.** Beweis aus dem Repo selbst:
- Memory hat schon Einträge "CSS-JSX-Drift", "Source-Yield-Bugs nach D-122 still tot", "Pi-Cutover-Runbook-Lücken (3 Lehren)" — und das ohne 5+8 neue Layer.
- Codex und main-agent legen parallel Files an (Memory: "Daily-Strategy-Files vor Write zwingend Read").
- V-DB5 hat 12 offene Audit-Items.

Bei nächster Pi-Migration (Pi 5 → Pi 6 oder Cloud) zerbricht: RLIMIT_MEMLOCK, YubiKey-udev-Rules, Vault-Dateipfade, Recovery-Phrase-Backup-Standort, Watchdog-Heartbeat-Sequenznummer-State. **Jeder einzelne dieser Punkte ist ein still-kompromittierter Live-Pfad, wenn nicht detektiert.** SATOSHIs Spec hat keinen Operationalisierungs-Plan für Migration/Drift-Detection.

**Empfehlung:** vor Build mindestens "Migration-Test" als Pflicht-Gate definieren — wenn Layer auf neuer Hardware nicht in 1 Tag wiederhergestellt werden kann, ist er zu komplex.

---

## Showstopper-Liste

| ID | Befund | Status |
|---|---|---|
| **S-001** | Session-Timer (Layer 3) ist nicht adversarial-wirksam — ohne per-Order-Co-Sign ist die ganze Vault-Architektur eine Komfort-Maßnahme | **offen** für Voll-Stack-Phase 1+2; in Light-Live durch HOTP per-command bereits adressiert |
| **S-002** | `RiskEngine.check_order` läuft in-process, monkey-patchbar in 1 Zeile → Live-Bypass | **muss vor Live geschlossen werden** — separater Process oder signed-result. In Light-Live zusätzlich durch hardcoded Cap im Order-Send-Path verifiziert |
| **S-003** | Stop-Loss-Pfad nicht spezifiziert. Wenn clientseitig via co-signed Order, kollidiert mit "Operator duscht / unterwegs" | **Server-Side-SL Pflicht** in beiden Architekturen (Light-Live + Voll-Stack) |
| **S-004** | Recovery-Pfad bei YubiKey-Verlust fehlt — frozen Live-Positionen | **Recovery-Spec Pflicht** vor Live-Aktivierung in beiden Architekturen |
| **B-001** | SATOSHIs Spec-Doc war 13-Zeilen-Marker — Inhalt nur in Chat | **gefixt 2026-05-09** durch Voll-Persistierung |
| **B-002** | 1-Wochen-Sprint ist 3-4 Wochen | **akzeptiert** — Light-Live kommt mit 3-5 Tagen aus, Voll-Stack-Eskalation später |

---

## Blind Spots (Red-Team kann nicht beurteilen)

- Tatsächliche Capital-Pläne: Operator-Antwort 2026-05-09 = **>$10k ernsthaftes Setup**.
- Operator-Reise-Frequenz (Urlaub mit aktiven Positionen?).
- Exchange-Wahl Final: Binance hat Server-Side-SL gut, Bybit anders. Beeinflusst S-003.
- Ob YubiKey 5C NFC schon physisch da ist — sonst ist P1 sowieso blockiert.

---

## Cross-Refs

- **SATOSHI-Spec:** `live_trading_circuit_breaker_v1.md`
- **Operator-Decision:** `decision_log_20260509.md`
- **Active-Architektur:** `kai_light_live_phase0_spec.md`
