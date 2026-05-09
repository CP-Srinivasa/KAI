# KAI-Light-Live — Phase 0 Implementation-Spec

**Stand:** 2026-05-09 abend · **Architektur-Wahl:** Operator (siehe `decision_log_20260509.md` D1=B). · **Aufwand:** 3-5 Tage. · **Status der Spec:** binding für Phase 0.

## Ziel

Live-Trading mit gedeckeltem Worst-Case-Risiko **ohne** den 3-4-Wochen-Voll-Stack aus SATOSHIs Spec. Phase 0 sammelt 3-6 Monate Real-Daten. Eskalation auf Voll-Stack (Phase 1) erst mit Drift-Daten + bewährter Operator-Routine.

## Worst-Case-Bilanz

| Szenario | Schaden | Begründung |
|---|---|---|
| SSH-Key gestohlen + Operator-Laptop kompromittiert | **≤ $400** | Hard-Cap `MAX_POSITION_USD=200` × `MAX_OPEN_POSITIONS=2`. Plus: Withdraw-Permission ist OFF → Coins können nicht abgezogen, nur zu schlechten Preisen verkauft. |
| HOTP-Seed leakt + Telegram-Bot-Token leakt | ≤ $400 + Fee-Verlust | gleiches Cap. HOTP-Counter-Replay-Schutz: jeder Code nur 1x gültig. |
| Pi 5 stirbt mid-Trade | offene Position(en) bleiben am Exchange | Server-Side-SL ist beim Order-Open Pflicht. Position schließt automatisch wenn Markt SL trifft, unabhängig von KAI. |
| Operator vergisst Live-Mode-Lock | unbemerkter Drift | Auto-Lock nach 60 Min Idle (HOTP-Counter), live-mode required HOTP zum Re-Aktivieren. |

## Komponenten

### 1 Hard-Cap-Enforcement (`app/security/live_caps.py`)

**Hardcoded constants** — kein Settings-Field, kein dynamischer Override:

```python
# app/security/live_caps.py — DO NOT make configurable.
# Phase-0-Schutz: jeder Override braucht Code-Edit + Re-Deploy + Operator-Review.
MAX_POSITION_USD: float = 200.0
MAX_OPEN_POSITIONS: int = 2
LIVE_TRADING_DEFAULT_ENABLED: bool = False  # Boot-Default, wird per HOTP geunlockt
LIVE_MODE_IDLE_LOCK_SECONDS: int = 3600     # 60 min Idle → auto-lock
```

**Doppel-Verifikation** im Order-Send-Path:
1. **RiskEngine** (`app/risk/engine.py:check_order`) — bestehender Pfad, paper-erprobt
2. **live_caps.verify_live_order** — neu, hardcoded, separater Code-Pfad

```python
def verify_live_order(env: OrderEnvelope, current_open: int) -> None:
    if env.notional_usd > MAX_POSITION_USD:
        raise LiveCapBreach(f"position_size: {env.notional_usd} > {MAX_POSITION_USD}")
    if current_open >= MAX_OPEN_POSITIONS:
        raise LiveCapBreach(f"max_positions: {current_open} >= {MAX_OPEN_POSITIONS}")
```

**S-002-Mitigation:** Angreifer muss **zwei** unabhängige Pfade patchen (RiskEngine + live_caps). In Phase 1 ergänzt durch separaten IPC-Process für RiskEngine.

### 2 HOTP-Auth via Telegram (`app/security/hotp_auth.py`)

**Modell:** RFC 4226 HOTP (Counter-basiert). Seed initial in Authenticator-App (Aegis, Authy, Google Authenticator) — später bei YubiKey-Beschaffung Drop-in-Replacement durch YubiKey OATH-HOTP.

**Seed-Storage Phase 0:**
- File `~/.config/kai/hotp_seed.b32` (mode 600, owner ubuntu)
- Plus Backup-Seed als QR-Print im Tresor (S-004-Recovery)
- **Nicht** in `.env`, **nicht** im Git, in `.gitignore`

**Counter-State:**
- `artifacts/security/hotp_counter.jsonl` — append-only, jede Verifikation loggt counter+timestamp
- Replay-Schutz: counter darf nur monoton steigen
- Tolerance-Fenster: 3 Codes voraus (Operator's App und Pi können desyncen)

**Telegram-Commands:**
```
/live unlock <hotp>           # Aktiviert Live-Mode (Idle-TTL 60 min)
/live status                  # Zeigt: locked/unlocked, last-trade, current-positions
/live lock                    # Sofort-Lock (kein HOTP nötig)
/trade <SYM> <side> <qty> <hotp>   # Live-Order, jeder Trade braucht NEUEN HOTP
```

**Per-Order-HOTP-Pflicht:** auch wenn Live-Mode "unlocked" ist. Das adressiert S-001 (Session-Timer-Theater): Angreifer mit SSH-Key kann zwar Live-Mode-Flag auf "unlocked" setzen, aber jeder einzelne Trade verlangt einen neuen HOTP-Code, den nur das physische Operator-Device generiert.

### 3 Exchange-Permission-Verifier (`app/security/exchange_perms.py`)

**Boot-time-Check** (kai-server lifespan event):
- Binance: `GET /sapi/v1/account/apiRestrictions` → muss zeigen: `enableSpotAndMarginTrading=true`, `enableWithdrawals=false`, `tradingAuthorityExpirationTime=null`, IP-Allowlist enthält Pi-WAN-IP
- Bybit: `GET /v5/user/query-api` → analog
- Wenn Verifikation **fehl** → kai-server lädt mit `LIVE_MODE_PERMANENT_DISABLED=True`, kein Live-Trade möglich, Telegram-Notify an Operator

**Periodic-Re-Check** alle 30 Min via Position-Monitor-Scheduler-Hook. Bei Permission-Drift (z.B. Withdrawal versehentlich aktiviert) → Live-Mode sofort lock.

**Operator-One-Time-Setup** (außerhalb Code, in Implementation-Runbook):
1. Binance/Bybit-Account: API-Key erstellen mit `Spot-Trade=ON`, `Withdraw=OFF`, `Futures=OFF` (Phase 0)
2. IP-Allowlist: nur Pi-WAN-IP eintragen
3. **Sub-Account empfohlen** — separater Sub-Account mit max $10k transferred, Main-Account bleibt unberührt

### 4 Server-Side-SL Pflicht (`app/execution/exchanges/{binance,bybit}.py` Erweiterung)

**Pflicht beim Order-Open:** jede Live-Order MUSS mit Server-Side-SL platziert werden.

- **Binance:** OCO (One-Cancels-the-Other) Order — Entry + SL + (optional) TP in einer atomaren Order-Group.
- **Bybit:** conditional-Order mit `triggerPrice` für SL.

```python
async def place_live_order(env: OrderEnvelope) -> OrderResult:
    if env.stop_loss is None:
        raise LiveOrderRejected("server_side_sl_required")
    # Atomic: entry + SL go to exchange together.
    result = await exchange.place_oco(
        symbol=env.symbol, side=env.side, qty=env.quantity,
        entry_price=env.limit_price, stop_loss=env.stop_loss,
    )
    if not result.sl_order_id:
        # Best-effort: cancel main order if SL didn't go through.
        await exchange.cancel(result.main_order_id)
        raise LiveOrderRejected("sl_placement_failed")
    return result
```

**S-003-Fix:** Operator's "duschen während SL feuert"-Szenario ist abgedeckt, weil SL am Exchange selbst läuft. KAI-Pi kann sterben — Position schließt trotzdem automatisch.

### 5 Audit-Schema-Erweiterung (`artifacts/live_execution_audit.jsonl`)

Neuer JSONL-Stream parallel zu `paper_execution_audit.jsonl`. Schema-Versionierung: `schema_version: "live-v1"`.

Jede Live-Order-Event:
```json
{
  "schema_version": "live-v1",
  "event_type": "live_order_placed",
  "timestamp_utc": "2026-05-09T20:30:15Z",
  "order_id": "...",
  "symbol": "BTC/USDT",
  "side": "buy",
  "quantity": 0.001,
  "notional_usd": 80.0,
  "exchange": "binance",
  "hotp_counter_used": 42,
  "exchange_perms_verified_at": "2026-05-09T20:30:00Z",
  "server_sl_order_id": "...",
  "server_sl_price": 76000.0,
  "current_open_positions_at_send": 1,
  "live_caps_check": "passed",
  "risk_engine_check": "passed"
}
```

**Forensik-tauglich:** jede Live-Order-Spur enthält alle 4 Approval-Schichten (HOTP, Cap-Check, Risk, Exchange-Perm) + Server-SL-Beweis. Ein Audit-Replay zeigt "war diese Order legitim?" eindeutig.

## Operator-Workflow (Live-Trade-Day, Phase 0)

```
[09:00]  Operator öffnet Telegram → KAI-Bot
[09:00]  /live unlock 384921          # HOTP aus Authenticator-App
[09:00]  KAI-Bot: "Live-Mode aktiv. TTL 60 min. Cap $200/pos, max 2 offen."

[10:30]  Operator bekommt KAI-Signal-Vorschlag im Bot (Premium-Channel-Alert)
[10:30]  KAI-Bot: "Signal: BTCUSDT buy 0.001 @ 80100, SL 78500. Live? Antworte mit /trade BTCUSDT buy 0.001 <hotp>"
[10:30]  Operator: /trade BTCUSDT buy 0.001 384733
[10:30]  KAI-Bot:
            1. HOTP-verify ✓ (counter 43)
            2. Cap-check ✓ ($80 < $200, 0 < 2 open)
            3. Risk-engine ✓
            4. Exchange-perms ✓ (cached)
            5. Order placed: id=ord_xyz, server-SL=78500 ✓
         "Live-Order ✓ id=ord_xyz | SL=78500 server-side"

[10:32]  Markt fällt → SL feuert am Exchange → Position auto-closed.
[10:32]  KAI-Position-Monitor sieht close → Audit-Eintrag.
[10:32]  KAI-Bot: "Position closed @ 78500 (SL). PnL -$1.60."

[11:30]  60 min Idle → Auto-Lock. KAI-Bot: "Live-Mode locked (idle timeout). /live unlock zum Reaktivieren."
```

## Implementation-Reihenfolge

| # | Task | Aufwand | Blocker |
|---|---|---|---|
| 1 | `app/security/__init__.py` + `live_caps.py` Konstanten + `verify_live_order()` | 2h | — |
| 2 | `hotp_auth.py` HOTP-Verifier + Counter-Tracking + Seed-Setup-CLI | 4h | — |
| 3 | `exchange_perms.py` Boot-Verifier (Binance + Bybit) | 4h | API-Account-Setup-Runbook |
| 4 | `app/execution/exchanges/binance.py` OCO-Order-Wrapper | 3h | — |
| 5 | `app/execution/exchanges/bybit.py` conditional-Order-Wrapper | 3h | — |
| 6 | `app/messaging/telegram_bot.py` `/live` + `/trade` Commands | 3h | — |
| 7 | `app/execution/live_engine.py` (analog `paper_engine.py`, aber mit allen Gates) | 6h | 1-5 |
| 8 | `live_execution_audit.jsonl` Schema + Writer | 2h | 7 |
| 9 | Tests: HOTP-Replay, Cap-Bypass-Negativ, OCO-Atomar | 6h | 1-7 |
| 10 | Integration-Test gegen Binance-Testnet + Bybit-Testnet | 4h | 1-9 |
| 11 | Operator-Setup-Runbook (Sub-Account, API-Permissions, HOTP-Seed-Backup) | 2h | — |
| 12 | Tabletop: simulierter Compromise-Test | 2h | 1-11 |

**Total:** ~41h ≈ **5 Arbeitstage** Solo. Realistisch mit Test-Iterationen + Runbook-Polish: **5-7 Tage**.

## S-004-Recovery-Pfad (Pflicht-Eintrag)

**HOTP-Seed-Verlust (Phone-Defekt, Authenticator-App-Reset):**
1. Backup-QR aus Tresor → Authenticator-App neu install
2. Falls Backup nicht verfügbar: KAI-Pi-CLI `kai-live reset-hotp` (lokal auf Pi, braucht physischen SSH-Login + Operator-Confirmation) → neuer Seed, neuer QR-Print im Tresor

**Pi 5 stirbt:**
- Live-Positionen: bereits durch Server-SL gehedged. Worst-Case: SL feuert irgendwann zum Markt-Preis.
- Re-Aktivierung: Pi 4b (Cold-Standby seit Cutover) als Hot-Standby setup nach Live-Aktivierung. Operator-Runbook: `kai-live cutover-to-pi4b.md`.

**YubiKey-Verlust (wenn später Phase 1):**
- Phase 0 nutzt Authenticator-App auf 2 Geräten (Smartphone + Tablet). Verlust eines Geräts → Backup-Gerät reicht.
- Phase 1 mit YubiKey: Backup-YubiKey im Bankschließfach + Backup-Seed im Tresor.

## Migration-Test (Pflicht-Gate vor Live-Aktivierung)

Vor erstem echten Live-Trade: **Pi-Migration-Drill**.
- Phase-0-Komponenten 1-5 müssen in <1h auf einer alternativen Hardware (Pi 4b oder Cloud-VM) funktionieren.
- Testskript: `scripts/security/migration_drill.sh` — provisioniert frische Hardware, deployed Code, läuft Test-Suite, stoppt vor erstem echten Order.

Wenn Migration >1h dauert: **Architektur ist zu komplex**, Re-Design-Trigger.

## Cross-Refs

- SATOSHI-Voll-Stack-Spec: `live_trading_circuit_breaker_v1.md`
- Red-Team-Showstopper: `red_team_response_v1.md`
- Operator-Decisions: `decision_log_20260509.md`
- ApprovalState/DecisionExecutionState (existing): `app/execution/models.py:182-195`
- Paper-Engine-Enforcer (bleibt aktiv bis Phase 0 grün): `app/execution/paper_engine.py:73-78`
- RiskEngine (Layer-Eins-Approval): `app/risk/engine.py:109`

## Phase-1-Trigger (Eskalation-Bedingungen)

Phase 0 wird zu Phase 1 wenn **eines** dieser Kriterien erfüllt ist:
1. **Capital-Plan erhöht auf >$50k** (4% Worst-Case = $2k wird tolerabel-aber-spürbar)
2. **3-6 Monate Phase-0-Live-Run sauber** (kein Drift, kein Bypass-Versuch in Audit-Log)
3. **Drift-Daten zeigen** dass Phase-0-Schichten unzureichend sind (z.B. ungewöhnliche Order-Patterns ohne klaren Trigger)

Phase-1-Komponenten sind in `live_trading_circuit_breaker_v1.md` voll spezifiziert. Build-Aufwand realistisch 3-4 Wochen.
