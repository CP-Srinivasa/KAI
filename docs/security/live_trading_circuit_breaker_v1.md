# Live-Trading Circuit Breaker — Spec v1 (SATOSHI)

**Stand:** 2026-05-09 · **Author:** SATOSHI (subagent-run) · **Trigger:** Operator-Brief vom 2026-05-09 ("LIVE KEY SECURITY ABSOLUT KRITISCH").

**Kurzfazit:** Der Operator-Brief ist im Kern richtig, aber an drei Stellen Cargo-Cult (TPM/HSM-Trio, Verschlüsselung aller 13 Keys, Hardware-Token "empfohlen"). Mit YubiKey 5C NFC + USB-Stick-Vault + 5-Layer-Approval + 8 Kill-Switch-Triggern ist KAI für privates Capital adäquat gehärtet — ohne Theater. Threat-Model-Eintrag: `SAT-T-LTC-001` in `artifacts/agents/satoshi/threat-models.jsonl`.

> **Hinweis 2026-05-09:** Diese Spec wurde durch Red-Team-Review (siehe `red_team_response_v1.md`) in 4 Showstoppern angefochten (S-001 bis S-004) und durch eine Anti-Hypothese §6 "KAI-Light-Live" ergänzt. **Aktive Architektur-Wahl: Option B (Light-Live)** — siehe `kai_light_live_phase0_spec.md` und `decision_log_20260509.md`. Diese SATOSHI-Spec bleibt als Anker für Phase-1+2 (Voll-Stack ab Capital >$10k oder bei Drift-Daten aus Phase-0).

---

## 1 Threat-Model (Pi 5 im Heimnetz)

| # | Szenario | Realismus | Erstmitigation |
|---|---|---|---|
| **T1** | RCE über exposed Service (cloudflared/CF Access bypass, falsche Origin-Bindung) | **mittel-hoch** — größte real-world-Fläche | Email-Allowlist (D-156d aktiv), Origin nur localhost |
| **T2** | Operator-Laptop-Compromise → SSH-Pubkey ist Pi-Schlüssel | **mittel** | YubiKey-touch für SSH, Vault-Passphrase getrennt |
| **T3** | Pi-Diebstahl (physisch) | **niedrig-mittel** | Vault auf externem USB-Stick, nicht permanent im Pi |
| **T4** | Telegram-Bot-Token-Leak (Code zeigt: `bot_token: str` ungeschützt in `telegram_bot.py:149`, Token aus `OPERATOR_TELEGRAM_BOT_TOKEN` env-plain) | **mittel** | Webhook-Secret bereits da (`_webhook_secret_token`), Approval **niemals** via Telegram |
| **T5** | Supply-Chain (pip-Update-Trojaner) | **mittel** | requirements-Hash-Pinning, separater non-vault-User für CI |
| **T6** | Insider-Codepath-Drift (Codex/Claude legt parallelen Live-Pfad an — siehe Memory-Eintrag zu Daily-Strategy-Vandalismus) | **mittel** | Multi-Approval-Gate AND-chained, keine Bypass-Flag |
| **T7** | Exchange-API-seitiger Compromise | niedrig | trade-only, IP-Whitelist, **no-withdraw** |

**Paranoia (heute irrelevant):** SoC-Backdoor, TEMPEST, LAN-MITM gegen TLS. **Real, oft unterschätzt:** T1 + T5 + T6.

## 2 Hardware-Root — Empfehlung

| Option | Verdikt |
|---|---|
| **YubiKey 5C NFC** | **Empfohlen.** ~55 EUR, schließt T2 fast komplett, PIV/HMAC für Trade-Co-Sign |
| Optiga TPM SLB9670 (Pi-HAT) | Nur falls Pi sowieso fixiert; Pi 5 hat kein `/dev/tpm*` → Aufwand für Pi-HAT fest verlöten steht in keinem Verhältnis |
| Nitrokey HSM 2 | **Cargo-Cult für deinen Scope.** Echtes HSM für privates Capital ist Theater |
| USB-Stick-only (SanDisk Extreme Pro) | **Als Vault-Träger ja, als Auth-Faktor nein** — kein Hardware-Crypto, nur Datenträger |

**Direkt:** YubiKey kaufen, eingestecktes USB als Vault-Träger nutzen. TPM/HSM überspringen, bis Capital sechsstellig wird. Ein Hardware-Token ist Pflicht — nicht "empfohlen". Wenn dir der Aufwand zu groß ist, ist die richtige Antwort Paper-Bleiben, nicht Live-mit-weniger-Schutz.

## 3 Encrypted-Vault-Architektur

**Klare Trennung — gegen Operator-Brief:** Nur **Live-Exchange-Keys** (Binance/Bybit) brauchen Encrypted-Vault. Die anderen 11 Keys (OpenAI, Anthropic, NewsData, Telegram-Bot, X-Bearer …) sind keine Trading-Capital-Risiken — encrypted-at-rest dafür macht den Bot bei Restart unbedienbar oder zwingt die Passphrase ins systemd → Theater.

**Vault-Layout:**
- Datei: `/media/usb/kai-vault/exec-keys.age` auf SanDisk-Stick
- Stick steckt **außerhalb Trading-Sessions** im Schreibtisch — entwertet T3
- Backup-Stick + ausgedruckte Recovery-Phrase im Tresor

**Krypto-Primitive (keine Roll-your-own):**
- KEK: **Argon2id**, `m=256MiB, t=4, p=1, salt=32B random` (Header)
- Sealing: **AES-256-GCM**, 96-bit-Nonce random, 128-bit Tag
- Bibliothek: `cryptography` (pyca, KAI nutzt es schon) — nicht PyCryptodome
- In-Memory-Schutz: `mlock` via `ctypes.CDLL("libc")` — Linux-only, also Vault-Decrypt **nur auf Pi**

**Unlock-Flow:**
```
T0  Operator: 'kai live unlock' (CLI on Pi)
T1  Mount-Check /media/usb/kai-vault/exec-keys.age
T2  YubiKey-touch (PIV-PIN + physical touch)
T3  Passphrase via getpass()  — nie in History
T4  KEK = Argon2id(passphrase, salt) XOR YubiKey-HMAC-SHA1(challenge)
T5  plaintext = AES-GCM-decrypt(blob, KEK)
T6  mlock(plaintext); secure-zero(KEK); unmount USB sofort
T7  Session-TTL 1-2h hard, idle 30 min  (Brief sagt 4h — zu lang)
T8  Auto-Lock: secure-zero plaintext + Session-Token
```

**Vetos:** kein Plaintext in Logs/Tracebacks/JSONL · `.gitignore` für Vault-Pfad · Rotation alle 90 Tage Pflicht · Vault-File **nie** auf Pi-FS gecached.

## 4 Multi-Approval-Gate Pseudo-Code

```python
@require_live_approval
async def submit_order(env: OrderEnvelope) -> OrderResult: ...

def require_live_approval(fn):
    async def wrapper(env, *a, **kw):
        # L1 Operator-Unlock
        if not vault_session.is_unlocked():
            raise LiveBlocked("L1: vault_locked")
        # L2 Hardware-Verification (touch <30s alt für Order > $100)
        if env.notional_usd > LIVE_TOUCH_THRESHOLD_USD:
            if not yubikey.assert_recent_touch(max_age_s=30):
                raise LiveBlocked("L2: hw_touch_required")
        # L3 Session-Timeout
        if vault_session.expired_or_idle(idle_s=1800, ttl_s=7200):
            vault_session.lock(); raise LiveBlocked("L3: session_expired")
        # L4 Risk-State (re-uses app/risk/engine.py:109 check_order)
        risk = risk_engine.check_order(env)
        if not risk.allowed or risk.kill_switch_active:
            raise LiveBlocked(f"L4: risk={risk.reason}")
        # L5 Watchdog-Heartbeat
        if not watchdog.is_green(max_age_s=60):
            raise LiveBlocked("L5: watchdog_red")
        # L6 Trade-HMAC-Co-Sign (kryptographischer Operator-Anwesenheits-Beweis)
        env_hash = sha256(canonical_json(env))
        sig = yubikey.hmac_sha1(slot=2, challenge=env_hash)
        audit.append({"env_hash": env_hash.hex(), "yk_sig": sig.hex(), ...})
        return await fn(env, *a, **kw)
    return wrapper
```

**AND-verknüpft, keine Bypass-Flag, kein dev-mode skip.** L4 nutzt vorhandene `RiskEngine` — kein Reimplement. L6 ist die kryptographische Beweis-Kette: jede Live-Order hat unbestreitbares HMAC vom physischen YubiKey.

## 5 Kill-Switch-Trigger (8 Trigger, AND-Trip)

| ID | Metrik | Default | Begründung |
|---|---|---|---|
| **K1** | Equity-Drawdown rolling 1h | **−3.0%** | Crypto-Vola erlaubt −1% normal; −3% ist Anomalie |
| **K2** | Order-Rate | **>12 / 5min** | Loop-Bug oder Spoof; manueller Trader macht das nicht |
| **K3** | Exchange-Error-Rate | **>30% in 60s** | Exchange-down oder Key-revoked — beides Stop |
| **K4** | Market-Data-Staleness | **last-tick > 8s** | Datenpfad tot → blind handeln verboten |
| **K5** | Order-Latency p95 | **>2500ms / 60s** | Slippage explodiert, Netz degraded |
| **K6** | PnL-Variance 15min vs 7d-Baseline | **>5σ** | Anomalie unabhängig vom Drawdown |
| **K7** | actual_open ≠ intended_open | **>10s** | Reconciliation-Drift = State-Korruption |
| **K8** | 401/403 von Exchange | **>3 / 60s** | Key-Compromise oder Rotation-Mismatch |

**On-Trip:** `vault_session.lock()` → `ctypes.memset` auf mlock'd buffer (secure-zero) → cancel-all-orders best-effort → `circuit_breaker.tripped.jsonl` Audit → Telegram-Notify (nur Notify, kein Approval) → alle pending Decisions → `ApprovalState.REJECTED` → Re-Aktivierung **nur** durch Operator-CLI-Reset + Root-Cause-Bestätigung. **Schwellen niemals auto-relaxen.**

## 6 Implementation-Pfad

**P0 — heute, 1-2h, lokal**
- Doc-Marker geschrieben: `docs/security/live_trading_circuit_breaker_v1.md`
- Threat-Model-Eintrag: `SAT-T-LTC-001` in `artifacts/agents/satoshi/threat-models.jsonl`
- (optional, ohne Code-Touch): `LiveBlockedReason` Enum-Skizze in einem Comment-Block in `app/execution/models.py` — markiert die 5+8 IDs für P1
- **Kein** Code-Pfad in `app/execution/*` aktivieren

**P1 — 1-Wochen-Sprint** *(Red-Team-Korrektur: realistisch 3-4 Wochen)*
- `app/security/vault.py` (AES-256-GCM + Argon2id + mlock-wrapper, **read-only Decrypt**)
- `app/security/yubikey.py` (PIV/HMAC-SHA1 wrapper, `python-fido2`)
- `app/security/circuit_breaker.py` (8 Trigger + Trip-Handler, hooked in `position_monitor_scheduler.py`)
- `app/cli/commands/live.py` (`kai live unlock` / `lock` / `status`)
- Tests: Vault-Roundtrip, wrong-passphrase fail-closed, mlock-presence assert, jeder K-Trigger einzeln + kombiniert + reset, jede L-Layer als negative-test
- Tabletop: simulierter T1-Compromise — wie schnell trippt es

**P2 — vor erster echter Live-Order**
- HMAC-Co-Sign-Audit-Chain materialisiert
- Key-Rotation-Tooling (90d-Reminder, atomic swap, old-key-revoke verification)
- Exchange-Permission-Audit: trade-only, withdraw-deny, IP-Whitelist auf Pi-WAN-IP gepinned
- pip-Hash-Pinning + separater non-vault-User für CI/Codex (gegen T5+T6)
- systemd `LimitMEMLOCK=infinity` setzen — sonst silent-fail, plaintext im Swap

## 7 Hartes Veto gegen Operator-Brief

1. **"Niemals plaintext auf Disk"** → gilt **nur für Live-Exchange-Keys**. Alle 13 Keys zu verschlüsseln macht den Bot unbedienbar oder versteckt die Passphrase in systemd. Klare Trennung Trading-Keys (Vault) vs Service-Keys (env-plain ist OK).
2. **TPM / HSM / YubiKey gleichberechtigt** → falsch. Pi 5 hat kein TPM, HSM ist Overkill, YubiKey ist die einzig sinnvolle Wahl. Trio = Cargo-Cult.
3. **Hardware-Token "EMPFOHLEN"** → **Pflicht**, sobald Live an. Wer den Aufwand scheut, gehört in Paper.
4. **Session-Timeout 4h** → für privates KAI-Capital zu lang; **1-2h** + 30min idle.
5. **"Kill-Switch bei abnormalen Verlusten"** → zu schwach. Drawdown allein ist trivial bypassbar durch viele kleine Verluste. Die 8-Trigger-Matrix ist Pflicht-Untergrenze.
6. **Watchdog-Approval als L5** → gut, aber heutiger `position_monitor_scheduler.py` ist nicht für Adversarial-Heartbeat-Replay gehärtet. Vor Live: Heartbeat muss signed sein (sonst kann ein lokaler Angreifer einfach `green` vortäuschen).
7. **Telegram als Approval-Channel** → bewusst NICHT. Token-Leak (T4) darf niemals zu Live-Order führen. Approval ausschließlich CLI auf Pi mit physischem YubiKey.

## 8 Offene Punkte / Restrisiken

- **mlock + RLIMIT_MEMLOCK:** Linux-default 64KB für non-root → braucht `LimitMEMLOCK=infinity` in systemd-Unit, sonst plaintext im Swap (Pi 5 hat default kein Swap → akzeptabel, aber explizit pinnen).
- **YubiKey PIV vs FIDO2:** PIV schlanker für KAI-Skala, FIDO2 zukunftssicher. PIV+HMAC-SHA1-Slot 2 für jetzt.
- **dm-verity für Code-Integrity** (gegen Pi-Boot-Tampering bei T3): Aufwand vs Gewinn nicht im Verhältnis bei privatem Setup → P3, nicht jetzt.
- **Watchdog-Heartbeat-Hardening** (L5-Lücke) → muss vor Live-Aktivierung adressiert sein, nicht erst danach.

---

## Cross-Refs

- **Red-Team-Review:** `docs/security/red_team_response_v1.md`
- **Operator-Decision:** `docs/security/decision_log_20260509.md`
- **Active-Architektur (Light-Live):** `docs/security/kai_light_live_phase0_spec.md`
- **Threat-Model JSONL:** `artifacts/agents/satoshi/threat-models.jsonl` → `SAT-T-LTC-001`

**Relevante Code-Pfade:**
- `app/execution/models.py:182-195` (vorhandene `ApprovalState` + `DecisionExecutionState` — Spec baut darauf auf, ersetzt sie nicht)
- `app/execution/paper_engine.py:73-78` (heutiger `live_enabled=False`-Enforcer — bleibt Pflicht-Anker bis P1 fertig)
- `app/risk/engine.py:109` (`check_order` — wird L4 im Approval-Gate, nicht reimplementieren)
- `app/messaging/telegram_bot.py:149,193,208,415` (Bot-Token + Webhook-Secret — Approval bleibt **außerhalb** dieses Pfads)
